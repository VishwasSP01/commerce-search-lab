"""One predeclared RRF experiment on frozen BM25 and exact-dense validation lists."""

import argparse
import csv
import json
from pathlib import Path
import platform
import resource
from statistics import fmean
import sys
import tempfile
import time

import duckdb

from audit_esci import records
from catalog_metrics import evaluate_retrieval
from dense_metrics import METRICS, evaluate_dense, summarize_dense
from download_esci import ROOT, verify_file
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest
from rrf_fusion import compare_rrf, nomination, read_rankings, recovery_rows, rrf_rank, summarize_recovery
from run_bm25 import validation_groups

CONFIG = ROOT / 'config/catalog-rrf-v1.json'
SOURCES = ('config/catalog-rrf-v1.json', 'docs/decisions/0007-rrf-hybrid-retrieval.md',
           'scripts/run_rrf.py', 'scripts/rrf_fusion.py', 'tests/test_rrf_fusion.py')


def verify_sources(sources):
    for name, sha in sources.items():
        if file_digest(ROOT / name) != sha:
            raise ValueError(f'Frozen source changed: {name}')


def frozen_cohort(cases, keys, expected=78):
    identities = [(r['locale'], r['query_id']) for r in cases]
    if len(identities) != len(set(identities)) or set(identities) != set(keys):
        raise ValueError('Original diagnostic query coverage mismatch')
    cohort = {(r['locale'], r['query_id']) for r in cases if r['zero_kind'] == 'no_title_token_overlap'}
    if len(cohort) != expected or any(not r['all_zero'] for r in cases if (r['locale'], r['query_id']) in cohort):
        raise ValueError('Original vocabulary-gap cohort changed')
    return cohort


def measure(rrf_rows, bm25_rows, dense_rows, policy):
    return {'metrics': {'bm25': summarize_dense(bm25_rows, policy), 'dense': summarize_dense(dense_rows, policy),
                        'rrf': summarize_dense(rrf_rows, policy)},
            'paired_rrf_minus_reference': {'bm25': compare_rrf(rrf_rows, bm25_rows, policy),
                                           'dense': compare_rrf(rrf_rows, dense_rows, policy)}}


