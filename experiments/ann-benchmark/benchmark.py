"""Build/reuse frozen locale graphs, isolate native timings, evaluate ANN and RRF."""

import argparse
import csv
import json
import os
from pathlib import Path
import platform
from statistics import fmean
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import duckdb
import faiss
import numpy as np

from ann_core import agreement_rows, choose_candidate
from audit_esci import records
from catalog_metrics import evaluate_retrieval, mean_interval
from dense_metrics import METRICS, evaluate_dense, paired_comparison, summarize_dense
from download_esci import verify_file
from embedding_checkpoint import atomic_json, fingerprint
from prepare_esci_splits import file_digest
from rrf_fusion import read_rankings, rrf_rank
from run_bm25 import validation_groups

SOURCES = ('config/catalog-ann-v1.json', 'docs/decisions/0008-ann-cost-quality.md',
           'experiments/ann-benchmark/pyproject.toml', 'experiments/ann-benchmark/uv.lock',
           'experiments/ann-benchmark/ann_core.py', 'experiments/ann-benchmark/worker.py',
           'experiments/ann-benchmark/benchmark.py', 'experiments/ann-benchmark/tests/test_ann.py')
POLICY = ROOT / 'config/catalog-ann-v1.json'
LOCALES = ('es', 'jp', 'us')
DENSE_DIR = ROOT / 'data/processed/catalog-dense-exact-v1'
BM25_DIR = ROOT / 'data/processed/catalog-bm25-title-v1'
RRF_DIR = ROOT / 'data/processed/catalog-rrf-v1'
CATALOG_DIR = ROOT / 'data/processed/catalog-embeddings-v1'


def verify_sources(sources):
    for name, sha in sources.items():
        if file_digest(ROOT / name) != sha:
            raise ValueError(f'Frozen source changed: {name}')


