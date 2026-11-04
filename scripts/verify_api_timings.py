"""Versioned functional/metric checks and four bounded startup timing bursts."""
import argparse
from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from api_load import parse_metrics
from api_timings import correlate, scrape_count
from download_esci import ROOT, verify_file
from embedding_checkpoint import atomic_json
from es_pilot import LocalElasticsearch
from install_k6 import install
from prepare_esci_splits import file_digest
from run_api_load import Sampler
from run_elasticsearch_pilot import backend, docker_observation
from verify_catalog_serving import restored_index
from verify_search_api import API, COMPOSE, JAR, compare, inputs, request

CONFIG = ROOT / 'config/api-timings-v1.json'
REFERENCE = ROOT / 'data/reference/search-api-0.1.0/commerce-search-api.jar'


@contextmanager
def process(jar, index, folder, observed, events=True):
    try:
        with socket.create_connection(('127.0.0.1', 18080), timeout=.2): raise RuntimeError('API port is occupied')
    except (ConnectionRefusedError, TimeoutError): pass
    args = ['java', '-Xms64m', '-Xmx256m', '-jar', str(jar), '--server.address=127.0.0.1', '--server.port=18080',
            f'--commerce.index={index}', '--commerce.backend-url=http://127.0.0.1:19200']
    if observed: args.append(f'--commerce.telemetry.request-events.enabled={str(events).lower()}')
    with (folder / 'api.log').open('w') as log:
        child = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            tick = time.monotonic()
            while True:
                if child.poll() is not None: raise RuntimeError('Task-owned API exited')
                try:
                    if request('/health/ready')[0] == 200: break
                except OSError: pass
                if time.monotonic() - tick > 30: raise TimeoutError('API readiness deadline exceeded')
                time.sleep(.1)
            yield child
        finally:
            child.terminate()
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(); raise RuntimeError('Graceful API stop exceeded deadline')


def scrape(path):
    import urllib.request
    with urllib.request.urlopen(API + '/actuator/prometheus', timeout=5) as response: value = response.read().decode()
    path.write_text(value); return value


