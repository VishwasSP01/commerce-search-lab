"""Diagnose fixed validation rankings with unchanged baseline analyzers and scores."""

import argparse
import base64
from collections import Counter
import csv
import json
import os
from pathlib import Path
import platform
from statistics import fmean
import subprocess
import tempfile

import duckdb

from audit_esci import records
from download_esci import ROOT, verify_file
from prepare_esci_splits import digest, file_digest
from ranking_metrics import rank_candidates
from run_bm25 import JAR, encode, evaluate, validation_groups

CONFIG = ROOT / "config/bm25-error-analysis-v1.json"
BRIDGE = ROOT / "tools/java/lab/commerce/search/AnalyzeText.java"
FIELDS = ("title", "description", "bullet_point", "brand", "color")
EXTRA_FIELDS = FIELDS[1:]


def analyze_texts(items: list[tuple], directory: Path) -> list[list[str]]:
    """Use package access through the same Java application classloader as the baseline."""
    classes = directory / "classes"
    classes.mkdir()
    subprocess.run(["javac", "--release", "21", "-cp", str(JAR), "-d", str(classes), str(BRIDGE)], check=True)
    with (directory / "texts.tsv").open("w", encoding="utf-8", newline="") as handle:
        for index, (locale, text) in enumerate(items):
            handle.write(f"{index}\t{locale}\t{encode(text)}\n")
    result = subprocess.run([
        "java", "-Xmx2g", "--enable-native-access=ALL-UNNAMED", "-cp", os.pathsep.join((str(classes), str(JAR))),
        "lab.commerce.search.AnalyzeText", str(directory / "texts.tsv"), str(directory / "tokens.tsv"),
    ], check=True, capture_output=True, text=True)
    metadata = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if int(metadata["records"]) != len(items) or metadata["lucene"] != "10.6.0":
        raise ValueError("Diagnostic Java coverage or Lucene version mismatch")
    tokens = [None] * len(items)
    with (directory / "tokens.tsv").open(encoding="utf-8") as handle:
        for line in handle:
            identifier, count, encoded = line.rstrip("\n").split("\t")
            index, count = int(identifier), int(count)
            if not 0 <= index < len(tokens) or tokens[index] is not None:
                raise ValueError("Unexpected or duplicate diagnostic token record")
            values = [base64.b64decode(value, validate=True).decode("utf-8") for value in encoded.split(",")] if count else []
            if len(values) != count:
                raise ValueError("Token-count mismatch")
            tokens[index] = values
    if any(value is None for value in tokens):
        raise ValueError("Missing diagnostic tokens")
    return tokens


def query_diagnostics(group: dict, scores: dict, query_tokens: list[str], products: dict,
                      metric: dict, tolerance: float) -> dict:
    if len(query_tokens) != metric["terms"]:
        raise ValueError("Query analyzer differs from the scored baseline")
    ranked = rank_candidates(scores)
    terms = set(query_tokens)
    labels = group["labels"]
    relevant = [product for product in ranked if labels[product] in ("E", "S")]
    irrelevant = [product for product in ranked if labels[product] == "I"]
    title_overlap = {product: terms & set(products[product]["title"]) for product in ranked}
    if any(scores[product] > 0 and not title_overlap[product] for product in ranked):
        raise ValueError("Positive lexical score has no shared diagnostic title token")
    zero = all(score == 0 for score in scores.values())
    delta = metric["bm25"] - metric["reference"]
    outcome = "win" if delta > tolerance else "loss" if delta < -tolerance else "tie"
    if zero and outcome != "tie":
        raise ValueError("Zero-score ranking must equal the product-ID reference")
    if zero:
        zero_kind = "empty_query_analysis" if not terms else (
            "no_title_token_overlap" if not any(title_overlap.values()) else "token_overlap_but_zero_score")
    else:
        zero_kind = None
    flags = {}
    for field in EXTRA_FIELDS:
        flags[field] = {
            "es_overlap": any(terms & set(products[product][field]) for product in relevant),
            "irrelevant_overlap": any(terms & set(products[product][field]) for product in irrelevant),
            "es_full_query_coverage": bool(terms) and any(terms <= set(products[product][field]) for product in relevant),
            "es_new_tokens": any((terms & set(products[product][field])) - title_overlap[product] for product in relevant),
            "irrelevant_new_tokens": any((terms & set(products[product][field])) - title_overlap[product] for product in irrelevant),
            "es_without_title_overlap": any(not title_overlap[product] and terms & set(products[product][field]) for product in relevant),
        }
    size = len(query_tokens)
    bucket = str(size) if size <= 2 else "3-4" if size <= 4 else "5+"
    best_score = max(scores.values())
    return {
        "locale": metric["locale"], "query_id": metric["query_id"], "outcome": outcome,
        "bm25": metric["bm25"], "reference": metric["reference"], "delta": delta,
        "terms": size, "unique_terms": len(terms), "term_bucket": bucket, "candidates": len(ranked),
        "all_zero": zero, "zero_kind": zero_kind, "has_es_candidate": bool(relevant),
        "no_positive_es": bool(relevant) and not any(scores[product] > 0 for product in relevant),
        "any_zero_es": any(scores[product] == 0 for product in relevant),
        "top1_label": labels[ranked[0]], "top_score_tied": sum(score == best_score for score in scores.values()) > 1,
        "metadata_flags": flags,
    }


