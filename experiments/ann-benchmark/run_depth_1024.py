"""One additional HNSW search-depth point; immutable graphs and frozen workers."""

import argparse
import json
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import faiss
import numpy as np

from ann_core import agreement_rows, choose_candidate
from benchmark import (BM25_DIR, LOCALES, combined_results, fused_results, inputs, latency_summary,
                       paired, summary, verify_sources, worker)
from catalog_metrics import mean_interval
from dense_metrics import evaluate_dense
from download_esci import verify_file
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest
from rrf_fusion import read_rankings
from worker import verify_index

CONFIG = ROOT / 'config/ann-depth-1024-v1.json'
SOURCES = ('config/ann-depth-1024-v1.json', 'docs/decisions/0010-ann-depth-1024.md',
           'experiments/ann-benchmark/run_depth_1024.py', 'experiments/ann-benchmark/tests/test_depth_1024.py')
DEPTHS = (512, 1024)


def validate_policy(policy):
    if (policy['partition'] != 'validation' or policy['candidate_efSearch'] != 1024
            or policy['timing_control_efSearch'] != 512
            or policy['confidence'] != {'resamples': 2000, 'seed': 20261101}):
        raise ValueError('Only the predeclared depth-1024 experiment is supported')


def search_only(contract_path, index_dir, locale, depth, output):
    if depth not in DEPTHS:
        raise ValueError('Only the control and one additional depth are permitted')
    worker('search', contract_path, index_dir, locale, f'hnsw-{depth}', output)


def transitions(control, candidate, reference):
    if set(control) != set(candidate) or set(control) != set(reference):
        raise ValueError('Transition query coverage mismatch')
    rows = []
    for key in sorted(reference):
        for method in (control, candidate, reference):
            if len(method[key]) != 100 or len(set(method[key])) != 100:
                raise ValueError('Transitions require complete unique top-100 lists')
        exact, before, after = set(reference[key]), set(control[key]), set(candidate[key])
        missing = exact - before
        recovered, remaining, new = missing & after, missing - after, (exact & before) - after
        if len(missing) != len(recovered) + len(remaining) or len(exact - after) != len(remaining) + len(new):
            raise ValueError('Transition conservation failure')
        rows.append({'locale': key[0], 'query_id': key[1], 'missing_at_512': len(missing),
                     'recovered_at_1024': len(recovered), 'still_missing': len(remaining),
                     'newly_missing': len(new), 'missing_at_1024': len(exact - after)})
    return rows


def check_control(directory, original):
    for name in ('dense.tsv', 'hybrid.tsv', 'per-query.json'):
        if file_digest(directory / name) != file_digest(original / name):
            raise ValueError('Contemporaneous depth-512 control changed')


def verify_timing(passes, expected_queries, policy):
    if len(passes) != policy['timed_passes']:
        raise ValueError('Incomplete timing passes')
    all_values = []
    for number, row in enumerate(passes):
        order = list(expected_queries)
        random.Random(policy['timing_order_seed'] + number).shuffle(order)
        values, calls = row['milliseconds'], row['native_calls']
        if (row['pass'] != number or row['query_order'] != order or len(values) != len(order)
                or len(calls) != len(order) or any(not math.isfinite(v) or v <= 0 for v in values)
                or any(type(c) is not int or c < 1 for c in calls)):
            raise ValueError('Timing identity, order, coverage or numeric value mismatch')
        all_values.extend(values)
    return all_values


