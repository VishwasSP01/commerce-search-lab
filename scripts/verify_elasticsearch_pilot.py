"""Verify two fresh pilot runs, then gracefully restart only their owned backend."""

import base64
import csv
import json
from pathlib import Path
import subprocess
import tempfile
import time

from download_esci import ROOT
from embedding_checkpoint import atomic_json
from es_pilot import LocalElasticsearch, owned_index
from prepare_esci_splits import file_digest
from run_elasticsearch_pilot import backend, docker_observation, retrieve, write_results


def main():
    first_path = ROOT / 'reports/elasticsearch-pilot-v1.json'
    repeat_path = ROOT / 'data/processed/elasticsearch-pilot-v1-repeat-report.json'
    output = ROOT / 'reports/elasticsearch-pilot-v1-reproduction.json'
    if output.exists():
        raise FileExistsError('Verification evidence already exists')
    first, repeat = (json.loads(p.read_text()) for p in (first_path, repeat_path))
    directories = [ROOT / 'data/processed/elasticsearch-pilot-v1', ROOT / 'data/processed/elasticsearch-pilot-v1-repeat']
    equal_fields = ['config', 'official_test_read', 'partition', 'summary', 'analysis_mismatches',
                    'retrieval_mismatches', 'provenance', 'artifacts', 'limits']
    if not all(first[key] == repeat[key] for key in equal_fields):
        raise ValueError('Fresh-run evidence differs')
    if not first['summary']['passed_bounded_parity'] or first['index_name'] == repeat['index_name']:
        raise ValueError('Require passing parity on distinct fresh indexes')
    for report, directory in zip((first, repeat), directories, strict=True):
        for artifact in report['artifacts']:
            path = directory / artifact['name']
            if path.stat().st_size != artifact['bytes'] or file_digest(path) != artifact['sha256']:
                raise ValueError('Changed generated artifact')
        for name, sha in {**report['provenance']['source_sha256'], **report['provenance']['frozen_source_sha256']}.items():
            if file_digest(ROOT / name) != sha:
                raise ValueError(f'Changed bound source: {name}')

    compose = ['docker', 'compose', '-f', str(ROOT / 'infra/elasticsearch/compose.yml')]
    client = LocalElasticsearch()
    backend(client, first['config'])
    before = docker_observation()  # Check ownership, limits and port before any stop.
    names = [owned_index(r['index_name']) for r in (first, repeat)]
    uuids = {name: client.request('GET', f'/{name}/_settings')[name]['settings']['index']['uuid'] for name in names}
    lifecycle = {}
    try:
        tick = time.perf_counter()
        subprocess.run(compose + ['stop', 'elasticsearch'], check=True, capture_output=True)
        lifecycle['graceful_stop_seconds'] = time.perf_counter() - tick
        tick = time.perf_counter()
        subprocess.run(compose + ['start', 'elasticsearch'], check=True, capture_output=True)
        server = backend(client, first['config'])
        health = client.request('GET', '/_cluster/health?wait_for_status=green&timeout=30s')
        if health.get('timed_out') or health['status'] != 'green':
            raise ValueError('Backend is not green after restart')
        lifecycle['restart_to_ready_green_seconds'] = time.perf_counter() - tick
        for report, directory in zip((first, repeat), directories, strict=True):
            name = report['index_name']
            if (client.request('GET', f'/{name}/_settings')[name]['settings']['index']['uuid'] != uuids[name]
                    or client.request('GET', f'/{name}/_count')['count'] != report['summary']['products']):
                raise ValueError('Index identity/count did not survive graceful restart')
            with (directory / 'products.tsv').open() as handle:
                catalog = {(row[0], row[1]) for row in csv.reader(handle, delimiter='\t')}
            with (directory / 'queries.tsv').open() as handle:
                queries = [{'locale': row[0], 'query_id': int(row[1]), 'query': base64.b64decode(row[2], validate=True).decode()}
                           for row in csv.reader(handle, delimiter='\t')]
            with tempfile.TemporaryDirectory(prefix='es-restart-') as tmp:
                path = Path(tmp) / 'elasticsearch.tsv'
                write_results(path, retrieve(client, name, queries, catalog))
                expected = next(a['sha256'] for a in report['artifacts'] if a['name'] == path.name)
                if file_digest(path) != expected:
                    raise ValueError('Ranked results changed after restart')
        lifecycle['both_index_uuids_counts_and_all_300_ranked_results_preserved'] = True
        lifecycle['backend_after_restart'] = server
        lifecycle['container_after_restart'] = docker_observation()
    finally:
        subprocess.run(compose + ['stop', 'elasticsearch'], check=True, capture_output=True)
    running = subprocess.run(compose + ['ps', '--status', 'running', '-q', 'elasticsearch'],
                             check=True, capture_output=True, text=True).stdout.strip()
    if running:
        raise ValueError('Pilot service still running')
    lifecycle['pilot_service_stopped_at_end_volume_preserved'] = True
    result = {'first_report_sha256': file_digest(first_path), 'repeat_report_sha256': file_digest(repeat_path),
              'verification_script_sha256': file_digest(Path(__file__)),
              'equal_report_fields': equal_fields, 'all_nine_artifact_hashes_equal': True,
              'summary': first['summary'], 'artifacts': first['artifacts'],
              'first_execution': first['execution'], 'repeat_execution': repeat['execution'],
              'container_before_restart': before, 'lifecycle': lifecycle,
              'limits': 'Two fresh bounded indexes, then graceful same-volume restart; no crash, event replay, update freshness, API capacity or full-catalog serving claim. Only the task-owned service is stopped; Docker Desktop and unrelated services are left running.'}
    atomic_json(output, result)
    print(json.dumps({'all_nine_artifact_hashes_equal': True, 'passed_bounded_parity': True,
                      'restart_preserves_both_indexes_and_results': True, 'pilot_stopped': True}, indent=2))


if __name__ == '__main__':
    main()
