"""Encode validation queries and search all saved locale vectors exhaustively."""

import argparse
import csv
import gc
import json
from pathlib import Path
import platform
import resource
from statistics import fmean
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import duckdb
import numpy as np

from audit_esci import records
from catalog_metrics import evaluate_retrieval
from dense_metrics import METRICS, evaluate_dense, paired_comparison, summarize_dense
from download_esci import verify_file
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest
from run_bm25 import encode, validation_groups
from encoder import E5Encoder
from exact_search import ExactTopK, catalog_shards, check_vectors
import torch
import transformers

CONFIG = ROOT / 'config/catalog-dense-exact-v1.json'
SOURCE_PATHS = ('config/catalog-dense-exact-v1.json', 'docs/decisions/0006-exact-dense-retrieval.md',
                'experiments/embedding-pilot/run_dense.py', 'experiments/embedding-pilot/exact_search.py',
                'scripts/dense_metrics.py', 'tests/test_dense_metrics.py',
                'experiments/embedding-pilot/tests/test_exact_search.py')


def verify_sources(sources):
    for name, sha in sources.items():
        if file_digest(ROOT / name) != sha:
            raise ValueError(f'Frozen source changed: {name}')


def run(output, report_path, catalog_dir, bm25_dir):
    started = time.perf_counter()
    if output.exists() or report_path.exists():
        raise FileExistsError('Use fresh output and report paths to preserve evidence')
    policy = json.loads(CONFIG.read_text())
    if (policy['partition'] != 'validation' or policy['retrieval_cutoff'] != 100 or policy['coverage_cutoff'] != 10
            or policy['query_batch_size'] != 128 or policy['cpu_threads'] != 4 or policy['dimensions'] != 384
            or policy['confidence'] != {'resamples': 2000, 'seed': 20261101}):
        raise ValueError('Only the predeclared dense protocol is supported')
    export_path = ROOT / 'reports/catalog-embeddings-v1.json'
    export = json.loads(export_path.read_text())
    for name, sha in (('contract.json', export['provenance']['contract_sha256']),
                      ('export-manifest.json', export['provenance']['export_manifest_sha256'])):
        if file_digest(catalog_dir / name) != sha:
            raise ValueError('Completed catalog export contract/manifest changed')
    contract = json.loads((catalog_dir / 'contract.json').read_text())
    manifest = json.loads((catalog_dir / 'export-manifest.json').read_text())
    if manifest['completion'] != export['completion']:
        raise ValueError('Catalog completion differs from published evidence')
    for field in ('source_sha256', 'pilot_source_sha256', 'frozen_source_sha256'):
        verify_sources(contract[field])
    encoding = contract['encoding_policy']
    if encoding != json.loads((ROOT / 'config/embedding-pilot-v1.json').read_text()):
        raise ValueError('Query/product encoding policy drift')
    model_dir = ROOT / 'models/multilingual-e5-small-614241f'
    source = json.loads((ROOT / encoding['model_source']).read_text())
    model_files = [verify_file(model_dir / item['name'], item) for item in source['files']]
    if model_files != contract['model_files']:
        raise ValueError('Query/product model mismatch')
    baseline_path = ROOT / 'reports/catalog-bm25-title-v1.json'
    baseline = json.loads(baseline_path.read_text())
    verify_sources(baseline['provenance']['source_sha256'])
    verify_sources(baseline['provenance']['frozen_source_sha256'])
    specs = {a['name']: a for a in baseline['artifacts']}
    for name in ('queries.tsv', 'results.tsv', 'query-status.tsv', 'per-query.json'):
        verify_file(bm25_dir / name, specs[name])
    validation = ROOT / 'data/processed/esci-ranking-v1/validation.parquet'
    verify_file(validation, baseline['provenance']['validation_input'])
    with duckdb.connect(config={'threads': 1, 'memory_limit': '1GB'}) as connection:
        connection.read_parquet(str(validation)).create_view('validation')
        groups = validation_groups(records(connection, '''SELECT product_locale,query_id,query,product_id,
            esci_label,split,evaluation_split FROM validation ORDER BY product_locale,query_id,product_id'''))
    if len(groups) != baseline['overall_query_weighted']['queries']:
        raise ValueError('Validation query coverage differs from BM25')
    reference = json.loads((bm25_dir / 'per-query.json').read_text())
    terms = {(r['locale'], r['query_id']): r['analyzed_terms'] for r in reference}
    if evaluate_retrieval(groups, bm25_dir / 'results.tsv', bm25_dir / 'query-status.tsv', terms) != reference:
        raise ValueError('BM25 per-query evidence does not reproduce')
    verified_setup_seconds = time.perf_counter() - started
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dense-exact-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'run'
        stage.mkdir()
        # This is the only input handed to encoding/retrieval: no labels or candidate IDs.
        queries = [{'locale': key[0], 'query_id': key[1], 'query': group['query']}
                   for key, group in sorted(groups.items())]
        with (stage / 'queries.tsv').open('w', encoding='utf-8', newline='') as handle:
            for row in queries:
                handle.write(f"{row['locale']}\t{row['query_id']}\t{encode(row['query'])}\n")
        verify_file(stage / 'queries.tsv', specs['queries.tsv'])
        atomic_json(stage / 'queries.json', queries)
        tick = time.perf_counter()
        encoder = E5Encoder(model_dir, encoding)
        load_seconds = time.perf_counter() - tick
        vectors = np.empty((len(queries), policy['dimensions']), dtype='<f4')
        encoding_by_locale = {}
        positions_by_locale = {loc: [i for i, q in enumerate(queries) if q['locale'] == loc]
                               for loc in sorted(manifest['completion']['counts'])}
        for locale, positions in positions_by_locale.items():
            texts = [queries[i]['query'] for i in positions]
            tick = time.perf_counter()
            lengths = encoder.lengths(texts, kind='query')
            encoder.encode(texts[:encoding['batch_size']], kind='query')
            setup_seconds = time.perf_counter() - tick
            tick = time.perf_counter()
            for start in range(0, len(texts), encoding['batch_size']):
                block = positions[start:start + encoding['batch_size']]
                vectors[block] = encoder.encode(texts[start:start + encoding['batch_size']], kind='query')
            encoding_by_locale[locale] = {'queries': len(texts), 'warmup_and_length_seconds': setup_seconds,
                                           'encode_seconds': time.perf_counter() - tick,
                                           'truncated_queries': sum(n > encoding['max_length'] for n in lengths),
                                           'max_untruncated_tokens': max(lengths)}
            print(f'Encoded {locale}: {len(texts)} queries', flush=True)
        check_vectors(vectors, policy['dimensions'])
        vectors.tofile(stage / 'query-vectors.f32')
        query_norms = np.linalg.norm(vectors, axis=1)
        query_encoding = {'device': encoder.device, 'model_load_seconds': load_seconds,
                          'by_locale': encoding_by_locale,
                          'norm_min': float(query_norms.min()), 'norm_max': float(query_norms.max()),
                          'observed_mps_tensor_bytes': encoder.observed_mps_tensor_bytes,
                          'observed_mps_driver_bytes': encoder.observed_mps_driver_bytes}
        del encoder
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        torch.set_num_threads(policy['cpu_threads'])
        engines = {loc: ExactTopK(vectors[pos], policy['retrieval_cutoff'], policy['query_batch_size'])
                   for loc, pos in positions_by_locale.items()}
        search_seconds = dict.fromkeys(engines, 0.0)
        tick = time.perf_counter()
        for index, (locale, ids, matrix) in enumerate(catalog_shards(catalog_dir, contract, manifest)):
            work = time.perf_counter()
            engines[locale].consume(matrix, ids)
            search_seconds[locale] += time.perf_counter() - work
            if (index + 1) % 32 == 0 or index + 1 == manifest['completion']['shards']:
                print(f'Exact scan {index + 1}/{manifest["completion"]["shards"]} shards; '
                      f'{sum(e.products for e in engines.values()):,} products scored', flush=True)
        scan_wall = time.perf_counter() - tick
        if {loc: e.products for loc, e in engines.items()} != manifest['completion']['counts']:
            raise ValueError('Search did not score every locale product')
        with (stage / 'results.tsv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
            writer.writerow(['product_locale', 'query_id', 'rank', 'product_id', 'score'])
            for locale, engine in engines.items():
                for position, hits in zip(positions_by_locale[locale], engine.results(), strict=True):
                    for rank, (product, score) in enumerate(hits, 1):
                        writer.writerow([locale, queries[position]['query_id'], rank, product, repr(score)])
        per_query = evaluate_dense(groups, stage / 'results.tsv')
        atomic_json(stage / 'per-query.json', per_query)
        overall = summarize_dense(per_query, policy)
        locales = {loc: summarize_dense([r for r in per_query if r['locale'] == loc], policy) for loc in engines}
        comparisons = {'overall_query_weighted': paired_comparison(per_query, reference, policy),
                       'by_locale': {loc: paired_comparison([r for r in per_query if r['locale'] == loc],
                                                          [r for r in reference if r['locale'] == loc], policy)
                                     for loc in engines}}
        artifacts = [{'name': name, 'bytes': (stage / name).stat().st_size, 'sha256': file_digest(stage / name)}
                     for name in ('queries.tsv', 'queries.json', 'query-vectors.f32', 'results.tsv', 'per-query.json')]
        report = {'config': policy, 'partition': 'validation', 'official_test_read': False,
                  'catalog': manifest['completion'], 'overall_query_weighted': overall, 'metrics_by_locale': locales,
                  'locale_macro_mean': {m: fmean(r[m]['mean'] for r in locales.values()) for m in METRICS},
                  'comparison_with_bm25': comparisons,
                  'checks': {'query_input_identical_to_bm25': True, 'all_catalog_shards_verified': True,
                             'every_locale_product_scored_for_every_locale_query': True,
                             'query_coverage_and_label_denominators_match_bm25': True,
                             'score_id_ties_and_full_cutoffs_valid': True, 'no_product_reencoding': True},
                  'query_encoding': query_encoding,
                  'execution': {'verified_setup_seconds': verified_setup_seconds,
                                'search_compute_and_selection_seconds_by_locale': search_seconds,
                                'scan_with_verification_seconds': scan_wall,
                                'scan_verification_and_iteration_seconds': scan_wall - sum(search_seconds.values()),
                                'main_wall_seconds': time.perf_counter() - started,
                                'max_process_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                                'search_device': 'cpu', 'torch_threads': torch.get_num_threads(),
                                'raw_catalog_vector_bytes': manifest['completion']['vector_bytes'],
                                'query_vector_bytes': len(queries) * policy['dimensions'] * 4,
                                'note': 'Single local batch. Main wall includes verification, encoding, scan, metrics/bootstrap and artifact hashing; excludes interpreter/import startup and final report serialization. Scan includes checksums, ID parsing and norms. Process RSS includes model load, encoding and search; observed MPS allocations are not true peaks and not additive. No API latency, QPS or production capacity claim.'},
                  'provenance': {'embedding_report_sha256': file_digest(export_path),
                                 'catalog_contract_sha256': export['provenance']['contract_sha256'],
                                 'catalog_manifest_sha256': export['provenance']['export_manifest_sha256'],
                                 'bm25_report_sha256': file_digest(baseline_path),
                                 'validation_input': baseline['provenance']['validation_input'],
                                 'model_revision': source['revision'],
                                 'source_sha256': {name: file_digest(ROOT / name) for name in SOURCE_PATHS},
                                 'runtime': {'python': platform.python_version(), 'platform': platform.platform(),
                                             'numpy': np.__version__, 'torch': torch.__version__,
                                             'transformers': transformers.__version__, 'duckdb': duckdb.__version__,
                                             'torch_build': torch.__config__.show()}},
                  'artifacts': artifacts,
                  'limits': 'Validation-development and known positives only; unknown relevance is not Irrelevant. Judgment coverage is not precision. Exhaustive float32 search has no ANN candidate loss but still has numerical rounding. No test, training, fusion, quantization, online effect or API capacity result.'}
        atomic_json(stage / 'manifest.json', report)
        stage.rename(output)
        atomic_json(report_path, report)
    print(json.dumps({'overall': overall, 'comparison': comparisons['overall_query_weighted']}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/catalog-dense-exact-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/catalog-dense-exact-v1.json')
    parser.add_argument('--catalog-dir', type=Path, default=ROOT / 'data/processed/catalog-embeddings-v1')
    parser.add_argument('--bm25-dir', type=Path, default=ROOT / 'data/processed/catalog-bm25-title-v1')
    args = parser.parse_args()
    run(args.output_dir, args.report, args.catalog_dir, args.bm25_dir)


if __name__ == '__main__':
    main()
