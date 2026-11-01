"""Linear-gain NDCG and paired group bootstrap, independent of retrieval code."""

import math
import random
from statistics import fmean


def rank_candidates(scores: dict[str, float]) -> list[str]:
    if not scores or any(not math.isfinite(value) or value < 0 for value in scores.values()):
        raise ValueError("Expected nonempty finite nonnegative candidate scores")
    return sorted(scores, key=lambda product_id: (-scores[product_id], product_id))


def ndcg_at_k(ranked: list[str], gains: dict[str, float], k: int = 10) -> float:
    if k < 1 or len(ranked) != len(set(ranked)) or set(ranked) != set(gains):
        raise ValueError("Ranking must contain every judged candidate exactly once")
    if any(not math.isfinite(gain) or gain < 0 for gain in gains.values()):
        raise ValueError("Gains must be finite and nonnegative")
    dcg = sum(gains[product] / math.log2(rank + 2) for rank, product in enumerate(ranked[:k]))
    ideal = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(sorted(gains.values(), reverse=True)[:k]))
    return dcg / ideal if ideal else 0.0


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def summarize(rows: list[dict], resamples: int, seed: int) -> dict:
    """Resample normalized query groups, retaining all member query IDs together."""
    if not rows or resamples < 2:
        raise ValueError("Need observations and at least two bootstrap resamples")
    groups = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row)
    grouped = list(groups.values())
    # Sample paired sums/counts, avoiding millions of temporary row dictionaries.
    aggregates = [(sum(r["bm25"] for r in g), sum(r["reference"] for r in g), len(g)) for g in grouped]
    rng = random.Random(seed)
    bm25_samples, delta_samples = [], []
    for _ in range(resamples):
        sample = rng.choices(aggregates, k=len(aggregates))
        count = sum(item[2] for item in sample)
        value = sum(item[0] for item in sample) / count
        reference = sum(item[1] for item in sample) / count
        bm25_samples.append(value)
        delta_samples.append(value - reference)
    bm25 = fmean(row["bm25"] for row in rows)
    reference = fmean(row["reference"] for row in rows)
    return {
        "queries": len(rows), "normalized_query_groups": len(grouped),
        "bm25_ndcg_at_10": bm25, "reference_ndcg_at_10": reference,
        "delta_ndcg_at_10": bm25 - reference,
        "bm25_ci95": [percentile(bm25_samples, .025), percentile(bm25_samples, .975)],
        "paired_delta_ci95": [percentile(delta_samples, .025), percentile(delta_samples, .975)],
        "zero_ideal_dcg_queries": sum(row["zero_ideal"] for row in rows),
        "zero_analyzed_term_queries": sum(row["terms"] == 0 for row in rows),
        "all_zero_score_queries": sum(row["all_zero"] for row in rows),
        "judgments": sum(row["candidates"] for row in rows),
    }