def inputs(policy):
    report_paths = {name: ROOT / f'reports/{name}.json' for name in
                    ('catalog-bm25-title-v1', 'catalog-dense-exact-v1', 'catalog-rrf-v1', 'catalog-embeddings-v1')}
    reports = {name: json.loads(p.read_text()) for name, p in report_paths.items()}
    for report in reports.values():
        verify_sources(report['provenance']['source_sha256'])
    dense, bm25, rrf, export = [reports[name] for name in
                              ('catalog-dense-exact-v1', 'catalog-bm25-title-v1', 'catalog-rrf-v1', 'catalog-embeddings-v1')]
    if (dense['provenance']['bm25_report_sha256'] != file_digest(report_paths['catalog-bm25-title-v1'])
            or rrf['provenance']['input_report_sha256']['dense'] != file_digest(report_paths['catalog-dense-exact-v1'])):
        raise ValueError('Frozen retrieval lineage mismatch')
    verify_sources(bm25['provenance']['frozen_source_sha256'])
    for name, sha in [('contract.json', export['provenance']['contract_sha256']),
                      ('export-manifest.json', export['provenance']['export_manifest_sha256'])]:
        if file_digest(CATALOG_DIR / name) != sha:
            raise ValueError('Catalog export contract/manifest changed')
    catalog_contract = json.loads((CATALOG_DIR / 'contract.json').read_text())
    manifest = json.loads((CATALOG_DIR / 'export-manifest.json').read_text())
    if manifest['contract_fingerprint'] != fingerprint(catalog_contract) or manifest['completion'] != export['completion']:
        raise ValueError('Catalog completion binding mismatch')
    for field in ('source_sha256', 'pilot_source_sha256', 'frozen_source_sha256'):
        verify_sources(catalog_contract[field])
    required = [('catalog-dense-exact-v1', DENSE_DIR, ('queries.json', 'queries.tsv', 'query-vectors.f32', 'results.tsv', 'per-query.json')),
                ('catalog-bm25-title-v1', BM25_DIR, ('queries.tsv', 'results.tsv', 'query-status.tsv', 'per-query.json')),
                ('catalog-rrf-v1', RRF_DIR, ('results.tsv', 'per-query.json'))]
    for name, directory, names in required:
        specs = {a['name']: a for a in reports[name]['artifacts']}
        for artifact in names:
            verify_file(directory / artifact, specs[artifact])
    validation = ROOT / 'data/processed/esci-ranking-v1/validation.parquet'
    verify_file(validation, dense['provenance']['validation_input'])
    with duckdb.connect(config={'threads': 1, 'memory_limit': '1GB'}) as connection:
        connection.read_parquet(str(validation)).create_view('validation')
        groups = validation_groups(records(connection, '''SELECT product_locale,query_id,query,product_id,
            esci_label,split,evaluation_split FROM validation ORDER BY product_locale,query_id,product_id'''))
    queries = json.loads((DENSE_DIR / 'queries.json').read_text())
    expected = [{'locale': key[0], 'query_id': key[1], 'query': g['query']} for key, g in sorted(groups.items())]
    if queries != expected or file_digest(DENSE_DIR / 'queries.tsv') != file_digest(BM25_DIR / 'queries.tsv'):
        raise ValueError('Query vector identities differ from validation')
    frozen = {m: json.loads((directory / 'per-query.json').read_text())
              for m, directory in [('dense', DENSE_DIR), ('bm25', BM25_DIR), ('hybrid', RRF_DIR)]}
    terms = {(r['locale'], r['query_id']): r['analyzed_terms'] for r in frozen['bm25']}
    if (evaluate_dense(groups, DENSE_DIR / 'results.tsv') != frozen['dense']
            or evaluate_dense(groups, RRF_DIR / 'results.tsv') != frozen['hybrid']
            or evaluate_retrieval(groups, BM25_DIR / 'results.tsv', BM25_DIR / 'query-status.tsv', terms) != frozen['bm25']):
        raise ValueError('Frozen per-query metrics no longer reproduce')
    contract = {'policy': policy, 'source_sha256': {p: file_digest(ROOT / p) for p in SOURCES},
                'catalog_manifest_sha256': export['provenance']['export_manifest_sha256'],
                'catalog_contract_sha256': export['provenance']['contract_sha256'],
                'report_sha256': {n: file_digest(p) for n, p in report_paths.items()},
                'query_vector_sha256': file_digest(DENSE_DIR / 'query-vectors.f32'),
                'query_identity_sha256': file_digest(DENSE_DIR / 'queries.json'),
                'runtime': {'python': platform.python_version(), 'faiss': faiss.__version__, 'numpy': np.__version__,
                            'duckdb': duckdb.__version__, 'platform': platform.platform(), 'faiss_compile': faiss.get_compile_options()}}
    return contract, groups, frozen


def worker(mode, contract_path, index_dir, locale, method=None, output=None):
    command = [sys.executable, str(Path(__file__).with_name('worker.py')), mode, '--contract', str(contract_path),
               '--directory', str(index_dir), '--locale', locale]
    if mode == 'build':
        command += ['--catalog-dir', str(CATALOG_DIR)]
    else:
        command += ['--dense-dir', str(DENSE_DIR), '--method', method, '--output', str(output)]
    env = dict(os.environ, OMP_NUM_THREADS='4' if mode == 'build' else '1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
    subprocess.run(command, env=env, check=True)


def combined_results(parts, destination):
    with destination.open('w', encoding='utf-8', newline='') as target:
        for i, path in enumerate(parts):
            with path.open(encoding='utf-8') as source:
                header = next(source)
                if i == 0:
                    target.write(header)
                for line in source:
                    target.write(line)


def fused_results(bm25, dense, destination):
    with destination.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['product_locale', 'query_id', 'rank', 'product_id', 'score'])
        for (loc, qid) in sorted(bm25):
            for rank, (product, score) in enumerate(rrf_rank(bm25[(loc, qid)], dense[(loc, qid)]), 1):
                writer.writerow([loc, qid, rank, product, repr(score)])


def summary(rows, policy):
    return {'overall': summarize_dense(rows, policy),
            'by_locale': {loc: summarize_dense([r for r in rows if r['locale'] == loc], policy) for loc in LOCALES}}


