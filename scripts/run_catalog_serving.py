"""Full-catalog immutable ES and unchanged Java API functional feasibility."""

import argparse
import base64
import csv
import json
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import threading
import time

import duckdb
from audit_esci import records
from catalog_metrics import evaluate_retrieval, summarize_retrieval
from catalog_serving import export_rows, load
from download_esci import ROOT, verify_file
from embedding_checkpoint import atomic_json
from es_pilot import EDGES, LocalElasticsearch, compare_rankings
from prepare_esci_splits import file_digest
from run_bm25 import validation_groups
from run_elasticsearch_pilot import analyze, backend, docker_observation, read_reference, retrieve, write_results
from verify_search_api import COMPOSE, JAR, compare, process, request

CONFIG = ROOT / 'config/catalog-serving-v1.json'


def python_peak():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if platform.system() == 'Darwin' else value * 1024


class Observations:
    def __init__(self, client, policy, directory):
        self.client, self.policy, self.directory = client, policy, directory
        self.rows, self.errors, self.stop = [], [], threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def capture(self):
        container = docker_observation()
        node = next(iter(self.client.request('GET', '/_nodes/stats/jvm')['nodes'].values()))
        self.rows.append({'elapsed_seconds': time.monotonic() - self.started, 'container': container,
                          'jvm_heap_used_bytes': node['jvm']['mem']['heap_used_in_bytes'], 'python_peak_rss_bytes': python_peak()})
        if node['jvm']['mem']['heap_max_in_bytes'] != self.policy['es_heap_bytes']:
            raise ValueError('Heap envelope changed')

    def loop(self):
        while not self.stop.wait(self.policy['resource_sample_seconds']):
            try: self.capture()
            except Exception as error: self.errors.append(str(error)); return

    def check(self):
        if self.errors:
            raise RuntimeError(self.errors[0])
        if python_peak() > self.policy['python_rss_stop_bytes']:
            raise MemoryError('Python peak RSS budget exceeded')

    def __enter__(self):
        self.started = time.monotonic(); self.capture(); self.thread.start(); return self

    def __exit__(self, *_):
        self.stop.set(); self.thread.join(timeout=40)
        if self.thread.is_alive(): self.errors.append('Resource observer did not stop')
        try: self.capture()
        except Exception as error: self.errors.append(str(error))
        atomic_json(self.directory / 'resources.json', {'samples': self.rows, 'errors': self.errors})


def verified_inputs():
    baseline = json.loads((ROOT / 'reports/catalog-bm25-title-v1.json').read_text())
    api = json.loads((ROOT / 'reports/search-api-v1.json').read_text())
    for mapping in (baseline['provenance']['source_sha256'], baseline['provenance']['frozen_source_sha256'], api['source_sha256']):
        for name, sha in mapping.items():
            if name == '.github/workflows/ci.yml':
                continue  # Historical workflow binding; current CI adds this loader fixture.
            if file_digest(ROOT / name) != sha: raise ValueError(f'Frozen runtime/experiment source changed: {name}')
    if file_digest(JAR) != api['jar_sha256']: raise ValueError('Frozen API JAR changed')
    export = ROOT / 'data/processed/bm25-title-v1/products.tsv'
    verify_file(export, baseline['provenance']['reused_title_export'])
    validation = ROOT / 'data/processed/esci-ranking-v1/validation.parquet'
    verify_file(validation, baseline['provenance']['validation_input'])
    directory = ROOT / 'data/processed/catalog-bm25-title-v1'
    for artifact in baseline['artifacts']: verify_file(directory / artifact['name'], artifact)
    queries = []
    with (directory / 'queries.tsv').open() as handle:
        for loc, query, text in csv.reader(handle, delimiter='\t'):
            queries.append({'locale': loc, 'query_id': int(query), 'query': base64.b64decode(text, validate=True).decode()})
    return baseline, api, export, directory, queries, validation