def run(output, report_path, bm25_dir, dense_dir, diagnostic_dir):
    started = time.perf_counter()
    if output.exists() or report_path.exists():
        raise FileExistsError('Use fresh output and report paths to preserve evidence')
    policy = json.loads(CONFIG.read_text())
    if (policy['partition'] != 'validation' or policy['rank_constant'] != 60 or policy['source_window'] != 100
            or policy['retrieval_cutoff'] != 100 or policy['coverage_cutoff'] != 10 or policy['weights'] != [1, 1]
            or policy['sources'] != ['catalog-bm25-title-v1', 'catalog-dense-exact-v1']
            or policy['confidence'] != {'resamples': 2000, 'seed': 20261101}):
        raise ValueError('Only the predeclared RRF protocol is supported')
    paths = {m: ROOT / f'reports/{name}.json' for m, name in
             [('bm25', 'catalog-bm25-title-v1'), ('dense', 'catalog-dense-exact-v1'), ('diagnostic', 'bm25-error-analysis-v1')]}
    reports = {m: json.loads(path.read_text()) for m, path in paths.items()}
    bm25, dense, diagnostic = (reports[m] for m in ('bm25', 'dense', 'diagnostic'))
    for report in (bm25, dense):
        verify_sources(report['provenance']['source_sha256'])
    verify_sources(bm25['provenance']['frozen_source_sha256'])
    if dense['provenance']['bm25_report_sha256'] != file_digest(paths['bm25']):
        raise ValueError('Dense/BM25 report lineage changed')
    specs = {m: {a['name']: a for a in report['artifacts']} for m, report in reports.items()}
    for method, directory, names in [('bm25', bm25_dir, ('queries.tsv', 'results.tsv', 'query-status.tsv', 'per-query.json')),
                                      ('dense', dense_dir, ('queries.tsv', 'results.tsv', 'per-query.json')),
                                      ('diagnostic', diagnostic_dir, ('per-query-diagnostics.json',))]:
        for name in names:
            verify_file(directory / name, specs[method][name])
    validation = ROOT / 'data/processed/esci-ranking-v1/validation.parquet'
    verify_file(validation, bm25['provenance']['validation_input'])
    if (dense['provenance']['validation_input'] != bm25['provenance']['validation_input']
            or diagnostic['provenance']['input_validation_sha256'] != file_digest(validation)
            or diagnostic['provenance']['baseline_report_sha256'] != file_digest(ROOT / 'reports/bm25-title-v1.json')
            or diagnostic['provenance']['analysis_script_sha256'] != file_digest(ROOT / 'scripts/analyze_bm25_errors.py')
            or diagnostic['provenance']['policy_sha256'] != file_digest(ROOT / 'config/bm25-error-analysis-v1.json')
            or file_digest(bm25_dir / 'queries.tsv') != file_digest(dense_dir / 'queries.tsv')):
        raise ValueError('Validation/query/diagnostic provenance changed')
    with duckdb.connect(config={'threads': 1, 'memory_limit': '1GB'}) as connection:
        connection.read_parquet(str(validation)).create_view('validation')
        groups = validation_groups(records(connection, '''SELECT product_locale,query_id,query,product_id,
            esci_label,split,evaluation_split FROM validation ORDER BY product_locale,query_id,product_id'''))
    bm25_rows, dense_rows = (json.loads((directory / 'per-query.json').read_text()) for directory in (bm25_dir, dense_dir))
    terms = {(r['locale'], r['query_id']): r['analyzed_terms'] for r in bm25_rows}
    if (evaluate_retrieval(groups, bm25_dir / 'results.tsv', bm25_dir / 'query-status.tsv', terms) != bm25_rows
            or evaluate_dense(groups, dense_dir / 'results.tsv') != dense_rows):
        raise ValueError('Saved source per-query metrics do not reproduce')
    cohort = frozen_cohort(json.loads((diagnostic_dir / 'per-query-diagnostics.json').read_text()), groups)
    source_rankings = {m: read_rankings(directory / 'results.tsv', groups, policy['source_window'])
                       for m, directory in [('bm25', bm25_dir), ('dense', dense_dir)]}
    verification_seconds = time.perf_counter() - started
    tick = time.perf_counter()
    fused = {key: rrf_rank(source_rankings['bm25'][key], source_rankings['dense'][key], policy['rank_constant'],
                           policy['source_window'], policy['retrieval_cutoff']) for key in sorted(groups)}
    fusion_seconds = time.perf_counter() - tick
    if any(len(hits) != 100 for hits in fused.values()):
        raise ValueError('Fusion must return a full cutoff for every real query')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.catalog-rrf-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'run'
        stage.mkdir()
        with (stage / 'results.tsv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
            writer.writerow(['product_locale', 'query_id', 'rank', 'product_id', 'score'])
            for (locale, query_id), hits in fused.items():
                for rank, (product, score) in enumerate(hits, 1):
                    writer.writerow([locale, query_id, rank, product, repr(score)])
        # Share frozen rank/metric arithmetic. RRF scores are rank-derived, not cosine;
        # their range (0, 2/61] also satisfies this parser's finite-score bound.
        rrf_rows = evaluate_dense(groups, stage / 'results.tsv')
        atomic_json(stage / 'per-query.json', rrf_rows)
        rankings = dict(source_rankings, rrf={key: [p for p, _ in hits] for key, hits in fused.items()})
        recoveries = recovery_rows(groups, rankings, cohort)
        atomic_json(stage / 'recovery-per-query.json', recoveries)
        overall = measure(rrf_rows, bm25_rows, dense_rows, policy)
        locales = sorted({key[0] for key in groups})
        by_locale = {loc: measure(*[[r for r in rows if r['locale'] == loc]
                                          for rows in (rrf_rows, bm25_rows, dense_rows)], policy) for loc in locales}
        cohort_rows = [[r for r in rows if (r['locale'], r['query_id']) in cohort]
                       for rows in (rrf_rows, bm25_rows, dense_rows)]
        cohort_report = measure(*cohort_rows, policy)
        cohort_report['by_locale'] = {loc: measure(*[[r for r in rows if r['locale'] == loc]
                                                            for rows in cohort_rows], policy)
                                     for loc in locales if any(r['locale'] == loc for r in cohort_rows[0])}
        cohort_report['recovery'] = summarize_recovery([r for r in recoveries if r['vocabulary_gap_cohort']])
        cohort_report['recovery_by_locale'] = {loc: summarize_recovery([r for r in recoveries if r['vocabulary_gap_cohort'] and r['locale'] == loc])
                                              for loc in cohort_report['by_locale']}
        atomic_json(stage / 'vocabulary-gap-cohort.json', {'membership': [list(key) for key in sorted(cohort)],
                                                        'evaluation': cohort_report,
                                                        'per_query': [r for r in recoveries if r['vocabulary_gap_cohort']]})
        decision = nomination(overall['paired_rrf_minus_reference'], {loc: r['metrics']['rrf'] for loc, r in by_locale.items()},
                              {loc: r['metrics']['bm25'] for loc, r in by_locale.items()})
        report = {'config': policy, 'official_test_read': False, 'partition': 'validation',
                  'catalog_scope': dense['catalog']['counts'],
                  'overall_query_weighted': overall, 'by_locale': by_locale,
                  'locale_macro_mean': {method: {metric: fmean(r['metrics'][method][metric]['mean'] for r in by_locale.values())
                                                 for metric in METRICS} for method in ('bm25', 'dense', 'rrf')},
                  'recovery_and_union_diagnostic': {'overall': summarize_recovery(recoveries),
                                                   'by_locale': {loc: summarize_recovery([r for r in recoveries if r['locale'] == loc]) for loc in locales},
                                                   'note': 'Union has up to 200 products, not a served top-100 method. Addition/loss query flags can overlap; net changes are separate.'},
                  'fixed_vocabulary_gap_cohort': cohort_report, 'decision': decision,
                  'checks': {'source_artifact_hashes_verified': True, 'source_per_query_metrics_reproduced': True,
                             'identical_validation_query_inputs': True, 'exact_query_group_and_denominator_pairing': True,
                             'cohort_fixed_at_original_78_queries': True, 'all_fused_products_in_source_union': True,
                             'full_cutoffs_and_score_id_order': True, 'no_new_retrieval_or_encoding': True},
                  'execution': {'input_verification_seconds': verification_seconds, 'fusion_only_seconds': fusion_seconds,
                                'main_wall_seconds': time.perf_counter() - started,
                                'max_process_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                                'note': 'Cached-list offline fusion. Main wall includes input verification, fusion, metrics/bootstrap and diagnostics; excludes import startup and final serialization/artifact hashing. RSS covers input/evaluation state. Neither end-to-end hybrid latency nor serving memory/QPS.'},
                  'provenance': {'input_report_sha256': {m: file_digest(path) for m, path in paths.items()},
                                 'input_artifacts': {m: [specs[m][name] for name in names] for m, names in
                                                     [('bm25', ('queries.tsv', 'results.tsv', 'query-status.tsv', 'per-query.json')),
                                                      ('dense', ('queries.tsv', 'results.tsv', 'per-query.json')),
                                                      ('diagnostic', ('per-query-diagnostics.json',))]},
                                 'validation_input': bm25['provenance']['validation_input'],
                                 'source_sha256': {name: file_digest(ROOT / name) for name in SOURCES},
                                 'python': platform.python_version(), 'duckdb': duckdb.__version__, 'platform': platform.platform()},
                  'artifacts': [{'name': name, 'bytes': (stage / name).stat().st_size, 'sha256': file_digest(stage / name)}
                                for name in ('results.tsv', 'per-query.json', 'recovery-per-query.json', 'vocabulary-gap-cohort.json')],
                  'limits': 'Repeatedly inspected validation and incomplete human judgments only. Unknown relevance stays unknown; coverage is not precision. No adjusted confirmatory significance, final-test release, complete query fix, cross-language, ANN, deployment or online claim.'}
        atomic_json(stage / 'manifest.json', report)
        stage.rename(output)
        atomic_json(report_path, report)
    print(json.dumps({'recall': {m: r['known_exact_recall_at_100'] for m, r in overall['metrics'].items()},
                      'comparisons': overall['paired_rrf_minus_reference'], 'decision': decision,
                      'cohort_recovery': cohort_report['recovery']}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/catalog-rrf-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/catalog-rrf-v1.json')
    parser.add_argument('--bm25-dir', type=Path, default=ROOT / 'data/processed/catalog-bm25-title-v1')
    parser.add_argument('--dense-dir', type=Path, default=ROOT / 'data/processed/catalog-dense-exact-v1')
    parser.add_argument('--diagnostic-dir', type=Path, default=ROOT / 'data/processed/bm25-error-analysis-v1')
    args = parser.parse_args()
    run(args.output_dir, args.report, args.bm25_dir, args.dense_dir, args.diagnostic_dir)


if __name__ == '__main__':
    main()
