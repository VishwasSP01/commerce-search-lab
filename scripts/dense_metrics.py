"""Dense retrieval protocol and paired comparisons on incomplete human labels."""

import csv
import math
import random
from statistics import fmean

from audit_esci import normalize_query
from catalog_metrics import summarize_retrieval
from prepare_esci_splits import digest
from ranking_metrics import percentile

METRICS = ('known_exact_recall_at_100', 'judgment_coverage_at_10')


def evaluate_dense(groups, results_path, cutoff=100, coverage_cutoff=10):
    if not groups or cutoff < coverage_cutoff or coverage_cutoff < 1:
        raise ValueError('Invalid evaluation limits')
    results, seen = {key: [] for key in groups}, {key: set() for key in groups}
    with results_path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle, delimiter='\t')
        if reader.fieldnames != ['product_locale', 'query_id', 'rank', 'product_id', 'score']:
            raise ValueError('Unexpected dense result schema')
        for row in reader:
            key = (row['product_locale'], int(row['query_id']))
            product, score, rank = row['product_id'], float(row['score']), int(row['rank'])
            if key not in groups or not product or not math.isfinite(score) or not -1.00001 <= score <= 1.00001:
                raise ValueError('Invalid dense result identity or normalized inner product')
            hits = results[key]
            if rank != len(hits) + 1 or rank > cutoff or product in seen[key]:
                raise ValueError('Invalid rank, duplicate product or exceeded cutoff')
            if hits and (-score, product) < (-hits[-1][1], hits[-1][0]):
                raise ValueError('Invalid score/ID order')
            hits.append((product, score))
            seen[key].add(product)
    evaluated = []
    for key, group in sorted(groups.items()):
        hits, labels = results[key], group['labels']
        # Every real locale has >100 products; dense always returns the full cutoff.
        if len(hits) != cutoff or any(label not in ('E', 'S', 'C', 'I') for label in labels.values()):
            raise ValueError('Missing query/results or invalid human label')
        exact = {p for p, label in labels.items() if label == 'E'}
        found = len(exact & seen[key])
        top = hits[:coverage_cutoff]
        judged = sum(product in labels for product, _ in top)
        evaluated.append({'locale': key[0], 'query_id': key[1],
                          'group': digest([key[0], normalize_query(group['query'])]),
                          'known_exact_products': len(exact), 'retrieved_known_exact_at_100': found,
                          'known_exact_recall_at_100': found / len(exact) if exact else None,
                          'judged_at_10_count': judged, 'returned_at_10': len(top),
                          'judgment_coverage_at_10': judged / len(top),
                          'top_10_slot_fill': len(top) / coverage_cutoff, 'returned_at_100': len(hits)})
    return evaluated


def summarize_dense(rows, policy):
    # Reuse the frozen metric/CI definitions without assigning lexical token counts to dense queries.
    result = summarize_retrieval([dict(row, analyzed_terms=None) for row in rows], policy)
    del result['zero_analyzed_term_queries']
    return result


def paired_comparison(rows, reference, policy):
    keys = lambda items: {(r['locale'], r['query_id']): r for r in items}
    actual, baseline = keys(rows), keys(reference)
    if len(actual) != len(rows) or len(baseline) != len(reference) or actual.keys() != baseline.keys():
        raise ValueError('Paired query coverage mismatch')
    for key, row in actual.items():
        if any(row[field] != baseline[key][field] for field in ('group', 'known_exact_products')):
            raise ValueError('Paired group/label denominator mismatch')
    output = {}
    for metric in METRICS:
        grouped, deltas = {}, []
        for key in sorted(actual):
            a, b = actual[key][metric], baseline[key][metric]
            if (a is None) != (b is None):
                raise ValueError('Paired metric eligibility mismatch')
            if a is None:
                continue
            if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (a, b)):
                raise ValueError('Invalid paired metric')
            delta = a - b
            grouped.setdefault(actual[key]['group'], []).append(delta)
            deltas.append(delta)
        if not deltas:
            output[metric] = {'dense_minus_bm25': None, 'paired_delta_ci95': None, 'defined_queries': 0}
            continue
        aggregates = [(sum(grouped[g]), len(grouped[g])) for g in sorted(grouped)]
        rng = random.Random(policy['confidence']['seed'])
        samples = []
        for _ in range(policy['confidence']['resamples']):
            sample = rng.choices(aggregates, k=len(aggregates))
            samples.append(sum(s for s, _ in sample) / sum(n for _, n in sample))
        output[metric] = {'dense_minus_bm25': fmean(deltas),
                          'paired_delta_ci95': [percentile(samples, .025), percentile(samples, .975)],
                          'defined_queries': len(deltas), 'normalized_query_groups': len(grouped),
                          'improved_queries': sum(d > 0 for d in deltas),
                          'regressed_queries': sum(d < 0 for d in deltas), 'tied_queries': sum(d == 0 for d in deltas)}
    return output
