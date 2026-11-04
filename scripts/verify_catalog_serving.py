"""Compare fresh catalog builds and verify their same-volume API persistence."""
import base64
import csv
import json
from pathlib import Path
import subprocess

from catalog_serving import verify_index
from download_esci import ROOT
from embedding_checkpoint import atomic_json
from es_pilot import LocalElasticsearch
from prepare_esci_splits import file_digest
from run_elasticsearch_pilot import backend, docker_observation, write_results
from verify_search_api import COMPOSE, process, request


def restored_index(client, index, counts):
    health = client.request('GET', f'/_cluster/health/{index}?wait_for_status=green&timeout=30s')
    if health.get('timed_out') or health.get('status') != 'green' or health.get('active_primary_shards') != 1:
        raise ValueError('Restored primary has not reached green index health')
    return verify_index(client, index, counts)


def main():
    paths = [ROOT / 'reports/catalog-serving-v1.json', ROOT / 'data/processed/catalog-serving-v1-repeat-report.json']
    directories = [ROOT / 'data/processed/catalog-serving-v1', ROOT / 'data/processed/catalog-serving-v1-repeat']
    output = ROOT / 'reports/catalog-serving-v1-reproduction.json'
    directory = ROOT / 'data/processed/catalog-serving-v1-persistence'
    if output.exists() or directory.exists(): raise FileExistsError('Preserve existing verification evidence')
    reports = [json.loads(p.read_text()) for p in paths]
    equal_fields = ['config', 'partition', 'official_test_read', 'summary', 'metrics', 'server', 'artifacts', 'provenance', 'limits']
    if any(reports[0][key] != reports[1][key] for key in equal_fields): raise ValueError('Fresh build evidence differs')
    if reports[0]['index']['uuid'] == reports[1]['index']['uuid']: raise ValueError('Repeat must use a fresh index')
    for report, folder in zip(reports, directories, strict=True):
        for name, sha in report['provenance']['source_sha256'].items():
            if file_digest(ROOT / name) != sha: raise ValueError(f'Source changed: {name}')
        for artifact in report['artifacts']:
            if file_digest(folder / artifact['name']) != artifact['sha256']: raise ValueError('Detailed artifact changed')
    queries = []
    with (ROOT / 'data/processed/catalog-bm25-title-v1/queries.tsv').open() as handle:
        for locale, query, text in csv.reader(handle, delimiter='\t'):
            queries.append({'locale': locale, 'query_id': int(query), 'query': base64.b64decode(text).decode()})
    directory.mkdir()
    subprocess.run(COMPOSE + ['start', 'elasticsearch'], check=True, capture_output=True)
    client = LocalElasticsearch()
    restored = []
    try:
        backend(client, json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text())); docker_observation()
        for i, report in enumerate(reports):
            state = restored_index(client, report['index_name'], report['config']['catalog_by_locale'])
            if state['uuid'] != report['index']['uuid']: raise ValueError('Index identity did not persist')
            actual = {}
            with process(report['index_name'], directory, f'api-restored-{i}'):
                if request('/health/ready')[0] != 200: raise ValueError('Restored API not ready')
                for query in queries:
                    status, result = request('/v1/search', {'locale': query['locale'], 'query': query['query'], 'limit': 100})
                    if status != 200: raise ValueError('Restored search failed')
                    actual[(query['locale'], query['query_id'])] = [(h['productId'], h['score']) for h in result['hits']]
            path = directory / f'api-restored-{i}.tsv'
            write_results(path, actual)
            expected = next(a['sha256'] for a in report['artifacts'] if a['name'] == 'elasticsearch.tsv')
            if file_digest(path) != expected: raise ValueError('Results changed after same-volume restart')
            restored.append({'index': report['index_name'], 'uuid': state['uuid'], 'counts': state['counts'],
                             'http_search_requests': len(queries), 'ranked_sha256': file_digest(path)})
    finally:
        subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)
    result = {'first_report_sha256': file_digest(paths[0]), 'repeat_report_sha256': file_digest(paths[1]),
              'verification_script_sha256': file_digest(Path(__file__)), 'equal_fields': equal_fields,
              'all_nine_artifact_hashes_equal': True, 'fresh_index_uuids_differ': True,
              'summary': reports[0]['summary'], 'restored_indexes': restored,
              'resources_first': reports[0]['resources'], 'resources_repeat': reports[1]['resources'],
              'execution_first': reports[0]['execution'], 'execution_repeat': reports[1]['execution'],
              'project_services_stopped_volume_preserved': True,
              'limits': 'Two fresh full-catalog builds and graceful same-volume restart; no crash/update recovery, capacity or final-test claim. Repeatability does not convert failed parity targets into passes.'}
    atomic_json(output, result)
    print(json.dumps({'all_nine_artifact_hashes_equal': True, 'restored_queries': sum(r['http_search_requests'] for r in restored),
                      'summary': result['summary'], 'project_services_stopped': True}, indent=2))


if __name__ == '__main__': main()
