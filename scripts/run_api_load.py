"""Bounded local open-arrival load campaign on the frozen full-catalog API."""
import base64
from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import threading
import time

from api_load import parse_metrics, select_workload
from download_esci import ROOT, verify_file
from embedding_checkpoint import atomic_json
from es_pilot import LocalElasticsearch
from install_k6 import install
from prepare_esci_splits import file_digest
from run_elasticsearch_pilot import backend, docker_observation
from verify_catalog_serving import restored_index
from verify_search_api import COMPOSE, JAR, request

CONFIG = ROOT / 'config/api-load-v1.json'


@contextmanager
def api_process(index, folder):
    try:
        with socket.create_connection(('127.0.0.1', 18080), timeout=.2): raise RuntimeError('API port is occupied; do not stop an unrelated process')
    except (ConnectionRefusedError, TimeoutError): pass
    with (folder / 'api.log').open('w') as log:
        child = subprocess.Popen(['java', '-Xms64m', '-Xmx256m', '-jar', str(JAR), '--server.address=127.0.0.1', '--server.port=18080',
                                  '--commerce.backend-url=http://127.0.0.1:19200', f'--commerce.index={index}'], stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        try:
            tick = time.monotonic()
            while True:
                if child.poll() is not None: raise RuntimeError('Task-owned API exited; inspect its log')
                try:
                    if request('/health/ready')[0] == 200: break
                except OSError: pass
                if time.monotonic() - tick > 30: raise TimeoutError('API readiness startup deadline exceeded')
                time.sleep(.1)
            yield child
        finally:
            child.terminate()
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(); raise RuntimeError('Task-owned API exceeded graceful shutdown deadline')


class Sampler:
    def __init__(self, client, api):
        self.client, self.api, self.generator = client, api, None
        self.phase, self.rows, self.errors = 'starting', [], []
        self.stop = threading.Event(); self.thread = threading.Thread(target=self.loop, daemon=True)

    def capture(self):
        if self.api.poll() is not None: raise RuntimeError('API process died during campaign')
        identities = [self.api.pid] + ([self.generator.pid] if self.generator is not None and self.generator.poll() is None else [])
        result = subprocess.run(['ps', '-p', ','.join(map(str, identities)), '-o', 'pid=,pcpu=,rss='], capture_output=True, text=True, check=True)
        processes = {int(line.split()[0]): {'ps_cpu_percent_lifetime_average': float(line.split()[1]), 'rss_bytes': int(line.split()[2]) * 1024}
                     for line in result.stdout.splitlines()}
        path = '/_nodes/stats/process,jvm,thread_pool?filter_path=nodes.*.process.cpu,nodes.*.jvm.mem.heap_used_in_bytes,nodes.*.thread_pool.search,nodes.*.thread_pool.search_coordination'
        node = next(iter(self.client.request('GET', path)['nodes'].values()))
        self.rows.append({'monotonic_seconds': time.monotonic(), 'phase': self.phase, 'api': processes.get(self.api.pid),
                          'generator': processes.get(self.generator.pid) if self.generator else None, 'node': node, 'container': docker_observation()})

    def loop(self):
        while not self.stop.wait(1):
            try: self.capture()
            except Exception as error: self.errors.append(str(error)); return

    def check(self):
        if self.errors: raise RuntimeError(self.errors[0])

    def __enter__(self): self.capture(); self.thread.start(); return self
    def __exit__(self, *_): self.stop.set(); self.thread.join(timeout=40)


def workload(policy, directory):
    full = json.loads((ROOT / 'reports/catalog-serving-v1.json').read_text())
    api = json.loads((ROOT / 'reports/search-api-v1.json').read_text())
    for mapping in (full['provenance']['source_sha256'], api['source_sha256']):
        for name, sha in mapping.items():
            if name != '.github/workflows/ci.yml' and file_digest(ROOT / name) != sha: raise ValueError(f'Frozen runtime/experiment source changed: {name}')
    if file_digest(JAR) != api['jar_sha256']: raise ValueError('API JAR changed')
    baseline = json.loads((ROOT / 'reports/catalog-bm25-title-v1.json').read_text())
    queries_path = ROOT / 'data/processed/catalog-bm25-title-v1/queries.tsv'
    verify_file(queries_path, next(a for a in baseline['artifacts'] if a['name'] == 'queries.tsv'))
    native = ROOT / 'data/processed/catalog-serving-v1/elasticsearch.tsv'
    verify_file(native, next(a for a in full['artifacts'] if a['name'] == 'elasticsearch.tsv'))
    queries = []
    with queries_path.open() as handle:
        for locale, query, text in csv.reader(handle, delimiter='\t'):
            queries.append({'locale': locale, 'query_id': int(query), 'query': base64.b64decode(text, validate=True).decode()})
    expected = {(q['locale'], q['query_id']): [] for q in queries}
    with native.open() as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            if int(row['rank']) <= 10: expected[(row['product_locale'], int(row['query_id']))].append((row['product_id'], float(row['score'])))
    selected = select_workload(queries, expected, policy['sample_seed'], policy['queries_per_locale'])
    atomic_json(directory / 'workload.json', selected)
    return full, selected


def point(binary, folder, selected_path, rate, seconds, sampler, policy):
    folder.mkdir()
    env = dict(os.environ, WORKLOAD=str(selected_path), RATE=str(rate), DURATION=str(seconds), VUS=str(policy['maximum_vus']),
               SUMMARY=str(folder / 'k6-summary.json'), BASE_URL='http://127.0.0.1:18080')
    raw = folder / 'raw.ndjson'
    with (folder / 'k6.log').open('w') as log:
        child = subprocess.Popen([str(binary), 'run', '--no-usage-report', '--out', f'json={raw}', str(ROOT / 'load/api-load.js')],
                                 env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        sampler.generator = child
        try:
            deadline = time.monotonic() + seconds + 35
            while child.poll() is None:
                sampler.check()
                if time.monotonic() > deadline: raise TimeoutError('Load generator exceeded bounded point duration')
                time.sleep(.25)
            if child.returncode not in (0, 99): raise RuntimeError(f'k6 failed with exit {child.returncode}; inspect k6.log')
        finally:
            if child.poll() is None: child.terminate(); child.wait(timeout=10)
            sampler.generator = None
    result = parse_metrics(raw, rate, seconds)
    result['raw_sha256'] = file_digest(raw)
    result['k6_exit_code'] = child.returncode
    atomic_json(folder / 'result.json', result)
    return result


def campaign(binary, folder, full, selected, rates, policy, client):
    folder.mkdir()
    points = []
    with api_process(policy['index'], folder) as api:
        sampler = Sampler(client, api)
        try:
            with sampler:
                sampler.phase = 'warmup'
                warmup = point(binary, folder / 'warmup', folder.parent / 'workload.json', policy['warmup_rate'], policy['warmup_seconds'], sampler, policy)
                if warmup['incorrect_http200']: raise ValueError('Incorrect successful result during warm-up')
                for rate in rates:
                    sampler.phase = 'cooldown'
                    time.sleep(policy['cooldown_seconds'])
                    sampler.phase = f'rate-{rate}'
                    result = point(binary, folder / f'rate-{rate}', folder.parent / 'workload.json', rate, policy['duration_seconds'], sampler, policy)
                    points.append(result)
                    print(json.dumps({'campaign': folder.name, 'rate': rate, 'correct_rps': result['correct_completion_rps_per_scheduled_window'],
                                      'loss_fraction': result['loss_fraction'], 'dropped': result['dropped'],
                                      'p99_all_ms': result['all_attempt_wall_ms']['p99_ms'], 'qualified': result['qualified']}), flush=True)
                    if result['incorrect_http200']: raise ValueError('Incorrect successful result; stop further points')
                sampler.phase = 'recovery'
                if request('/health/ready')[0] != 200: raise ValueError('API not ready after load')
                for q in selected:
                    status, body = request('/v1/search', {'query': q['query'], 'locale': q['locale'], 'limit': 10})
                    if status != 200 or [(h['productId'], h['score']) for h in body['hits']] != [tuple(h) for h in q['expected']]:
                        raise ValueError('Post-load recovery output differs')
                sampler.check()
        finally:
            atomic_json(folder / 'resources.json', {'samples': sampler.rows, 'errors': sampler.errors})
    result = {'rate_order': rates, 'warmup': warmup, 'points': points, 'post_load_recovery_queries': len(selected),
              'resource_samples': len(sampler.rows), 'resources_sha256': file_digest(folder / 'resources.json')}
    atomic_json(folder / 'campaign.json', result)
    return result


def main():
    directory = ROOT / 'data/processed/api-load-v1'
    report_path = ROOT / 'reports/api-load-v1.json'
    if directory.exists() or report_path.exists(): raise FileExistsError('Preserve previous campaign evidence')
    policy = json.loads(CONFIG.read_text()); directory.mkdir(parents=True)
    binary = install()
    version = subprocess.run([str(binary), 'version'], capture_output=True, text=True, check=True).stdout.strip()
    if not version.startswith('k6 v' + policy['k6_version']): raise ValueError('Unexpected k6 version')
    full, selected = workload(policy, directory)
    subprocess.run(COMPOSE + ['start', 'elasticsearch'], check=True, capture_output=True)
    client = LocalElasticsearch()
    campaigns = []
    try:
        server = backend(client, json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text()))
        container = docker_observation()
        state = restored_index(client, policy['index'], full['config']['catalog_by_locale'])
        if state['uuid'] != full['index']['uuid']: raise ValueError('Full-catalog index identity changed')
        for name, rates in [('ascending', policy['rates']), ('descending', list(reversed(policy['rates'])))]:
            campaigns.append(campaign(binary, directory / name, full, selected, rates, policy, client))
            atomic_json(directory / 'completed-campaigns.json', campaigns)
        eligible = set(p['offered_rps'] for p in campaigns[0]['points'] if p['qualified']) & set(p['offered_rps'] for p in campaigns[1]['points'] if p['qualified'])
        source_names = ['config/api-load-v1.json', 'docs/decisions/0014-api-load-baseline.md', 'load/api-load.js',
                        'scripts/api_load.py', 'scripts/install_k6.py', 'scripts/k6_fixture.py', 'scripts/run_api_load.py', 'tests/test_api_load.py', '.github/workflows/ci.yml']
        report = {'config': policy, 'partition': 'validation-query sample', 'official_test_read': False, 'server': server,
                  'index_uuid': state['uuid'], 'products': sum(state['counts'].values()), 'workload_queries': len(selected),
                  'workload_sha256': file_digest(directory / 'workload.json'), 'campaigns': campaigns,
                  'highest_qualifying_tested_rate_in_both_runs': max(eligible) if eligible else None,
                  'k6_version': version, 'k6_binary_sha256': file_digest(binary), 'initial_container': container,
                  'host': {'platform': platform.platform(), 'architecture': platform.machine(), 'logical_cpus': os.cpu_count()},
                  'provenance': {'api_jar_sha256': file_digest(JAR), 'full_catalog_report_sha256': file_digest(ROOT / 'reports/catalog-serving-v1.json'),
                                 'source_sha256': {name: file_digest(ROOT / name) for name in source_names}},
                  'project_services_stopped_volume_preserved': True,
                  'limits': 'Short local warm-cache open-arrival benchmark on a balanced fixed 300-query workload; shared host, bounded64VUs, no direct Tomcat queue instrumentation. Report errors/drops and all-attempt latency; no production SLO, cold-cache, exact saturation ceiling or long-duration capacity claim.'}
        atomic_json(report_path, report)
        print(json.dumps({'highest_qualifying_tested_rate_in_both_runs': report['highest_qualifying_tested_rate_in_both_runs'], 'workload_sha256': report['workload_sha256']}, indent=2))
    finally:
        subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)


if __name__ == '__main__': main()