def metrics(directory, validation, actual, queries, probes):
    terms = {(q['locale'], q['query_id']): len(probes[i]['elasticsearch']) for i, q in enumerate(queries)}
    with (directory / 'query-status.tsv').open('w') as handle:
        handle.write('product_locale\tquery_id\tanalyzed_terms\treturned\n')
        for key, hits in sorted(actual.items()): handle.write(f'{key[0]}\t{key[1]}\t{terms[key]}\t{len(hits)}\n')
    with duckdb.connect(config={'threads': 1, 'memory_limit': '512MB'}) as db:
        db.read_parquet(str(validation)).create_view('validation')
        groups = validation_groups(records(db, '''SELECT product_locale,query_id,query,product_id,esci_label,split,evaluation_split
            FROM validation ORDER BY product_locale,query_id,product_id'''))
    rows = evaluate_retrieval(groups, directory / 'elasticsearch.tsv', directory / 'query-status.tsv', terms)
    atomic_json(directory / 'per-query.json', rows)
    policy = json.loads((ROOT / 'config/catalog-bm25-title-v1.json').read_text())
    return {'overall': summarize_retrieval(rows, policy),
            'by_locale': {loc: summarize_retrieval([r for r in rows if r['locale'] == loc], policy) for loc in ('es', 'jp', 'us')}}


