"""Diagnose frozen HNSW-512 lists without issuing another nearest-neighbor search."""

import argparse
import gc
import json
import math
import os
from pathlib import Path
import platform
import resource
import sys
import tempfile
import time

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[name] = '1'

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import duckdb
import faiss
import numpy as np

from benchmark import inputs
from dense_metrics import evaluate_dense
from download_esci import verify_file
from embedding_checkpoint import atomic_json
from miss_diagnostics import CATEGORIES, aggregate, diagnose, representative_pairs, select_cases
from prepare_esci_splits import file_digest
from rrf_fusion import read_rankings
from worker import verify_index

CONFIG = ROOT / 'config/ann-miss-analysis-v1.json'
SOURCES = ('config/ann-miss-analysis-v1.json', 'docs/decisions/0009-ann-miss-analysis.md',
           'experiments/ann-benchmark/analyze_misses.py', 'experiments/ann-benchmark/miss_diagnostics.py',
           'experiments/ann-benchmark/tests/test_misses.py')


def source_specs(ann):
    paths = {ROOT / name: sha for name, sha in ann['contract']['source_sha256'].items()}
    for path, sha in paths.items():
        if file_digest(path) != sha:
            raise ValueError(f'Frozen ANN source changed: {path.name}')


def metadata(cases, raw, spec):
    verify_file(raw, spec)
    keys = {(case['locale'], pair[field]) for case in cases for pair in case['pairs']
            for field in ('missing_product_id', 'replacement_product_id')}
    if not keys:
        return
    with duckdb.connect(config={'threads': 1, 'memory_limit': '1GB'}) as connection:
        connection.execute('CREATE TABLE requested(locale VARCHAR, product_id VARCHAR)')
        connection.executemany('INSERT INTO requested VALUES (?,?)', sorted(keys))
        connection.read_parquet(str(raw)).create_view('products')
        rows = connection.execute('''SELECT p.product_locale,p.product_id,p.product_title FROM products p
            JOIN requested r ON p.product_locale=r.locale AND p.product_id=r.product_id
            ORDER BY p.product_locale,p.product_id''').fetchall()
    titles = {(locale, product): title for locale, product, title in rows}
    if set(titles) != keys or len(titles) != len(rows):
        raise ValueError('Case metadata locale/product coverage mismatch')
    for case in cases:
        for pair in case['pairs']:
            pair['missing_title'] = titles[(case['locale'], pair['missing_product_id'])]
            pair['replacement_title'] = titles[(case['locale'], pair['replacement_product_id'])]