def run(args):
    started = time.perf_counter()
    if args.output_dir.exists() or args.report.exists():
        raise FileExistsError('Use fresh evidence paths')
    policy = json.loads(CONFIG.read_text())
    validate_policy(policy)
    prior_path, diagnosis_path = ROOT / 'reports/catalog-ann-v1.json', ROOT / 'reports/ann-miss-analysis-v1.json'
    prior, diagnosis = json.loads(prior_path.read_text()), json.loads(diagnosis_path.read_text())
    verify_sources(prior['contract']['source_sha256'])
    verify_sources(diagnosis['provenance']['source_sha256'])
    if (diagnosis['provenance']['ann_report_sha256'] != file_digest(prior_path)
            or prior['config'] != json.loads((ROOT / 'config/catalog-ann-v1.json').read_text())
            or faiss.__version__ != prior['config']['faiss_version']):
        raise ValueError('Original experiment/library/diagnostic lineage changed')
    _, groups, _ = inputs(prior['config'])
    contract_path = args.ann_dir / 'contract.json'
    if json.loads(contract_path.read_text()) != prior['contract']:
        raise ValueError('Original build contract changed')
    for spec in prior['artifacts']:
        verify_file(args.ann_dir / spec['name'], spec)
    for loc in LOCALES:
        if verify_index(args.ann_dir / 'indices' / loc, prior['contract'], loc) != prior['builds'][loc]:
            raise ValueError('Original graph build changed')
    frozen_rows = {m: json.loads((args.ann_dir / m / 'per-query.json').read_text()) for m in ('flat', 'hnsw-512')}
    for method in frozen_rows:
        for track in ('dense', 'hybrid'):
            if evaluate_dense(groups, args.ann_dir / method / f'{track}.tsv') != frozen_rows[method][track]:
                raise ValueError('Frozen control metrics do not reproduce')
    experiment = {'policy': policy, 'original_build_contract_sha256': file_digest(contract_path),
                  'prior_report_sha256': file_digest(prior_path), 'diagnosis_sha256': file_digest(diagnosis_path),
                  'source_sha256': {p: file_digest(ROOT / p) for p in SOURCES},
                  'worker_order': [1024, 512] if args.reverse_order else [512, 1024]}
    args.output_dir.mkdir(parents=True)
    atomic_json(args.output_dir / 'experiment.json', experiment)
    for loc in LOCALES:
        for depth in experiment['worker_order']:
            search_only(contract_path, args.ann_dir / 'indices' / loc, loc, depth,
                        args.output_dir / 'workers' / f'hnsw-{depth}' / loc)
    bm25 = read_rankings(BM25_DIR / 'results.tsv', groups)
    exact = read_rankings(args.ann_dir / 'flat/dense.tsv', groups)
    group_ids = {(r['locale'], r['query_id']): r['group'] for r in frozen_rows['flat']['dense']}
    metrics, timing, agreement, rankings, all_rows, artifacts = {}, {}, {}, {}, {}, []
    query_records = json.loads((ROOT / 'data/processed/catalog-dense-exact-v1/queries.json').read_text())
    for depth in DEPTHS:
        method = f'hnsw-{depth}'
        directory = args.output_dir / method
        directory.mkdir()
        combined_results([args.output_dir / 'workers' / method / loc / 'results.tsv' for loc in LOCALES], directory / 'dense.tsv')
        rankings[depth] = read_rankings(directory / 'dense.tsv', groups)
        fused_results(bm25, rankings[depth], directory / 'hybrid.tsv')
        tracks = {t: evaluate_dense(groups, directory / f'{t}.tsv') for t in ('dense', 'hybrid')}
        all_rows[depth] = tracks
        atomic_json(directory / 'per-query.json', tracks)
        if depth == 512:
            check_control(directory, args.ann_dir / 'hnsw-512')
        metrics[method] = {t: summary(rows, policy) for t, rows in tracks.items()}
        metrics[method]['dense_vs_flat'] = paired(tracks['dense'], frozen_rows['flat']['dense'], policy)
        metrics[method]['hybrid_vs_flat'] = paired(tracks['hybrid'], frozen_rows['flat']['hybrid'], policy)
        rows = [dict(r, group=group_ids[(r['locale'], r['query_id'])]) for r in agreement_rows(rankings[depth], exact)]
        agreement[method] = {'overall': mean_interval(rows, 'agreement_at_100', 2000, 20261101),
                             'by_locale': {loc: mean_interval([r for r in rows if r['locale'] == loc], 'agreement_at_100', 2000, 20261101) for loc in LOCALES}}
        locales, pooled = {}, []
        for loc in LOCALES:
            worker_dir = args.output_dir / 'workers' / method / loc
            session = json.loads((worker_dir / 'session.json').read_text())
            passes = json.loads((worker_dir / 'latencies.json').read_text())
            expected_queries = [r['query_id'] for r in query_records if r['locale'] == loc]
            if (session['locale'] != loc or session['method'] != method or session['threads'] != 1
                    or session['queries'] != len(expected_queries) or not session['identical_rankings_all_passes']
                    or session['index_build_sha256'] != file_digest(args.ann_dir / 'indices' / loc / 'build.json')
                    or session['result_sha256'] != file_digest(worker_dir / 'results.tsv')):
                raise ValueError('Query-worker provenance mismatch')
            values = verify_timing(passes, expected_queries, prior['config'])
            pooled.extend(values)
            locales[loc] = {'pooled_three_passes': latency_summary(values),
                             'by_pass': [latency_summary(p['milliseconds']) for p in passes], 'session': session,
                             'raw_timing_sha256': file_digest(worker_dir / 'latencies.json')}
        timing[method] = {'pooled_three_passes': latency_summary(pooled), 'by_locale': locales}
        for name in ('dense.tsv', 'hybrid.tsv', 'per-query.json'):
            path = directory / name
            artifacts.append({'name': str(path.relative_to(args.output_dir)), 'bytes': path.stat().st_size, 'sha256': file_digest(path)})
    metrics['hnsw-1024']['dense_vs_512'] = paired(all_rows[1024]['dense'], all_rows[512]['dense'], policy)
    metrics['hnsw-1024']['hybrid_vs_512'] = paired(all_rows[1024]['hybrid'], all_rows[512]['hybrid'], policy)
    changes = transitions(rankings[512], rankings[1024], exact)
    atomic_json(args.output_dir / 'transitions.json', changes)
    path = args.output_dir / 'transitions.json'
    artifacts.append({'name': 'transitions.json', 'bytes': path.stat().st_size, 'sha256': file_digest(path)})
    def transition_summary(rows):
        return {'queries': len(rows), **{k: sum(r[k] for r in rows) for k in
                                       ('missing_at_512', 'recovered_at_1024', 'still_missing', 'newly_missing', 'missing_at_1024')}}
    candidate, comparison = agreement['hnsw-1024'], metrics['hnsw-1024']['hybrid_vs_flat']
    point = {'overall_agreement': candidate['overall']['mean'],
             'locale_agreement': {loc: a['mean'] for loc, a in candidate['by_locale'].items()},
             'hybrid_paired_lower': comparison['overall']['known_exact_recall_at_100']['paired_delta_ci95'][0],
             'locale_hybrid_delta': {loc: c['known_exact_recall_at_100']['delta'] for loc, c in comparison['by_locale'].items()}}
    for loc in LOCALES:
        verify_index(args.ann_dir / 'indices' / loc, prior['contract'], loc)
    for spec in prior['artifacts']:
        verify_file(args.ann_dir / spec['name'], spec)
    if file_digest(contract_path) != experiment['original_build_contract_sha256']:
        raise ValueError('Build contract mutated during experiment')
    report = {'config': policy, 'partition': 'validation', 'official_test_read': False,
              'experiment': experiment, 'agreement': agreement, 'metrics': metrics, 'timing': timing,
              'neighbor_transitions': {'overall': transition_summary(changes),
                                       'by_locale': {loc: transition_summary([r for r in changes if r['locale'] == loc]) for loc in LOCALES}},
              'selection': choose_candidate({1024: point}), 'selection_inputs': point,
              'checks': {'no_graph_build_or_encoding': True, 'depth_512_control_artifacts_identical': True,
                         'all_three_pass_rankings_identical': True, 'exact_query_group_pairing': True,
                         'raw_timing_identity_and_order_verified': True, 'input_results_indexes_and_contract_unchanged': True},
              'index_builds': prior['builds'], 'artifacts': artifacts, 'main_wall_seconds': time.perf_counter() - started,
              'limits': 'One additional validation search-depth point chosen after a published miss diagnosis; prior grid/decision preserved. Same frozen graphs, new native query timings. Saved matched Flat timing is historical and not remeasured. Agreement is not human recall; unknown judgments stay unknown. Warm singleton timings exclude encoding/API/concurrency; RSS is one-locale worker memory. Nomination is not final-test or production adoption.'}
    atomic_json(args.output_dir / 'manifest.json', report)
    atomic_json(args.report, report)
    print(json.dumps({'selection': report['selection'], 'agreement': candidate,
                      'hybrid_recall': metrics['hnsw-1024']['hybrid']['overall']['known_exact_recall_at_100'],
                      'native_timing': {m: t['pooled_three_passes'] for m, t in timing.items()}}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ann-dir', type=Path, default=ROOT / 'data/processed/catalog-ann-v1')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/processed/ann-depth-1024-v1')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/ann-depth-1024-v1.json')
    parser.add_argument('--reverse-order', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