def paired(rows, reference, policy):
    def compare(a, b):
        return {m: {'delta' if k == 'dense_minus_bm25' else k: v for k, v in fields.items()}
                for m, fields in paired_comparison(a, b, policy).items()}
    return {'overall': compare(rows, reference),
            'by_locale': {loc: compare([r for r in rows if r['locale'] == loc],
                                       [r for r in reference if r['locale'] == loc]) for loc in LOCALES}}


def latency_summary(values):
    return {'samples': len(values), 'sum_seconds': sum(values) / 1000,
            **{name: float(np.quantile(values, q)) for name, q in [('p50_ms', .5), ('p95_ms', .95), ('p99_ms', .99)]}}


def evaluate(output, indices, contract, groups, frozen):
    policy = contract['policy']
    methods = ['flat'] + [f'hnsw-{ef}' for ef in policy['efSearch']]
    bm25 = read_rankings(BM25_DIR / 'results.tsv', groups)
    frozen_dense = read_rankings(DENSE_DIR / 'results.tsv', groups)
    metrics, rankings, dense_rows, hybrid_rows, timing = {}, {}, {}, {}, {}
    artifacts = []
    for method in methods:
        directory = output / method
        directory.mkdir(exist_ok=True)
        combined_results([output / 'workers' / method / loc / 'results.tsv' for loc in LOCALES], directory / 'dense.tsv')
        rankings[method] = read_rankings(directory / 'dense.tsv', groups)
        dense_rows[method] = evaluate_dense(groups, directory / 'dense.tsv')
        fused_results(bm25, rankings[method], directory / 'hybrid.tsv')
        hybrid_rows[method] = evaluate_dense(groups, directory / 'hybrid.tsv')
        atomic_json(directory / 'per-query.json', {'dense': dense_rows[method], 'hybrid': hybrid_rows[method]})
        metrics[method] = {'dense': summary(dense_rows[method], policy), 'hybrid': summary(hybrid_rows[method], policy),
                           'dense_vs_frozen_torch': paired(dense_rows[method], frozen['dense'], policy),
                           'hybrid_vs_frozen_torch_rrf': paired(hybrid_rows[method], frozen['hybrid'], policy)}
        by_locale, pooled = {}, []
        for loc in LOCALES:
            worker_dir = output / 'workers' / method / loc
            sessions = json.loads((worker_dir / 'session.json').read_text())
            passes = json.loads((worker_dir / 'latencies.json').read_text())
            values = [v for p in passes for v in p['milliseconds']]
            pooled.extend(values)
            by_locale[loc] = {'pooled_three_passes': latency_summary(values),
                              'by_pass': [latency_summary(p['milliseconds']) for p in passes], 'session': sessions}
        timing[method] = {'pooled_three_passes': latency_summary(pooled), 'by_locale': by_locale}
        for name in ('dense.tsv', 'hybrid.tsv', 'per-query.json'):
            path = directory / name
            artifacts.append({'name': str(path.relative_to(output)), 'bytes': path.stat().st_size, 'sha256': file_digest(path)})
    agreement, points = {}, {}
    group_map = {(r['locale'], r['query_id']): r['group'] for r in frozen['dense']}
    for method in methods:
        reference = frozen_dense if method == 'flat' else rankings['flat']
        rows = [dict(r, group=group_map[(r['locale'], r['query_id'])]) for r in agreement_rows(rankings[method], reference)]
        agreement[method] = {'reference': 'frozen_torch' if method == 'flat' else 'faiss_flat',
                             'overall': mean_interval(rows, 'agreement_at_100', 2000, 20261101),
                             'by_locale': {loc: mean_interval([r for r in rows if r['locale'] == loc], 'agreement_at_100', 2000, 20261101) for loc in LOCALES}}
        if method != 'flat':
            metrics[method]['dense_vs_flat'] = paired(dense_rows[method], dense_rows['flat'], policy)
            metrics[method]['hybrid_vs_flat_rrf'] = paired(hybrid_rows[method], hybrid_rows['flat'], policy)
            ef = int(method.removeprefix('hnsw-'))
            comparison = metrics[method]['hybrid_vs_flat_rrf']
            points[ef] = {'overall_agreement': agreement[method]['overall']['mean'],
                          'locale_agreement': {loc: a['mean'] for loc, a in agreement[method]['by_locale'].items()},
                          'hybrid_paired_lower': comparison['overall']['known_exact_recall_at_100']['paired_delta_ci95'][0],
                          'locale_hybrid_delta': {loc: a['known_exact_recall_at_100']['delta'] for loc, a in comparison['by_locale'].items()}}
    builds = {loc: json.loads((indices / loc / 'build.json').read_text()) for loc in LOCALES}
    return {'metrics': metrics, 'agreement': agreement, 'selection': choose_candidate(points),
            'selection_inputs': points, 'timing': timing, 'builds': builds, 'artifacts': artifacts}


