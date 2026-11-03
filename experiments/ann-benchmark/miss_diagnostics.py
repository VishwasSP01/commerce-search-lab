"""Conserved replacement pairing and relevance diagnostics on saved ANN lists."""

from collections import Counter, defaultdict
import math
from statistics import fmean

import numpy as np

from ann_core import valid_vectors

CATEGORIES = ('identical_vector', 'score_equivalent', 'strict_score_loss', 'float64_order_reversal')


def diagnose(exact, approximate, vectors, query, labels, tolerance=1e-10, cutoff=100):
    union = sorted(set(exact) | set(approximate))
    if (len(exact) != cutoff or len(approximate) != cutoff or len(set(exact)) != cutoff
            or len(set(approximate)) != cutoff or set(vectors) != set(union)
            or any(not isinstance(p, str) or not p for p in union)
            or not math.isfinite(tolerance) or not 0 < tolerance < 1e-6):
        raise ValueError('Invalid ranking, union identity or score tolerance')
    query = np.asarray(query)
    matrix = np.stack([vectors[p] for p in union])
    if query.ndim != 1:
        raise ValueError('Query must be a vector')
    valid_vectors(matrix, len(query))
    valid_vectors(query[None, :], len(query))
    if any(label not in ('E', 'S', 'C', 'I') for label in labels.values()):
        raise ValueError('Invalid human label')
    computed = np.einsum('ij,j->i', matrix.astype(np.float64), query.astype(np.float64), optimize=False)
    scores = dict(zip(union, computed.tolist(), strict=True))
    missing, extra = set(exact) - set(approximate), set(approximate) - set(exact)
    original_missing, original_extra = set(missing), set(extra)
    pairs = []

    def emit(a, b, category):
        pairs.append({'missing_product_id': a, 'replacement_product_id': b, 'category': category,
                      'missing_score64': scores[a], 'replacement_score64': scores[b],
                      'score_gap64': scores[a] - scores[b],
                      'missing_label': labels.get(a), 'replacement_label': labels.get(b)})
        missing.remove(a)
        extra.remove(b)

    left, right = defaultdict(list), defaultdict(list)
    for p in sorted(missing):
        left[vectors[p].tobytes()].append(p)
    for p in sorted(extra):
        right[vectors[p].tobytes()].append(p)
    for key in sorted(left.keys() & right.keys()):
        for a, b in zip(left[key], right[key]):
            emit(a, b, 'identical_vector')
    a = sorted(missing, key=lambda p: (scores[p], p))
    b = sorted(extra, key=lambda p: (scores[p], p))
    i = j = 0
    while i < len(a) and j < len(b):
        difference = scores[a[i]] - scores[b[j]]
        if difference < -tolerance:
            i += 1
        elif difference > tolerance:
            j += 1
        else:
            emit(a[i], b[j], 'score_equivalent')
            i += 1
            j += 1
    for a, b in zip(sorted(missing, key=lambda p: (-scores[p], p)),
                    sorted(extra, key=lambda p: (-scores[p], p)), strict=True):
        gap = scores[a] - scores[b]
        if abs(gap) <= tolerance:
            raise ValueError('Unmatched score-equivalent pair remains')
        emit(a, b, 'strict_score_loss' if gap > 0 else 'float64_order_reversal')
    pairs.sort(key=lambda p: (p['missing_product_id'], p['replacement_product_id']))
    if (missing or extra or {p['missing_product_id'] for p in pairs} != original_missing
            or {p['replacement_product_id'] for p in pairs} != original_extra or len(pairs) != len(original_missing)):
        raise ValueError('Pair conservation failure')
    counts = dict.fromkeys(CATEGORIES, 0)
    counts.update(Counter(p['category'] for p in pairs))
    credits = counts['identical_vector'] + counts['score_equivalent']
    return {'missing_neighbors': len(pairs), 'strict_agreement': (cutoff - len(pairs)) / cutoff,
            'equivalent_credit_agreement': (cutoff - len(pairs) + credits) / cutoff,
            'category_counts': counts,
            'lost_known_exact_by_category': {c: sum(p['category'] == c and p['missing_label'] == 'E' for p in pairs) for c in CATEGORIES},
            'new_known_exact': sum(p['replacement_label'] == 'E' for p in pairs),
            'lost_known_exact': sum(p['missing_label'] == 'E' for p in pairs), 'pairs': pairs}