def run(args):
    if args.output_dir.exists() or args.report.exists(): raise FileExistsError('Preserve existing evidence; choose fresh output paths')
    policy = json.loads(CONFIG.read_text())
    if shutil.disk_usage(ROOT).free < policy['minimum_free_disk_bytes']: raise ValueError('Insufficient declared free disk')
    baseline, api, export, reference_dir, queries, validation = verified_inputs()
    if len(queries) != policy['queries']: raise ValueError('Query coverage mismatch')
    args.output_dir.mkdir(parents=True)
    client = LocalElasticsearch()
    pilot = json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text())
    backend(client, pilot); docker_observation()  # Verify ownership before restarting only this service.
    subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)
    subprocess.run(COMPOSE + ['start', 'elasticsearch'], check=True, capture_output=True)
    server = backend(client, pilot)
    started = time.monotonic()
    try:
        with Observations(client, policy, args.output_dir) as observation:
            loaded = load(client, args.index, export_rows(export), policy, observation.check)
            atomic_json(args.output_dir / 'load.json', {k: v for k, v in loaded.items() if k not in ('catalog', 'titles')})
            observation.check()
            probes = [{'locale': q['locale'], 'id': f"query/{q['locale']}/{q['query_id']}", 'text': q['query']} for q in queries]
            probes += [{'locale': p['locale'], 'id': f"title/{p['locale']}/{p['product_id']}", 'text': p['title']} for p in loaded['titles']]
            probes += [{'locale': loc, 'id': f'edge/{loc}/{key}', 'text': text} for loc, key, text in EDGES]
            analysis = analyze(client, args.index, probes, args.output_dir)
            atomic_json(args.output_dir / 'analysis.json', analysis)
            tick = time.monotonic()
            actual = retrieve(client, args.index, queries, loaded['catalog'])
            retrieval_seconds = time.monotonic() - tick
            write_results(args.output_dir / 'elasticsearch.tsv', actual)
            expected = read_reference(reference_dir / 'results.tsv', queries, loaded['catalog'])
            parity = compare_rankings(expected, actual, policy)
            atomic_json(args.output_dir / 'parity-per-query.json', parity)
            measured = metrics(args.output_dir, validation, actual, queries, analysis)
            atomic_json(args.output_dir / 'metrics.json', measured)
            tick = time.monotonic()
            with process(args.index, args.output_dir, 'api'):
                if request('/health/ready')[0] != 200: raise ValueError('Full-catalog API is not ready')
                api_run = compare(queries, actual, args.output_dir, 'api')
            api_seconds = time.monotonic() - tick
            atomic_json(args.output_dir / 'api-checks.json', api_run)
            observation.check()
            inventory = client.request('GET', '/_cat/indices/csl-*?format=json&h=index,uuid,docs.count,store.size&bytes=b')
        observation.check()
        summary = {'products': len(loaded['catalog']), 'queries': len(queries), 'analyzer_probes': len(analysis),
                   'analyzer_mismatches': sum(not p['equal'] for p in analysis),
                   'ordered_id_mismatch_queries': sum(not p['ordered_ids_equal'] for p in parity),
                   'normalized_score_mismatch_queries': sum(bool(p['normalized_score_mismatches']) for p in parity),
                   'api_http_search_requests': api_run['http_search_requests'], 'api_mismatch_cases': len(api_run['mismatch_cases'])}
        summary['passed_lucene_parity'] = not any(summary[k] for k in ('analyzer_mismatches', 'ordered_id_mismatch_queries', 'normalized_score_mismatch_queries'))
        summary['passed_native_api_parity'] = summary['api_mismatch_cases'] == 0
        names = ('analysis.json', 'reference-tokens.tsv', 'elasticsearch.tsv', 'parity-per-query.json',
                 'query-status.tsv', 'per-query.json', 'metrics.json', 'api.tsv', 'api-checks.json')
        source_names = ['config/catalog-serving-v1.json', 'docs/decisions/0013-full-catalog-serving.md',
                        'scripts/catalog_serving.py', 'scripts/run_catalog_serving.py', 'tests/test_catalog_serving.py',
                        'scripts/catalog_loader_fixture.py', '.github/workflows/ci.yml']
        report = {'config': policy, 'partition': 'validation', 'official_test_read': False, 'index_name': args.index,
                  'summary': summary, 'metrics': measured, 'index': loaded['index'], 'server': server, 'inventory': inventory,
                  'execution': {'bulk_batches': loaded['bulk_batches'], 'bulk_seconds': loaded['bulk_seconds'],
                                'merge_seconds': loaded['merge_seconds'], 'retrieval_batch_seconds': retrieval_seconds,
                                'api_functional_batch_seconds': api_seconds, 'main_wall_seconds': time.monotonic() - started},
                  'resources': {'samples': len(observation.rows), 'observed_cgroup_peak_bytes': max(r['container']['container_lifetime_cgroup_memory_peak_bytes'] for r in observation.rows),
                                'sampled_max_jvm_heap_used_bytes': max(r['jvm_heap_used_bytes'] for r in observation.rows), 'python_peak_rss_bytes': python_peak(),
                                'note': 'Cgroup lifetime peak since pre-run restart includes old indexes/cache/native memory; JVM heap use sampled every five seconds. Batch timings are not API capacity or percentiles.'},
                  'artifacts': [{'name': name, 'bytes': (args.output_dir / name).stat().st_size, 'sha256': file_digest(args.output_dir / name)} for name in names],
                  'provenance': {'catalog_report_sha256': file_digest(ROOT / 'reports/catalog-bm25-title-v1.json'),
                                 'api_report_sha256': file_digest(ROOT / 'reports/search-api-v1.json'), 'api_jar_sha256': file_digest(JAR),
                                 'reused_title_export': baseline['provenance']['reused_title_export'],
                                 'validation_input': baseline['provenance']['validation_input'],
                                 'frozen_runtime_sources_except_historical_ci_workflow_checked': True,
                                 'source_sha256': {name: file_digest(ROOT / name) for name in source_names}},
                  'limits': 'Full-catalog lexical functional feasibility; incomplete judgments, fixed versions and one immutable primary. Unknown relevance stays unknown. No concurrent capacity, percentiles, freshness, neural serving, final-test or production claim.'}
        atomic_json(args.report, report)
        print(json.dumps({'summary': summary, 'resources': report['resources']}, indent=2), flush=True)
    finally:
        subprocess.run(COMPOSE + ['stop', 'elasticsearch'], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', default='csl-pilot-catalog-v1-a')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/catalog-serving-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/catalog-serving-v1.json')
    run(parser.parse_args())


if __name__ == '__main__': main()