def run(args):
    started = time.perf_counter()
    if args.report.exists() or args.output_dir.exists() and not args.resume:
        raise FileExistsError('Use fresh evidence paths (or --resume for an unfinished run)')
    policy = json.loads(POLICY.read_text())
    expected = {'partition': 'validation', 'dimensions': 384, 'cutoff': 100, 'faiss_version': '1.15.1',
                'M': 16, 'efConstruction': 100, 'build_threads': 4, 'seed': 20261102,
                'efSearch': [128, 256, 512], 'query_threads': 1, 'warmup_queries': 16, 'timed_passes': 3,
                'timing_order_seed': 20261102, 'confidence': {'resamples': 2000, 'seed': 20261101}}
    if any(policy[k] != v for k, v in expected.items()) or faiss.__version__ != policy['faiss_version']:
        raise ValueError('Only the predeclared native version/protocol is supported')
    contract, groups, frozen = inputs(policy)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    contract_path = args.output_dir / 'contract.json'
    if contract_path.exists() and json.loads(contract_path.read_text()) != contract:
        raise ValueError('Run contract changed')
    atomic_json(contract_path, contract)
    indices = args.indices_dir or args.output_dir / 'indices'
    for loc in LOCALES:
        worker('build', contract_path, indices / loc, loc)
    methods = ['flat'] + [f'hnsw-{ef}' for ef in policy['efSearch']]
    for method in methods:
        for loc in LOCALES:
            destination = args.output_dir / 'workers' / method / loc
            if destination.exists():
                if not (destination / 'session.json').exists():
                    raise ValueError('Incomplete query worker output; preserve it and use a fresh output directory')
                raise ValueError('Query worker output exists; fresh evidence directory required')
            worker('search', contract_path, indices / loc, loc, method, destination)
    evaluated = evaluate(args.output_dir, indices, contract, groups, frozen)
    for loc in LOCALES:
        for spec in evaluated['builds'][loc]['artifacts']:
            verify_file(indices / loc / spec['name'], spec)
    report = {'config': policy, 'partition': 'validation', 'official_test_read': False,
              'contract': contract, **evaluated, 'main_wall_seconds': time.perf_counter() - started,
              'checks': {'all_locale_products_indexed': True, 'saved_query_vectors_reused': True,
                         'all_three_pass_rankings_identical': True, 'index_artifacts_unchanged_after_search': True,
                         'frozen_baselines_reproduced': True, 'exact_locale_query_group_pairing': True},
              'limits': 'Finite validation grid, incomplete human judgments and one frozen multithread-built graph per locale. Agreement is not human recall; coverage is not precision. Warm serial native timings exclude encoding, API, I/O serialization and concurrency. Worker RSS is per-locale, not all-index/whole-host residency. No independent graph rebuild identity, int8, test, production adoption or QPS claim.'}
    atomic_json(args.output_dir / 'manifest.json', report)
    atomic_json(args.report, report)
    print(json.dumps({'selection': report['selection'], 'agreement': {m: a['overall']['mean'] for m, a in report['agreement'].items()},
                      'hybrid_recall': {m: a['hybrid']['overall']['known_exact_recall_at_100']['mean'] for m, a in report['metrics'].items()}}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/catalog-ann-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/catalog-ann-v1.json')
    parser.add_argument('--indices-dir', type=Path)
    parser.add_argument('--resume', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
