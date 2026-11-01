"""Incomplete-judgment retrieval metrics; unknown labels remain unknown."""

import csv
import math
from pathlib import Path
import random
from statistics import fmean

from audit_esci import normalize_query
from prepare_esci_splits import digest
from ranking_metrics import percentile


def evaluate_retrieval(groups: dict, results_path: Path, status_path: Path, expected_terms: dict,
                       cutoff: int = 100, coverage_cutoff: int = 10) -> list[dict]:
    if cutoff < coverage_cutoff or coverage_cutoff < 1 or set(expected_terms) != set(groups):
        raise ValueError("Invalid cutoffs or query analysis coverage")
    statuses = {}
    with status_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["product_locale", "query_id", "analyzed_terms", "returned"]:
            raise ValueError("Unexpected status schema")
        for row in reader:
            key = (row["product_locale"], int(row["query_id"]))
            terms, returned = int(row["analyzed_terms"]), int(row["returned"])
            if key not in groups or key in statuses or not 0 <= returned <= cutoff or terms != expected_terms[key]:
                raise ValueError("Unexpected, duplicated or inconsistent query status")
            if terms == 0 and returned:
                raise ValueError("Empty analysis cannot return lexical matches")
            statuses[key] = (terms, returned)
    if statuses.keys() != groups.keys():
        raise ValueError("Missing query status, including zero-result queries")
    results = {key: [] for key in groups}
    identities = {key: set() for key in groups}
    with results_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["product_locale", "query_id", "rank", "product_id", "score"]:
            raise ValueError("Unexpected retrieval schema")
        for row in reader:
            key = (row["product_locale"], int(row["query_id"]))
            rank, product, score = int(row["rank"]), row["product_id"], float(row["score"])
            if key not in groups or not product or not math.isfinite(score) or score < 0:
                raise ValueError("Unexpected or invalid retrieval result")
            hits = results[key]
            if rank != len(hits) + 1 or rank > cutoff or product in identities[key]:
                raise ValueError("Noncontiguous rank, duplicated product or exceeded cutoff")
            if hits and (-score, product) < (-hits[-1][1], hits[-1][0]):
                raise ValueError("Retrieval order violates score/ID tie policy")
            hits.append((product, score))
            identities[key].add(product)
    evaluated = []
    for key, group in sorted(groups.items()):
        hits = results[key]
        terms, returned = statuses[key]
        if len(hits) != returned:
            raise ValueError("Status/result count mismatch")
        if any(label not in ("E", "S", "C", "I") for label in group["labels"].values()):
            raise ValueError("Unknown ESCI label")
        exact = {product for product, label in group["labels"].items() if label == "E"}
        found = len(exact & identities[key])
        top = hits[:coverage_cutoff]
        judged = sum(product in group["labels"] for product, _ in top)
        evaluated.append({
            "locale": key[0], "query_id": key[1], "group": digest([key[0], normalize_query(group["query"])]),
            "known_exact_products": len(exact), "retrieved_known_exact_at_100": found,
            "known_exact_recall_at_100": found / len(exact) if exact else None,
            "judged_at_10_count": judged, "returned_at_10": len(top),
            "judgment_coverage_at_10": judged / len(top) if top else 0.0,
            "top_10_slot_fill": len(top) / coverage_cutoff, "returned_at_100": returned,
            "analyzed_terms": terms,
        })
    return evaluated


def mean_interval(rows: list[dict], metric: str, resamples: int, seed: int) -> dict:
    if not rows or resamples < 2:
        raise ValueError("Need observations and at least two resamples")
    groups = {}
    for row in rows:
        value = row[metric]
        if value is not None:
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Invalid metric value")
            groups.setdefault(row["group"], []).append(value)
    if not groups:
        return {"mean": None, "ci95": None, "defined_queries": 0, "normalized_query_groups": 0}
    aggregates = [(sum(groups[key]), len(groups[key])) for key in sorted(groups)]
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        sample = rng.choices(aggregates, k=len(aggregates))
        samples.append(sum(pair[0] for pair in sample) / sum(pair[1] for pair in sample))
    return {"mean": sum(pair[0] for pair in aggregates) / sum(pair[1] for pair in aggregates),
            "ci95": [percentile(samples, .025), percentile(samples, .975)],
            "defined_queries": sum(pair[1] for pair in aggregates), "normalized_query_groups": len(groups)}


def summarize_retrieval(rows: list[dict], policy: dict) -> dict:
    confidence = policy["confidence"]
    return {
        "queries": len(rows), "normalized_query_groups": len({row["group"] for row in rows}),
        "known_exact_recall_at_100": mean_interval(rows, "known_exact_recall_at_100", confidence["resamples"], confidence["seed"]),
        "judgment_coverage_at_10": mean_interval(rows, "judgment_coverage_at_10", confidence["resamples"], confidence["seed"]),
        "queries_without_known_exact": sum(row["known_exact_products"] == 0 for row in rows),
        "known_exact_products": sum(row["known_exact_products"] for row in rows),
        "retrieved_known_exact_at_100": sum(row["retrieved_known_exact_at_100"] for row in rows),
        "queries_with_a_known_exact_hit_at_100": sum(row["retrieved_known_exact_at_100"] > 0 for row in rows),
        "zero_result_queries": sum(row["returned_at_100"] == 0 for row in rows),
        "zero_analyzed_term_queries": sum(row["analyzed_terms"] == 0 for row in rows),
        "queries_with_fewer_than_10_results": sum(row["returned_at_100"] < 10 for row in rows),
        "queries_with_fewer_than_100_results": sum(row["returned_at_100"] < 100 for row in rows),
        "mean_returned_at_10": fmean(row["returned_at_10"] for row in rows),
        "mean_top_10_slot_fill": fmean(row["top_10_slot_fill"] for row in rows),
        "returned_at_100": sum(row["returned_at_100"] for row in rows),
        "judged_at_10_count": sum(row["judged_at_10_count"] for row in rows),
    }
