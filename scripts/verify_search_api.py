"""Check the packaged Java HTTP API against saved sample evidence or CI fixtures."""

import argparse
import base64
from contextlib import contextmanager
import csv
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from download_esci import ROOT
from embedding_checkpoint import atomic_json
from es_pilot import LocalElasticsearch, owned_index
from prepare_esci_splits import file_digest
from run_elasticsearch_pilot import backend, docker_observation, retrieve, write_results

JAR = ROOT / 'services/search-api/target/commerce-search-api.jar'
API = 'http://127.0.0.1:18080'
COMPOSE = ['docker', 'compose', '-f', str(ROOT / 'infra/elasticsearch/compose.yml')]


def request(path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as response:
        return response.code, json.load(response)


@contextmanager
def process(index, directory, name):
    try:
        with socket.create_connection(('127.0.0.1', 18080), timeout=.2):
            raise RuntimeError('Port 18080 is already in use; do not stop an unrelated process')
    except (ConnectionRefusedError, TimeoutError):
        pass
    with (directory / f'{name}.log').open('w') as log:
        child = subprocess.Popen(['java', '-Xms64m', '-Xmx256m', '-jar', str(JAR), '--server.address=127.0.0.1', '--server.port=18080',
                                  '--commerce.backend-url=http://127.0.0.1:19200', f'--commerce.index={owned_index(index)}'],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            started = time.monotonic()
            while True:
                if child.poll() is not None:
                    raise RuntimeError(f'Task-owned API exited; inspect {name}.log')
                try:
                    if request('/health/live') == (200, {'status': 'UP'}):
                        break
                except OSError:
                    pass
                if time.monotonic() - started > 30:
                    raise TimeoutError('API liveness startup deadline exceeded')
                time.sleep(.1)
            yield
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
                raise RuntimeError('Task-owned API exceeded graceful shutdown deadline')


def compare(queries, expected, directory, name):
    observed = {}
    mismatches = []
    calls = 0
    for query in queries:
        key = (query['locale'], query['query_id'])
        for limit in (100, None, 1):
            body = {'query': query['query'], 'locale': query['locale']}
            if limit is not None:
                body['limit'] = limit
            status, result = request('/v1/search', body)
            size = 10 if limit is None else limit
            calls += 1
            if (status != 200 or set(result) != {'locale', 'limit', 'returned', 'retrieval', 'hits'}
                    or result['locale'] != query['locale'] or result['limit'] != size or result['retrieval'] != 'bm25'
                    or result['returned'] != len(result['hits'])):
                raise ValueError(f'API response contract mismatch at {key}, limit={size}')
            actual = []
            for hit in result['hits']:
                if set(hit) != {'productId', 'score'} or not isinstance(hit['productId'], str):
                    raise ValueError('Invalid hit identity/shape')
                actual.append((hit['productId'], hit['score']))
            if actual != expected[key][:size]:
                mismatches.append({'locale': key[0], 'query_id': key[1], 'limit': size,
                                   'expected': expected[key][:size], 'actual': actual})
            if limit == 100:
                observed[key] = actual
    write_results(directory / f'{name}.tsv', observed)
    return {'http_search_requests': calls, 'mismatch_cases': mismatches,
            'ranked_sha256': file_digest(directory / f'{name}.tsv')}


def inputs(fixture, client, policy):
    if fixture:
        index = 'csl-fixture-parity-v1'
        queries = [{'locale': loc, 'query_id': i, 'query': query} for loc, i, query in [
            ('us', 1, 'shoes'), ('us', 2, 'the and of'), ('us', 3, 'red + shoes (nike):'), ('us', 4, 'shoes shoes'),
            ('es', 1, 'zapato'), ('es', 2, 'el y la'), ('es', 3, 'camión'), ('jp', 1, '関西国際空港'), ('jp', 2, 'ＡＢＣ')]]
        catalog = {(loc, str(i).zfill(3)) for loc, count in [('us', 4), ('es', 3), ('jp', 3)] for i in range(1, count + 1)}
        expected = retrieve(client, index, queries, catalog)
        return index, queries, expected, {'synthetic_fixture_only': True}
    path = ROOT / 'reports/elasticsearch-pilot-v1.json'
    report = json.loads(path.read_text())
    directory = ROOT / 'data/processed/elasticsearch-pilot-v1'
    for artifact in report['artifacts']:
        if file_digest(directory / artifact['name']) != artifact['sha256']:
            raise ValueError('Frozen pilot artifact changed')
    with (directory / 'queries.tsv').open() as handle:
        queries = [{'locale': row[0], 'query_id': int(row[1]), 'query': base64.b64decode(row[2], validate=True).decode()}
                   for row in csv.reader(handle, delimiter='\t')]
    expected = {(q['locale'], q['query_id']): [] for q in queries}
    with (directory / 'elasticsearch.tsv').open() as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            expected[(row['product_locale'], int(row['query_id']))].append((row['product_id'], float(row['score'])))
    index = owned_index(report['index_name'])
    if client.request('GET', f'/{index}/_count')['count'] != 30000 or len(queries) != 300:
        raise ValueError('Pilot index/query count differs')
    return index, queries, expected, {'pilot_report_sha256': file_digest(path), 'pilot_artifacts': report['artifacts']}


def run(fixture, directory):
    if not JAR.exists():
        raise FileNotFoundError('Package the API first')
    client = LocalElasticsearch()
    policy = json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text())
    server = backend(client, policy)
    container = None if fixture else docker_observation()
    index, queries, expected, provenance = inputs(fixture, client, policy)
    runs = []
    failure_checks = {}
    try:
        for name in ('first', 'restarted'):
            with process(index, directory, name):
                if request('/health/ready') != (200, {'status': 'UP'}):
                    raise ValueError('API index readiness failed')
                runs.append(compare(queries, expected, directory, name))
                if not fixture and name == 'first':
                    subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)
                    status, error = request('/v1/search', {'query': 'shoes', 'locale': 'us'})
                    if (status != 503 or error['code'] != 'BACKEND_UNAVAILABLE'
                            or request('/health/ready')[0] != 503 or request('/health/live')[0] != 200):
                        raise ValueError('Backend outage was not represented correctly')
                    subprocess.run(COMPOSE + ['start', 'elasticsearch'], check=True, capture_output=True)
                    backend(client, policy)
                    health = client.request('GET', f'/_cluster/health/{index}?wait_for_status=green&timeout=30s')
                    if health.get('timed_out') or health['status'] != 'green' or request('/health/ready')[0] != 200:
                        raise ValueError('Backend readiness did not recover')
                    failure_checks = {'backend_stopped_search_503': True, 'backend_stopped_ready_503': True,
                                      'backend_stopped_live_200': True, 'backend_restart_ready_200': True}
    finally:
        if not fixture:
            subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)
    atomic_json(directory / 'checks.json', {'runs': runs, 'failure_checks': failure_checks})
    if any(run['mismatch_cases'] for run in runs) or runs[0]['ranked_sha256'] != runs[1]['ranked_sha256']:
        raise ValueError('Ranked API results differ from the reference or across Java restart')
    if not fixture:
        frozen = next(a['sha256'] for a in provenance['pilot_artifacts'] if a['name'] == 'elasticsearch.tsv')
        if runs[0]['ranked_sha256'] != frozen:
            raise ValueError('API top-100 serialization differs from the frozen native ES results')
    sources = [Path('scripts/verify_search_api.py'), Path('docs/decisions/0012-java-search-api.md'),
               Path('.github/workflows/ci.yml'), Path('services/search-api/pom.xml')]
    sources += sorted(p.relative_to(ROOT) for p in (ROOT / 'services/search-api/src').rglob('*') if p.is_file())
    report = {'partition': 'synthetic' if fixture else 'validation', 'official_test_read': False,
              'products': 10 if fixture else 30000, 'queries': len(queries), 'index': index,
              'limits_checked': [100, 'default 10', 1], 'runs': runs, 'failure_checks': failure_checks,
              'backend': server, 'container': container, 'jar_sha256': file_digest(JAR),
              'source_sha256': {str(path): file_digest(ROOT / path) for path in sources}, 'provenance': provenance,
              'java_processes_gracefully_stopped': True, 'pilot_service_stopped': not fixture,
              'passed': True,
              'limits': 'Sequential functional HTTP requests on the bounded lexical corpus, including default/limit prefixes and fresh Java-process restart. No load/percentile/QPS, full-catalog serving, circuit-breaker, hybrid, update recovery or final-test claim. Backend scores remain native and equal to saved sample scores.'}
    if not fixture:
        atomic_json(ROOT / 'reports/search-api-v1.json', report)
    print(json.dumps({key: report[key] for key in ('products', 'queries', 'limits_checked', 'runs', 'failure_checks', 'passed')}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', action='store_true', help='Use existing tiny CI fixture index; no real-data report')
    args = parser.parse_args()
    if args.fixture:
        with tempfile.TemporaryDirectory(prefix='search-api-fixture-') as tmp:
            run(True, Path(tmp))
    else:
        directory = ROOT / 'data/processed/search-api-v1'
        if directory.exists() or (ROOT / 'reports/search-api-v1.json').exists():
            raise FileExistsError('Use a clean API evidence location; preserve previous results')
        directory.mkdir()
        run(False, directory)


if __name__ == '__main__':
    main()