def burst(binary, folder, workload_path, jar, index, observed, client, rate=100, seconds=30, vus=64):
    folder.mkdir()
    with process(jar, index, folder, observed) as child:
        if observed: scrape(folder / 'before.prom')
        sampler = Sampler(client, child); sampler.phase = 'startup-diagnostic'
        try:
            with sampler:
                env = dict(os.environ, WORKLOAD=str(workload_path), RATE=str(rate), DURATION=str(seconds), VUS=str(vus),
                           SUMMARY=str(folder / 'k6-summary.json'), BASE_URL=API)
                with (folder / 'client.log').open('w') as log:
                    generator = subprocess.Popen([str(binary), 'run', '--no-usage-report', '--log-format', 'json', '--out', f'json={folder / "raw.ndjson"}',
                                                  str(ROOT / 'load/api-timings.js')], env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    sampler.generator = generator
                    try:
                        deadline = time.monotonic() + seconds + 35
                        while generator.poll() is None:
                            sampler.check()
                            if time.monotonic() > deadline: raise TimeoutError('Diagnostic generator deadline exceeded')
                            time.sleep(.25)
                        if generator.returncode not in (0, 99): raise RuntimeError('Diagnostic k6 execution failed')
                    finally:
                        if generator.poll() is None: generator.terminate(); generator.wait(timeout=10)
                        sampler.generator = None
                measured = parse_metrics(folder / 'raw.ndjson', rate, seconds)
                if observed:
                    tick = time.monotonic()
                    while scrape_count(scrape(folder / 'after.prom')) < measured['started']:
                        if time.monotonic() - tick > 5: raise TimeoutError('Request meters did not drain')
                        time.sleep(.1)
                sampler.check()
            sampler.check()
        finally: atomic_json(folder / 'resources.json', {'samples': sampler.rows, 'errors': sampler.errors})
    if measured['incorrect_http200']: raise ValueError('Incorrect result; stop diagnostic')
    paired = correlate(folder / 'raw.ndjson', folder / 'client.log', folder / 'api.log', folder / 'before.prom', folder / 'after.prom') if observed else None
    result = {'version': '0.2.0' if observed else '0.1.0', 'metrics': measured, 'correlation': paired,
              'raw_sha256': file_digest(folder / 'raw.ndjson'), 'server_log_sha256': file_digest(folder / 'api.log'),
              'client_log_sha256': file_digest(folder / 'client.log'), 'resources_sha256': file_digest(folder / 'resources.json')}
    atomic_json(folder / 'result.json', result)
    print(json.dumps({'burst': folder.name, 'version': result['version'], 'started': measured['started'], 'dropped': measured['dropped'],
                      'client_p99_ms': measured['all_attempt_wall_ms']['p99_ms'], 'paired': paired['paired_requests'] if paired else None}), flush=True)
    return result


def real_inputs(directory, policy):
    if file_digest(REFERENCE) != policy['reference_jar_sha256']: raise ValueError('Preserved reference JAR changed')
    baseline = json.loads((ROOT / 'reports/api-load-v1.json').read_text())
    source = ROOT / 'data/processed/api-load-v1/workload.json'
    if file_digest(source) != policy['workload_sha256'] or file_digest(source) != baseline['workload_sha256']: raise ValueError('Frozen workload changed')
    selected = json.loads(source.read_text()); atomic_json(directory / 'workload.json', selected)
    full = json.loads((ROOT / 'reports/catalog-serving-v1.json').read_text())
    native = ROOT / 'data/processed/catalog-serving-v1/elasticsearch.tsv'
    verify_file(native, next(a for a in full['artifacts'] if a['name'] == 'elasticsearch.tsv'))
    expected = {(q['locale'], q['query_id']): [] for q in selected}
    with native.open() as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            key = (row['product_locale'], int(row['query_id']))
            if key in expected: expected[key].append((row['product_id'], float(row['score'])))
    # Re-serializing through the same atomic JSON helper must preserve the actual frozen workload bytes.
    if file_digest(directory / 'workload.json') != policy['workload_sha256']: raise ValueError('Workload serialization drift')
    return full, selected, expected


def run(fixture, directory):
    policy = json.loads(CONFIG.read_text()); client = LocalElasticsearch(); binary = install()
    pilot = json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text())
    backend(client, pilot)
    if fixture:
        index, selected, expected, _ = inputs(True, client, pilot)
        selected = [dict(q, expected=expected[(q['locale'], q['query_id'])][:10]) for q in selected]
        atomic_json(directory / 'workload.json', selected)
        burst(binary, directory / 'observed', directory / 'workload.json', JAR, index, True, client, 5, 2, 2)
        return
    full, selected, expected = real_inputs(directory, policy)
    docker_observation(); results = []
    try:
        for i, version in enumerate(policy['order']):
            subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)
            subprocess.run(COMPOSE + ['start', 'elasticsearch'], check=True, capture_output=True)
            backend(client, pilot)
            state = restored_index(client, policy['index'], full['config']['catalog_by_locale'])
            if state['uuid'] != full['index']['uuid']: raise ValueError('Index UUID changed')
            results.append(burst(binary, directory / f'{i}-{version}', directory / 'workload.json', JAR if version == 'instrumented' else REFERENCE,
                                 policy['index'], version == 'instrumented', client))
            atomic_json(directory / 'completed-bursts.json', results)
        functional = directory / 'functional'; functional.mkdir()
        with process(JAR, policy['index'], functional, True, events=False):
            checked = compare(selected, expected, functional, 'results')
        if checked['mismatch_cases']: raise ValueError('Functional responses changed under0.2.0')
        atomic_json(functional / 'checks.json', checked)
        sources = [Path('config/api-timings-v1.json'), Path('docs/decisions/0015-api-request-timings.md'), Path('load/api-timings.js'),
                   Path('scripts/api_timings.py'), Path('scripts/verify_api_timings.py'), Path('tests/test_api_timings.py'), Path('services/search-api/pom.xml'), Path('.github/workflows/ci.yml')]
        sources += sorted(p.relative_to(ROOT) for p in (ROOT / 'services/search-api/src').rglob('*') if p.is_file())
        report = {'config': policy, 'partition': 'frozen validation-query cohort', 'official_test_read': False,
                  'bursts': results, 'functional': checked, 'workload_sha256': file_digest(directory / 'workload.json'),
                  'reference_jar_sha256': file_digest(REFERENCE), 'instrumented_jar_sha256': file_digest(JAR), 'index_uuid': full['index']['uuid'],
                  'provenance': {'previous_load_report_sha256': file_digest(ROOT / 'reports/api-load-v1.json'),
                                 'source_sha256': {str(p): file_digest(ROOT / p) for p in sources}},
                  'project_services_stopped_volume_preserved': True,
                  'limits': 'Four fixed diagnostic bursts with backend restarts and retained OS caches. Instrumentation/logging changes runtime work. Per-request timings bound observed layers; residuals are not isolated Tomcat queueing, and these observations do not establish causal overhead or the historical spike root cause. No new load capacity/SLO claim.'}
        atomic_json(ROOT / 'reports/api-timings-v1.json', report)
    finally: subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--fixture', action='store_true'); args = parser.parse_args()
    if args.fixture:
        with tempfile.TemporaryDirectory(prefix='timing-fixture-') as tmp: run(True, Path(tmp))
    else:
        directory = ROOT / 'data/processed/api-timings-v1'
        if directory.exists() or (ROOT / 'reports/api-timings-v1.json').exists(): raise FileExistsError('Preserve earlier diagnostic evidence')
        directory.mkdir(); run(False, directory)


if __name__ == '__main__': main()
