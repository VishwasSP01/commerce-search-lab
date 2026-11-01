"""Rank-only RRF and known-positive recovery diagnostics; no model dependencies."""

import csv
from fractions import Fraction
import math
from statistics import fmean

from dense_metrics import paired_comparison


def read_rankings(path, expected_keys, window=100):
    rankings, previous = {key: [] for key in expected_keys}, {}
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle, delimiter='\t')
        if reader.fieldnames != ['product_locale', 'query_id', 'rank', 'product_id', 'score']:
            raise ValueError('Unexpected source result schema')
        for row in reader:
            key = (row['product_locale'], int(row['query_id']))
            product, rank, score = row['product_id'], int(row['rank']), float(row['score'])
            if key not in rankings or not product or not math.isfinite(score):
                raise ValueError('Invalid source query/product/score')
            hits = rankings[key]
            if rank != len(hits) + 1 or rank > window or product in hits:
                raise ValueError('Invalid source rank, duplicate or window')
            order = (-score, product)
            if key in previous and order < previous[key]:
                raise ValueError('Invalid source score/ID order')
            hits.append(product)
            previous[key] = order
    # Empty lexical lists remain explicit entries; separate source validation checks coverage.
    return rankings


def rrf_rank(bm25, dense, constant=60, window=100, cutoff=100):
    if type(constant) is not int or constant < 1 or window < 1 or not 1 <= cutoff <= window:
        raise ValueError('Invalid RRF limits')
    scores = {}
    for ranking in (bm25, dense):
        if (len(ranking) > window or len(set(ranking)) != len(ranking)
                or any(not isinstance(p, str) or not p for p in ranking)):
            raise ValueError('Invalid source product list')
        for rank, product in enumerate(ranking, 1):
            scores[product] = scores.get(product, Fraction()) + Fraction(1, constant + rank)
    ordered = sorted(scores, key=lambda product: (-scores[product], product))[:cutoff]
    return [(product, float(scores[product])) for product in ordered]


def compare_rrf(rows, reference, policy):
    # Reuse the frozen paired group bootstrap; give the output neutral reference names.
    compared = paired_comparison(rows, reference, policy)
    return {metric: {'rrf_minus_reference' if name == 'dense_minus_bm25' else name: value
                     for name, value in result.items()} for metric, result in compared.items()}


def recovery_rows(groups, rankings, cohort_keys):
    if set(rankings) != {'bm25', 'dense', 'rrf'} or not set(cohort_keys) <= set(groups):
        raise ValueError('Invalid methods or cohort query identities')
    if any(set(method) != set(groups) for method in rankings.values()):
        raise ValueError('Recovery query coverage mismatch')
    output = []
    for key, group in sorted(groups.items()):
        exact = {p for p, label in group['labels'].items() if label == 'E'}
        candidates = {method: set(lists[key]) for method, lists in rankings.items()}
        if any(len(candidates[m]) != len(rankings[m][key]) for m in rankings):
            raise ValueError('Duplicate recovery result')
        union = candidates['bm25'] | candidates['dense']
        if not candidates['rrf'] <= union:
            raise ValueError('Fusion returned product outside source union')
        found = {method: exact & ids for method, ids in candidates.items()}
        output.append({'locale': key[0], 'query_id': key[1], 'vocabulary_gap_cohort': key in cohort_keys,
                       'known_exact_products': len(exact), 'union_products': len(union),
                       'source_union_known_exact': len(exact & union),
                       'union_known_exact_coverage': len(exact & union) / len(exact) if exact else None,
                       'found_known_exact': {m: len(ids) for m, ids in found.items()},
                       'new_known_exact_vs_bm25': sorted(found['rrf'] - found['bm25']),
                       'lost_known_exact_vs_bm25': sorted(found['bm25'] - found['rrf']),
                       'available_known_exact_dropped': sorted((exact & union) - found['rrf'])})
    return output


def summarize_recovery(rows):
    if not rows:
        raise ValueError('Empty recovery cohort')
    union = [r['union_known_exact_coverage'] for r in rows if r['union_known_exact_coverage'] is not None]
    deltas = [r['found_known_exact']['rrf'] - r['found_known_exact']['bm25'] for r in rows]
    return {'queries': len(rows), 'known_exact_products': sum(r['known_exact_products'] for r in rows),
            'known_exact_found': {m: sum(r['found_known_exact'][m] for r in rows) for m in ('bm25', 'dense', 'rrf')},
            'candidate_union_known_exact': sum(r['source_union_known_exact'] for r in rows),
            'candidate_union_query_mean_known_exact_coverage': fmean(union) if union else None,
            'mean_union_products': fmean(r['union_products'] for r in rows),
            'available_known_exact_dropped_by_rrf': sum(len(r['available_known_exact_dropped']) for r in rows),
            'new_known_exact_vs_bm25': sum(len(r['new_known_exact_vs_bm25']) for r in rows),
            'lost_known_exact_vs_bm25': sum(len(r['lost_known_exact_vs_bm25']) for r in rows),
            'queries_with_new_known_exact': sum(bool(r['new_known_exact_vs_bm25']) for r in rows),
            'queries_with_lost_known_exact': sum(bool(r['lost_known_exact_vs_bm25']) for r in rows),
            'net_recall_improved_queries': sum(d > 0 for d in deltas),
            'net_recall_regressed_queries': sum(d < 0 for d in deltas), 'net_recall_tied_queries': sum(d == 0 for d in deltas),
            'bm25_zero_known_exact_queries': sum(r['found_known_exact']['bm25'] == 0 for r in rows),
            'bm25_zero_to_rrf_positive_queries': sum(r['found_known_exact']['bm25'] == 0 and r['found_known_exact']['rrf'] > 0 for r in rows)}


def nomination(comparisons, rrf_locales, bm25_locales):
    metric = 'known_exact_recall_at_100'
    lower_positive = {name: result[metric]['paired_delta_ci95'][0] > 0 for name, result in comparisons.items()}
    locale_safe = {loc: metrics[metric]['mean'] >= bm25_locales[loc][metric]['mean']
                   for loc, metrics in rrf_locales.items()}
    if set(lower_positive) != {'bm25', 'dense'} or set(locale_safe) != set(bm25_locales):
        raise ValueError('Missing required nomination comparator or locale')
    passed = all(lower_positive.values()) and all(locale_safe.values())
    return {'nominate_rrf_as_offline_reference': passed, 'positive_paired_lower_vs_sources': lower_positive,
            'no_locale_mean_regression_vs_bm25': locale_safe,
            'retained_reference': 'rrf' if passed else 'bm25', 'final_test_or_serving_adoption': False}