def run(args):
    started = time.perf_counter()
    if args.output_dir.exists() or args.report.exists():
        raise FileExistsError('Use fresh evidence paths')
    policy = json.loads(CONFIG.read_text())
    if (policy['partition'] != 'validation' or policy['primary_method'] != 'hnsw-512' or policy['reference'] != 'flat'
            or policy['cutoff'] != 100 or policy['score_equivalence_absolute'] != 1e-10
            or policy['categories'] != list(CATEGORIES) or faiss.__version__ != '1.15.1'):
        raise ValueError('Only the predeclared pinned diagnostic is supported')
    ann_path = ROOT / 'reports/catalog-ann-v1.json'
    ann = json.loads(ann_path.read_text())
    source_specs(ann)
    _, groups, _ = inputs(ann['config'])
    for spec in ann['artifacts']:
        verify_file(args.ann_dir / spec['name'], spec)
    faiss.omp_set_num_threads(1)
    methods = ('flat', 'hnsw-128', 'hnsw-256', 'hnsw-512')
    rankings = {m: read_rankings(args.ann_dir / m / 'dense.tsv', groups) for m in methods}
    hybrid = {m: read_rankings(args.ann_dir / m / 'hybrid.tsv', groups) for m in ('flat', 'hnsw-512')}
    saved = {m: json.loads((args.ann_dir / m / 'per-query.json').read_text()) for m in ('flat', 'hnsw-512')}
    for method in saved:
        for track in ('dense', 'hybrid'):
            if evaluate_dense(groups, args.ann_dir / method / f'{track}.tsv') != saved[method][track]:
                raise ValueError('Frozen native metrics no longer reproduce')
    by_key = {method: {track: {(r['locale'], r['query_id']): r for r in rows}
                       for track, rows in tracks.items()} for method, tracks in saved.items()}
    query_dir = ROOT / 'data/processed/catalog-dense-exact-v1'
    query_rows = json.loads((query_dir / 'queries.json').read_text())
    query_vectors = np.fromfile(query_dir / 'query-vectors.f32', dtype='<f4').reshape(len(query_rows), 384)
    query_positions = {(r['locale'], r['query_id']): i for i, r in enumerate(query_rows)}
    observations, checked_indexes = [], {}
    for locale in ('es', 'jp', 'us'):
        directory = args.ann_dir / 'indices' / locale
        record = verify_index(directory, ann['contract'], locale)
        if record != ann['builds'][locale]:
            raise ValueError('Frozen index build record changed')
        identities = json.loads((directory / 'ids.json').read_text())
        ids = identities['ids']
        if identities['locale'] != locale or len(ids) != record['products'] or ids != sorted(set(ids)):
            raise ValueError('Index identities invalid')
        positions = {product: i for i, product in enumerate(ids)}
        index = faiss.read_index(str(directory / 'flat.index'))
        if index.d != 384 or index.ntotal != len(ids) or index.metric_type != faiss.METRIC_INNER_PRODUCT:
            raise ValueError('Flat reconstruction source mismatch')
        for key in sorted(k for k in groups if k[0] == locale):
            exact, approximate = rankings['flat'][key], rankings['hnsw-512'][key]
            union = sorted(set(exact) | set(approximate))
            if any(p not in positions for p in union):
                raise ValueError('Neighbor absent from locale ID map')
            vectors = index.reconstruct_batch(np.array([positions[p] for p in union], dtype=np.int64))
            row = diagnose(exact, approximate, dict(zip(union, vectors, strict=True)), query_vectors[query_positions[key]],
                           groups[key]['labels'], policy['score_equivalence_absolute'])
            reference = by_key['flat']['dense'][key]
            actual = by_key['hnsw-512']['dense'][key]
            if (reference['group'] != actual['group'] or reference['known_exact_products'] != actual['known_exact_products']
                    or actual['retrieved_known_exact_at_100'] - reference['retrieved_known_exact_at_100']
                    != row['new_known_exact'] - row['lost_known_exact']):
                raise ValueError('Diagnostic identities/labels do not reproduce source recall')
            exact_products = {p for p, label in groups[key]['labels'].items() if label == 'E'}
            ref_hybrid, actual_hybrid = set(hybrid['flat'][key]), set(hybrid['hnsw-512'][key])
            row.update({'locale': locale, 'query_id': key[1], 'group': reference['group'],
                        'known_exact_products': len(exact_products),
                        'hybrid_recall_delta': by_key['hnsw-512']['hybrid'][key]['known_exact_recall_at_100']
                                               - by_key['flat']['hybrid'][key]['known_exact_recall_at_100'],
                        'hybrid_lost_known_exact': len((ref_hybrid - actual_hybrid) & exact_products),
                        'hybrid_new_known_exact': len((actual_hybrid - ref_hybrid) & exact_products),
                        'still_missing_at_shallower_depth': {str(ef): len((set(exact) - set(approximate)) - set(rankings[f'hnsw-{ef}'][key]))
                                                            for ef in (128, 256)}})
            if row['lost_known_exact'] != len((set(exact) - set(approximate)) & exact_products):
                raise ValueError('Known-Exact loss conservation failure')
            if (by_key['hnsw-512']['hybrid'][key]['retrieved_known_exact_at_100']
                    - by_key['flat']['hybrid'][key]['retrieved_known_exact_at_100']
                    != row['hybrid_new_known_exact'] - row['hybrid_lost_known_exact']):
                raise ValueError('Hybrid known-Exact change does not reproduce source metrics')
            observations.append(row)
        checked_indexes[locale] = record
        del index, positions, identities, ids
        gc.collect()
        print(f'Diagnosed {locale}: {sum(r["locale"] == locale for r in observations)} queries', flush=True)
    overall = aggregate(observations)
    by_locale = {loc: aggregate([r for r in observations if r['locale'] == loc]) for loc in ('es', 'jp', 'us')}
    if not math.isclose(overall['strict_agreement'], ann['agreement']['hnsw-512']['overall']['mean'], abs_tol=1e-12):
        raise ValueError('Diagnostic strict agreement differs from frozen benchmark')
    indexed = {(r['locale'], r['query_id']): r for r in observations}
    cases = []
    for case in select_cases(observations):
        key = (case['locale'], case['query_id'])
        row = indexed[key]
        cases.append(dict(case, query=groups[key]['query'], missing_neighbors=row['missing_neighbors'],
                          category_counts=row['category_counts'], hybrid_recall_delta=row['hybrid_recall_delta'],
                          pairs=[dict(p) for p in representative_pairs(row, case['reasons'])]))
    products_spec = json.loads((ROOT / 'reports/bm25-title-v1.json').read_text())['provenance']['products_input']
    metadata(cases, ROOT / 'data/raw/esci/shopping_queries_dataset_products.parquet', products_spec)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ann-misses-', dir=args.output_dir.parent) as tmp:
        stage = Path(tmp) / 'analysis'
        stage.mkdir()
        atomic_json(stage / 'per-query.json', [{k: v for k, v in r.items() if k != 'pairs'} for r in observations])
        atomic_json(stage / 'pairs.json', [{'locale': r['locale'], 'query_id': r['query_id'], 'pairs': r['pairs']} for r in observations])
        atomic_json(stage / 'cases.json', cases)
        for locale, record in checked_indexes.items():
            for spec in record['artifacts']:
                verify_file(args.ann_dir / 'indices' / locale / spec['name'], spec)
        for spec in ann['artifacts']:
            verify_file(args.ann_dir / spec['name'], spec)
        report = {'config': policy, 'partition': 'validation', 'official_test_read': False,
                  'overall': overall, 'by_locale': by_locale,
                  'case_manifest': [{k: v for k, v in case.items() if k not in ('query', 'pairs')} for case in cases],
                  'prior_nomination_unchanged': ann['selection'],
                  'checks': {'all_query_and_locale_identities_verified': True, 'saved_metrics_reproduced': True,
                             'pair_and_known_exact_counts_conserved': True, 'all_missing_neighbors_classified_once': True,
                             'index_and_input_artifacts_unchanged': True, 'no_new_nearest_neighbor_search': True},
                  'provenance': {'ann_report_sha256': file_digest(ann_path), 'products_input': products_spec,
                                 'ann_contract': ann['contract'],
                                 'index_build_sha256': {loc: file_digest(args.ann_dir / 'indices' / loc / 'build.json') for loc in checked_indexes},
                                 'source_sha256': {name: file_digest(ROOT / name) for name in SOURCES},
                                 'runtime': {'python': platform.python_version(), 'numpy': np.__version__, 'faiss': faiss.__version__, 'duckdb': duckdb.__version__}},
                  'execution': {'main_wall_seconds': time.perf_counter() - started,
                                'max_process_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                                'note': 'One Flat locale index and at most 200 reconstructed vectors per query; aggregate pair records retained. Main wall includes verification, reconstruction, diagnostic scoring, cases and output, excluding imports/final report construction. This is not retrieval latency or serving memory.'},
                  'artifacts': [{'name': name, 'bytes': (stage / name).stat().st_size, 'sha256': file_digest(stage / name)}
                                for name in ('per-query.json', 'pairs.json', 'cases.json')],
                  'limits': 'Query-specific union reconstruction/scoring, not full-catalog float64 ground truth or graph-traversal causality. Pairing and 1e-10 equivalence are diagnostics, not a changed adoption metric. Equal vectors/scores do not imply equal relevance. Cases are purpose-selected; judgments remain incomplete. No parameter changes, new labels or official test exposure.'}
        atomic_json(stage / 'manifest.json', report)
        stage.rename(args.output_dir)
        atomic_json(args.report, report)
    print(json.dumps({'overall': overall, 'by_locale': by_locale}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ann-dir', type=Path, default=ROOT / 'data/processed/catalog-ann-v1')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/ann-miss-analysis-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/ann-miss-analysis-v1.json')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