def aggregate(cases: list[dict]) -> dict:
    zero = [case for case in cases if case["all_zero"]]
    losses = [case for case in cases if case["outcome"] == "loss"]
    result = {
        "queries": len(cases), "judgments": sum(case["candidates"] for case in cases),
        "outcomes": dict(sorted(Counter(case["outcome"] for case in cases).items())),
        "zero_score_queries": len(zero), "zero_kinds": dict(sorted(Counter(case["zero_kind"] for case in zero).items())),
        "zero_with_es_candidate": sum(case["has_es_candidate"] for case in zero),
        "zero_with_any_es_extra_overlap": sum(any(flags["es_overlap"] for flags in case["metadata_flags"].values()) for case in zero),
        "zero_with_es_full_coverage_in_one_extra_field": sum(any(flags["es_full_query_coverage"] for flags in case["metadata_flags"].values()) for case in zero),
        "nonempty_zero_without_es_extra_overlap": sum(case["terms"] > 0 and not any(flags["es_overlap"] for flags in case["metadata_flags"].values()) for case in zero),
        "loss_top1_labels": dict(sorted(Counter(case["top1_label"] for case in losses).items())),
        "losses_with_es_candidate": sum(case["has_es_candidate"] for case in losses),
        "losses_no_positive_es": sum(case["no_positive_es"] for case in losses),
        "losses_any_zero_es": sum(case["any_zero_es"] for case in losses),
        "losses_top_score_tied": sum(case["top_score_tied"] for case in losses),
        "extra_field_flags": {}, "term_count_slices": [],
    }
    for field in EXTRA_FIELDS:
        result["extra_field_flags"][field] = {
            "zero_with_es_overlap": sum(case["metadata_flags"][field]["es_overlap"] for case in zero),
            "zero_with_irrelevant_overlap": sum(case["metadata_flags"][field]["irrelevant_overlap"] for case in zero),
            "zero_with_es_and_no_irrelevant_overlap": sum(case["metadata_flags"][field]["es_overlap"] and not case["metadata_flags"][field]["irrelevant_overlap"] for case in zero),
            "zero_with_es_full_query_coverage": sum(case["metadata_flags"][field]["es_full_query_coverage"] for case in zero),
            "loss_with_es_new_tokens": sum(case["metadata_flags"][field]["es_new_tokens"] for case in losses),
            "loss_with_irrelevant_new_tokens": sum(case["metadata_flags"][field]["irrelevant_new_tokens"] for case in losses),
            "loss_with_es_without_title_overlap": sum(case["metadata_flags"][field]["es_without_title_overlap"] for case in losses),
        }
    for bucket in ("0", "1", "2", "3-4", "5+"):
        selected = [case for case in cases if case["term_bucket"] == bucket]
        if selected:
            result["term_count_slices"].append({"analyzed_terms": bucket, "queries": len(selected),
                "bm25_mean": fmean(case["bm25"] for case in selected), "delta_mean": fmean(case["delta"] for case in selected),
                "losses": sum(case["outcome"] == "loss" for case in selected), "zero_scores": sum(case["all_zero"] for case in selected)})
    return result