def aggregate(rows):
    if not rows:
        raise ValueError('No diagnostic observations')
    counts = {c: sum(r['category_counts'][c] for r in rows) for c in CATEGORIES}
    gaps = [p['score_gap64'] for r in rows for p in r['pairs'] if p['category'] == 'strict_score_loss']
    return {'queries': len(rows), 'missing_neighbors': sum(r['missing_neighbors'] for r in rows),
            'strict_agreement': fmean(r['strict_agreement'] for r in rows),
            'equivalent_credit_agreement': fmean(r['equivalent_credit_agreement'] for r in rows),
            'category_counts': counts,
            'queries_with_category': {c: sum(r['category_counts'][c] > 0 for r in rows) for c in CATEGORIES},
            'lost_known_exact_by_category': {c: sum(r['lost_known_exact_by_category'][c] for r in rows) for c in CATEGORIES},
            'lost_known_exact': sum(r['lost_known_exact'] for r in rows),
            'new_known_exact': sum(r['new_known_exact'] for r in rows),
            'dense_net_improved_queries': sum(r['new_known_exact'] > r['lost_known_exact'] for r in rows),
            'dense_net_regressed_queries': sum(r['new_known_exact'] < r['lost_known_exact'] for r in rows),
            'hybrid_net_improved_queries': sum(r['hybrid_recall_delta'] > 0 for r in rows),
            'hybrid_net_regressed_queries': sum(r['hybrid_recall_delta'] < 0 for r in rows),
            'hybrid_regressions_with_no_dense_known_exact_loss': sum(r['hybrid_recall_delta'] < 0 and r['lost_known_exact'] == 0 for r in rows),
            'hybrid_lost_known_exact': sum(r['hybrid_lost_known_exact'] for r in rows),
            'hybrid_new_known_exact': sum(r['hybrid_new_known_exact'] for r in rows),
            'still_missing_at_shallower_depth': {str(ef): sum(r['still_missing_at_shallower_depth'][str(ef)] for r in rows) for ef in (128, 256)},
            'strict_pair_gap64': {'count': len(gaps), 'mean': fmean(gaps),
                                  'p50': float(np.quantile(gaps, .5)), 'p95': float(np.quantile(gaps, .95)),
                                  'max': max(gaps)} if gaps else {'count': 0}}


def select_cases(rows):
    selected = {}
    rules = [('strict_score_loss', lambda r: r['category_counts']['strict_score_loss']),
             ('hybrid_recall_loss', lambda r: -r['hybrid_recall_delta']),
             ('identical_vector', lambda r: r['category_counts']['identical_vector'])]
    for locale in sorted({r['locale'] for r in rows}):
        local = [r for r in rows if r['locale'] == locale]
        for reason, value in rules:
            eligible = [r for r in local if value(r) > 0]
            if eligible:
                row = min(eligible, key=lambda r: (-value(r), r['query_id']))
                key = (locale, row['query_id'])
                selected.setdefault(key, {'locale': locale, 'query_id': row['query_id'], 'reasons': []})['reasons'].append(reason)
    return [selected[key] for key in sorted(selected)]


def representative_pairs(row, reasons):
    ordered = sorted(row['pairs'], key=lambda p: (p['missing_label'] != 'E', p['category'] != 'strict_score_loss',
                                                -p['score_gap64'], p['missing_product_id'], p['replacement_product_id']))
    if 'identical_vector' in reasons:
        identical = sorted((p for p in row['pairs'] if p['category'] == 'identical_vector'),
                           key=lambda p: (p['missing_product_id'], p['replacement_product_id']))
        ordered = identical[:1] + [p for p in ordered if p not in identical[:1]]
    return ordered[:2]
