"""Bounded, independently sampled Elasticsearch/Lucene lexical parity experiment."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

import duckdb

from audit_esci import records
from download_esci import ROOT, verify_file
from embedding_checkpoint import atomic_json
from es_pilot import (ANALYZERS, EDGES, LOCALES, LocalElasticsearch, bulk_payload, canonical_tokens,
                      compare_rankings, index_definition, java_tokens, normalized, owned_index,
                      parse_search, validate_bulk)
from es_pilot import search_body
from prepare_esci_splits import file_digest
from run_bm25 import JAR, encode, validation_groups

CONFIG = ROOT / 'config/elasticsearch-pilot-v1.json'
SOURCES = ('config/elasticsearch-pilot-v1.json', 'docs/decisions/0011-elasticsearch-parity-pilot.md',
           'infra/elasticsearch/Dockerfile', 'infra/elasticsearch/compose.yml', 'scripts/es_pilot.py',
           'scripts/run_elasticsearch_pilot.py', 'scripts/elasticsearch_fixture.py',
           'tools/java/lab/commerce/search/AnalyzerParity.java', 'tests/test_es_pilot.py')


def sample_queries(groups, policy):
    selected = []
    for locale in LOCALES:
        candidates = [key for key in groups if key[0] == locale]
        candidates.sort(key=lambda key: (hashlib.sha256(f"{policy['sample_seed']}\t{locale}\t{key[1]}".encode()).hexdigest(), key[1]))
        if len(candidates) < policy['queries_per_locale']:
            raise ValueError('Insufficient locale queries')
        selected.extend(candidates[:policy['queries_per_locale']])
    return [{'locale': key[0], 'query_id': key[1], 'query': groups[key]['query']} for key in sorted(selected)]


def backend(client, policy):
    root = client.ready()
    version = root['version']
    if (root['cluster_name'] != 'commerce-search-lab-pilot' or version['number'] != policy['elasticsearch_version']
            or version['lucene_version'] != policy['elasticsearch_lucene_version']):
        raise ValueError('Unexpected cluster/backend/Lucene version')
    license = client.request('GET', '/_license')['license']
    if license['type'] != 'basic' or license['status'] != 'active':
        raise ValueError('Pilot requires active free Basic license, not trial/cloud')
    nodes = client.request('GET', '/_nodes/plugins')['nodes']
    if len(nodes) != 1:
        raise ValueError('Pilot requires exactly one node')
    plugins = next(iter(nodes.values()))['plugins']
    if not any(p['name'] == 'analysis-kuromoji' and p['version'] == policy['elasticsearch_version'] for p in plugins):
        raise ValueError('Pinned Japanese plugin is absent')
    return {'version': version, 'license': {'type': license['type'], 'status': license['status']},
            'plugins': [{'name': p['name'], 'version': p['version']} for p in plugins]}


def docker_observation():
    compose = ['docker', 'compose', '-f', str(ROOT / 'infra/elasticsearch/compose.yml')]
    identity = subprocess.run(compose + ['ps', '-q', 'elasticsearch'], check=True, capture_output=True, text=True).stdout.strip()
    if not identity or '\n' in identity:
        raise ValueError('Expected one task-owned Elasticsearch container')
    template = '{"image":{{json .Image}},"memory_limit":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}},"owner":{{json (index .Config.Labels "lab.commerce.owner")}},"ports":{{json .HostConfig.PortBindings}}}'
    record = json.loads(subprocess.run(['docker', 'inspect', '--format', template, identity], check=True, capture_output=True, text=True).stdout)
    if (record['owner'] != 'commerce-search-lab-pilot' or record['memory_limit'] != 3 * 2**30
            or record['nano_cpus'] != 2_000_000_000 or record['ports'] != {'9200/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '19200'}]}):
        raise ValueError('Pilot container resource/ownership/binding mismatch')
    memory = subprocess.run(['docker', 'exec', identity, 'sh', '-c', 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak'],
                            check=True, capture_output=True, text=True).stdout.splitlines()
    record['observed_cgroup_memory_current_bytes'], record['container_lifetime_cgroup_memory_peak_bytes'] = map(int, memory)
    record['note'] = 'Cgroup memory includes container JVM/native/cache; peak is since container startup, not isolated-run or whole-host RSS.'
    return record


def create_and_load(client, name, products, policy):
    owned_index(name)
    if client.request('GET', f'/{name}', missing=True) is not None:
        raise FileExistsError('Index exists; use a fresh task-owned name')
    response = client.request('PUT', f'/{name}', index_definition(policy))
    if not response.get('acknowledged') or not response.get('shards_acknowledged'):
        raise ValueError('Index creation unacknowledged')
    for start in range(0, len(products), 500):
        chunk = products[start:start + 500]
        validate_bulk(client.request('POST', f'/{name}/_bulk', bulk_payload(chunk), ndjson=True), len(chunk))
    client.request('POST', f'/{name}/_refresh')
    client.request('POST', f'/{name}/_forcemerge?max_num_segments=1')
    client.request('POST', f'/{name}/_flush')
    if client.request('GET', f'/{name}/_count')['count'] != len(products):
        raise ValueError('Indexed document count mismatch')
    health = client.request('GET', f'/_cluster/health/{name}?wait_for_status=green&timeout=30s')
    if health.get('timed_out') or health['status'] != 'green' or health['active_primary_shards'] != 1:
        raise ValueError('Pilot primary is not healthy')
    return health


def retrieve(client, name, queries, catalog):
    output = {}
    for start in range(0, len(queries), 25):
        chunk = queries[start:start + 25]
        lines = []
        for row in chunk:
            lines += [json.dumps({'index': name, 'search_type': 'query_then_fetch', 'request_cache': False}),
                      json.dumps(search_body(row['locale'], row['query']), ensure_ascii=False)]
        responses = client.request('POST', '/_msearch?max_concurrent_searches=1', '\n'.join(lines) + '\n', ndjson=True)['responses']
        if len(responses) != len(chunk):
            raise ValueError('Search response query coverage mismatch')
        for row, response in zip(chunk, responses, strict=True):
            output[(row['locale'], row['query_id'])] = parse_search(response, row['locale'], catalog)
    return output


def write_results(path, hits):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['product_locale', 'query_id', 'rank', 'product_id', 'score'])
        for (locale, query_id), rows in sorted(hits.items()):
            for rank, (product, score) in enumerate(rows, 1):
                writer.writerow([locale, query_id, rank, product, repr(score)])


def read_reference(path, queries, catalog):
    result = {(q['locale'], q['query_id']): [] for q in queries}
    with path.open(encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            key, product = (row['product_locale'], int(row['query_id'])), row['product_id']
            if key not in result or (key[0], product) not in catalog or int(row['rank']) != len(result[key]) + 1:
                raise ValueError('Invalid offline reference identity/rank')
            result[key].append((product, float(row['score'])))
    return result


def analyze(client, name, probes, stage):
    expected = java_tokens(probes, stage)
    rows = []
    for p in probes:
        actual = canonical_tokens(client.request('POST', f'/{name}/_analyze',
                                                  {'analyzer': ANALYZERS[p['locale']], 'text': normalized(p['text'])}))
        rows.append(dict(p, reference=expected[p['id']], elasticsearch=actual, equal=actual == expected[p['id']]))
    return rows


def measure(client, name, products, queries, stage, policy):
    catalog = {(p['locale'], p['product_id']) for p in products}
    if len(catalog) != len(products):
        raise ValueError('Duplicated locale-product metadata')
    with (stage / 'products.tsv').open('w', encoding='utf-8') as handle:
        for p in products:
            handle.write(f"{p['locale']}\t{p['product_id']}\t{encode(p['title'])}\n")
    with (stage / 'queries.tsv').open('w', encoding='utf-8') as handle:
        for q in queries:
            handle.write(f"{q['locale']}\t{q['query_id']}\t{encode(q['query'])}\n")
    command = ['java', '-Xmx1g', '--enable-native-access=ALL-UNNAMED', '-cp', str(JAR), 'lab.commerce.search.CatalogCli']
    tick = time.perf_counter()
    index_run = subprocess.run(command + ['index', str(stage / 'products.tsv'), str(stage / 'reference-index'), '1.2', '.75'], check=True, capture_output=True, text=True)
    query_run = subprocess.run(command + ['retrieve', str(stage / 'reference-index'), str(stage / 'queries.tsv'),
                                         str(stage / 'reference.tsv'), str(stage / 'reference-status.tsv'), '100', '1.2', '.75'], check=True, capture_output=True, text=True)
    for run, key, expected in ((index_run, 'products', len(products)), (query_run, 'queries', len(queries))):
        observed = dict(line.split('=', 1) for line in run.stdout.splitlines() if '=' in line)
        if observed.get('lucene') != policy['reference_lucene_version'] or int(observed[key]) != expected:
            raise ValueError('Frozen reference version/count mismatch')
    reference_seconds = time.perf_counter() - tick
    probes = [{'locale': q['locale'], 'id': f"query/{q['locale']}/{q['query_id']}", 'text': q['query']} for q in queries]
    for locale in LOCALES:
        probes += [{'locale': p['locale'], 'id': f"title/{locale}/{p['product_id']}", 'text': p['title']}
                   for p in [p for p in products if p['locale'] == locale][:policy['analysis_titles_per_locale']]]
    probes += [{'locale': loc, 'id': f'edge/{loc}/{key}', 'text': text} for loc, key, text in EDGES]
    tick = time.perf_counter()
    health = create_and_load(client, name, products, policy)
    load_seconds = time.perf_counter() - tick
    probe_rows = analyze(client, name, probes, stage)
    atomic_json(stage / 'analysis.json', probe_rows)
    tick = time.perf_counter()
    actual = retrieve(client, name, queries, catalog)
    retrieval_seconds = time.perf_counter() - tick
    write_results(stage / 'elasticsearch.tsv', actual)
    expected = read_reference(stage / 'reference.tsv', queries, catalog)
    parity = compare_rankings(expected, actual, policy)
    atomic_json(stage / 'parity-per-query.json', parity)
    passed = all(p['equal'] for p in probe_rows) and all(r['ordered_ids_equal'] and not r['normalized_score_mismatches'] for r in parity)
    summary = {'products': len(products), 'queries': len(queries), 'analyzer_probes': len(probes),
               'analyzer_mismatches': sum(not p['equal'] for p in probe_rows),
               'ordered_id_mismatch_queries': sum(not r['ordered_ids_equal'] for r in parity),
               'normalized_score_mismatch_queries': sum(bool(r['normalized_score_mismatches']) for r in parity),
               'max_normalized_absolute_score_error': max(r['max_normalized_absolute_score_error'] for r in parity),
               'zero_result_queries_reference': sum(not v for v in expected.values()),
               'zero_result_queries_es': sum(not v for v in actual.values()),
               'passed_bounded_parity': passed,
               'by_locale': {loc: {'queries': sum(r['locale'] == loc for r in parity),
                                  'ordered_id_mismatch_queries': sum(r['locale'] == loc and not r['ordered_ids_equal'] for r in parity),
                                  'normalized_score_mismatch_queries': sum(r['locale'] == loc and bool(r['normalized_score_mismatches']) for r in parity)} for loc in LOCALES}}
    return {'summary': summary, 'health': health,
            'execution': {'reference_build_and_batch_seconds': reference_seconds, 'es_load_refresh_merge_seconds': load_seconds,
                          'es_http_retrieval_batch_seconds': retrieval_seconds,
                          'note': 'Bounded HTTP/native batches including setup; not API percentile latency, concurrency or QPS.'},
            'analysis_mismatches': [p for p in probe_rows if not p['equal']],
            'retrieval_mismatches': [r for r in parity if not r['ordered_ids_equal'] or r['normalized_score_mismatches']]}


def run(args):
    started = time.perf_counter()
    if args.output_dir.exists() or args.report.exists():
        raise FileExistsError('Use fresh evidence paths')
    owned_index(args.index)
    policy = json.loads(CONFIG.read_text())
    if (policy['products_per_locale'] != 10000 or policy['queries_per_locale'] != 100 or policy['cutoff'] != 100
            or policy['raw_score_factor'] != 2.2 or policy['k1'] != 1.2 or policy['b'] != .75):
        raise ValueError('Only the predeclared pilot is supported')
    baseline_path = ROOT / 'reports/bm25-title-v1.json'
    baseline = json.loads(baseline_path.read_text())
    for name, sha in baseline['provenance']['source_sha256'].items():
        if file_digest(ROOT / name) != sha:
            raise ValueError(f'Frozen baseline source changed: {name}')
    raw = ROOT / 'data/raw/esci/shopping_queries_dataset_products.parquet'
    validation = ROOT / 'data/processed/esci-ranking-v1/validation.parquet'
    verify_file(raw, baseline['provenance']['products_input'])
    verify_file(validation, baseline['provenance']['validation_input'])
    products = []
    with duckdb.connect(config={'threads': 1, 'memory_limit': '1GB'}) as db:
        db.read_parquet(str(raw)).create_view('products')
        for locale in LOCALES:
            rows = db.execute('''SELECT product_locale,product_id,product_title FROM products WHERE product_locale=?
                ORDER BY sha256(? || chr(9) || product_locale || chr(9) || product_id),product_id LIMIT ?''',
                              [locale, policy['sample_seed'], policy['products_per_locale']]).fetchall()
            if len(rows) != policy['products_per_locale']:
                raise ValueError('Product sample coverage mismatch')
            products += [dict(zip(('locale', 'product_id', 'title'), row, strict=True)) for row in rows]
        db.read_parquet(str(validation)).create_view('validation')
        groups = validation_groups(records(db, '''SELECT product_locale,query_id,query,product_id,esci_label,split,evaluation_split
            FROM validation ORDER BY product_locale,query_id,product_id'''))
    products.sort(key=lambda p: (p['locale'], p['product_id']))
    queries = sample_queries(groups, policy)
    client = LocalElasticsearch(args.endpoint)
    server = backend(client, policy)
    initial_container = docker_observation()
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.es-pilot-', dir=args.output_dir.parent) as tmp:
        stage = Path(tmp) / 'run'
        stage.mkdir()
        result = measure(client, args.index, products, queries, stage, policy)
        stats = client.request('GET', f'/{args.index}/_stats/docs,store')['indices'][args.index]['primaries']
        node_stats = client.request('GET', '/_nodes/stats/jvm,process?filter_path=nodes.*.jvm.mem,nodes.*.process.mem')
        final_container = docker_observation()
        if initial_container['image'] != final_container['image']:
            raise ValueError('Container image changed during pilot')
        artifacts = [{'name': name, 'bytes': (stage / name).stat().st_size, 'sha256': file_digest(stage / name)}
                     for name in ('products.tsv', 'queries.tsv', 'reference.tsv', 'reference-status.tsv', 'probes.tsv',
                                  'reference-tokens.tsv', 'analysis.json', 'elasticsearch.tsv', 'parity-per-query.json')]
        report = {'config': policy, 'official_test_read': False, 'partition': 'validation', **result,
                  'server': server, 'index_name': args.index, 'observed_index_stats': stats, 'observed_node_memory': node_stats,
                  'container_before': initial_container, 'container_after': final_container,
                  'provenance': {'baseline_report_sha256': file_digest(baseline_path), 'jar_sha256': file_digest(JAR),
                                 'products_input': baseline['provenance']['products_input'],
                                 'validation_input': baseline['provenance']['validation_input'],
                                 'source_sha256': {name: file_digest(ROOT / name) for name in SOURCES},
                                 'frozen_source_sha256': baseline['provenance']['source_sha256']},
                  'artifacts': artifacts, 'main_wall_seconds': time.perf_counter() - started,
                  'limits': '30k independently sampled metadata products and 300 validation queries, not full-catalog relevance or a serving API. Lucene versions differ and ES legacy scores are normalized only for comparison. Probe parity is for selected strings; finite observed parity does not prove universal equivalence. No hybrid, ANN adoption, training, update/freshness, production capacity or official-test claim.'}
        atomic_json(stage / 'manifest.json', report)
        stage.rename(args.output_dir)
        atomic_json(args.report, report)
    print(json.dumps(result['summary'], indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:19200')
    parser.add_argument('--index', default='csl-pilot-es-v1-a')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/elasticsearch-pilot-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/elasticsearch-pilot-v1.json')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