def sample_cases(cases: list[dict], policy: dict) -> list[dict]:
    selected = {}
    for locale in ("es", "jp", "us"):
        local = [case for case in cases if case["locale"] == locale]
        zero = sorted((case for case in local if case["all_zero"]), key=lambda case: digest([policy["sample_seed"], locale, case["query_id"]]))
        losses = sorted((case for case in local if case["outcome"] == "loss"), key=lambda case: (case["delta"], case["query_id"]))
        for cohort, candidates in (("zero_hash_sample", zero), ("largest_loss", losses)):
            for case in candidates[:policy["samples_per_locale_per_cohort"]]:
                selected[(locale, case["query_id"])] = dict(case, sample_reason=cohort)
    for case in cases:
        if case["zero_kind"] == "empty_query_analysis":
            selected[(case["locale"], case["query_id"])] = dict(case, sample_reason="empty_query_analysis")
    return [selected[key] for key in sorted(selected)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=ROOT / "data/processed/bm25-title-v1")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/bm25-error-analysis-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/bm25-error-analysis-v1.json")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("Use a fresh --output-dir to preserve existing analysis evidence")
    policy = json.loads(CONFIG.read_text())
    if (policy["partition"] != "validation" or policy["metadata_fields"] != list(FIELDS)
            or policy["diagnostic_relevance_labels"] != ["E", "S"]
            or policy["query_length_buckets"] != ["0", "1", "2", "3-4", "5+"]):
        raise ValueError("Unsupported analysis policy")
    baseline_path = ROOT / "reports/bm25-title-v1.json"
    baseline = json.loads(baseline_path.read_text())
    for path, checksum in baseline["provenance"]["source_sha256"].items():
        if file_digest(ROOT / path) != checksum:
            raise ValueError(f"Baseline source changed: {path}")
    for artifact in baseline["artifacts"]:
        if artifact["name"] in ("scores.tsv", "per-query.csv"):
            verify_file(args.baseline_dir / artifact["name"], artifact)
    raw = ROOT / "data/raw/esci/shopping_queries_dataset_products.parquet"
    validation = ROOT / "data/processed/esci-ranking-v1/validation.parquet"
    verify_file(raw, baseline["provenance"]["products_input"])
    verify_file(validation, baseline["provenance"]["validation_input"])
    with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection:
        connection.read_parquet(str(validation)).create_view("validation")
        rows = records(connection, "SELECT product_locale,query_id,query,product_id,esci_label,split,evaluation_split FROM validation ORDER BY product_locale,query_id,product_id")
        groups = validation_groups(rows)
        connection.read_parquet(str(raw)).create_view("products")
        metadata_rows = records(connection, """SELECT p.product_locale,p.product_id,
            p.product_title AS title,p.product_description AS description,p.product_bullet_point AS bullet_point,
            p.product_brand AS brand,p.product_color AS color
            FROM products p SEMI JOIN validation v USING(product_locale,product_id)
            ORDER BY p.product_locale,p.product_id""")
    products = {(row["product_locale"], row["product_id"]): row for row in metadata_rows}
    expected = {(locale, product) for (locale, _), group in groups.items() for product in group["labels"]}
    if set(products) != expected or len(products) != len(metadata_rows):
        raise ValueError("Locale-product metadata coverage is incomplete or duplicated")
    metrics = evaluate(groups, args.baseline_dir / "scores.tsv", baseline["config"])
    if fmean(row["bm25"] for row in metrics) != baseline["overall_query_weighted"]["bm25_ndcg_at_10"]:
        raise ValueError("Baseline scores do not reproduce the recorded metric")
    scores = {}
    with (args.baseline_dir / "scores.tsv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            scores.setdefault((row["product_locale"], int(row["query_id"])), {})[row["product_id"]] = float(row["score"])
    subprocess.run([str(ROOT / "mvnw"), "--batch-mode", "--no-transfer-progress", "-q", "-DskipTests", "package"], cwd=ROOT, check=True)
    items, item_ids = [], {}
    def register(locale, text):
        key = (locale, text or "")
        if key not in item_ids:
            item_ids[key] = len(items)
            items.append(key)
        return item_ids[key]
    query_ids = {key: register(key[0], group["query"]) for key, group in groups.items()}
    product_ids = {key: {field: register(key[0], row[field]) for field in FIELDS} for key, row in products.items()}
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bm25-errors-", dir=args.output_dir.parent) as temporary:
        stage = Path(temporary) / "analysis"
        stage.mkdir()
        print(f"Analyzing {len(items):,} distinct locale/text records for {len(groups):,} validation queries", flush=True)
        tokens = analyze_texts(items, stage)
        product_tokens = {key: {field: tokens[index] for field, index in fields.items()} for key, fields in product_ids.items()}
        cases = []
        for metric in metrics:
            key = (metric["locale"], metric["query_id"])
            case_products = {product: product_tokens[(key[0], product)] for product in groups[key]["labels"]}
            cases.append(query_diagnostics(groups[key], scores[key], tokens[query_ids[key]], case_products, metric, policy["comparison_tolerance"]))
        samples = sample_cases(cases, policy)
        review = []
        for case in samples:
            key = (case["locale"], case["query_id"])
            ranked = rank_candidates(scores[key])
            best_relevant = sorted((p for p in ranked if groups[key]["labels"][p] in ("E", "S")),
                                   key=lambda p: (-baseline["config"]["gain"][groups[key]["labels"][p]], ranked.index(p)))[:2]
            ids = list(dict.fromkeys(ranked[:3] + best_relevant))
            review.append(dict(case, query=groups[key]["query"], query_tokens=tokens[query_ids[key]],
                candidate_examples=[dict(products[(key[0], product)], label=groups[key]["labels"][product],
                    rank=ranked.index(product)+1, score=scores[key][product],
                    query_term_overlap={field: sorted(set(tokens[query_ids[key]]) & set(product_tokens[(key[0], product)][field])) for field in FIELDS}) for product in ids]))
        (stage / "per-query-diagnostics.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2)+"\n")
        (stage / "case-samples.json").write_text(json.dumps(review, ensure_ascii=False, indent=2)+"\n")
        report = {"policy": policy, "partition": "validation", "ranker_changed": False, "official_test_read": False,
            "overall": aggregate(cases), "by_locale": {locale: aggregate([c for c in cases if c["locale"] == locale]) for locale in ("es","jp","us")},
            "sample_manifest": [{key: case[key] for key in ("locale","query_id","sample_reason","outcome","zero_kind","delta")} for case in samples],
            "distinct_texts_analyzed": len(items), "unique_candidate_products": len(products),
            "provenance": {"baseline_report_sha256": file_digest(baseline_path), "policy_sha256": file_digest(CONFIG),
                "analysis_script_sha256": file_digest(Path(__file__)), "java_bridge_sha256": file_digest(BRIDGE),
                "jar_sha256": file_digest(JAR), "input_validation_sha256": file_digest(validation), "input_products_sha256": baseline["provenance"]["products_input"]["sha256"],
                "python": platform.python_version(), "duckdb": duckdb.__version__,
                "java": subprocess.run(["java", "-version"], capture_output=True, text=True, check=True).stderr.strip()},
            "artifacts": [{"name": name,"bytes": (stage/name).stat().st_size,"sha256": file_digest(stage/name)}
                          for name in ("texts.tsv","tokens.tsv","per-query-diagnostics.json","case-samples.json")],
            "limits": "Metadata token overlaps are overlapping diagnostic flags, not causal categories or measured gains. E/S defines diagnostic relevance only; original ESCI gains and rankings remain unchanged. Samples are purpose-specific, not prevalence estimates."}
        serialized = json.dumps(report, ensure_ascii=False, indent=2)+"\n"
        (stage / "manifest.json").write_text(serialized)
        stage.rename(args.output_dir)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    print(json.dumps(report["overall"], indent=2))
    print(f"Analysis report: {args.report}; private review cases: {args.output_dir / 'case-samples.json'}")


if __name__ == "__main__":
    main()
