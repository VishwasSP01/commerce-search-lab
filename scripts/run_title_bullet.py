"""Run one predeclared weighted-field ablation; the official test is never opened."""

import argparse
import csv
import json
import math
from pathlib import Path
import platform
from statistics import fmean
import subprocess
import tempfile
import time

import duckdb

from audit_esci import records
from download_esci import ROOT, verify_file
from prepare_esci_splits import file_digest
from ranking_metrics import summarize
from run_bm25 import JAR, LOCALES, check_product_id, encode, evaluate, java, validation_groups

CONFIG = ROOT / "config/bm25-title-bullet-v1.json"
HEADER = ["product_locale", "query_id", "product_id", "score", "analyzed_terms"]


def read_scores(path: Path) -> dict:
    values = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != HEADER:
            raise ValueError("Unexpected score schema")
        for row in reader:
            key = (row["product_locale"], int(row["query_id"]), row["product_id"])
            score, terms = float(row["score"]), int(row["analyzed_terms"])
            if key in values or not math.isfinite(score) or score < 0 or terms < 0:
                raise ValueError("Duplicate or invalid score")
            values[key] = (score, terms)
    if not values:
        raise ValueError("Empty scores")
    return values


def combine_scores(title_path: Path, bullet_path: Path, output: Path, weight: float) -> None:
    if not math.isfinite(weight) or weight < 0:
        raise ValueError("Weight must be finite and nonnegative")
    title, bullet = read_scores(title_path), read_scores(bullet_path)
    if title.keys() != bullet.keys():
        raise ValueError("Title and bullet candidate identities differ")
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(HEADER)
        for key in sorted(title):
            score, terms = title[key]
            other, other_terms = bullet[key]
            if terms != other_terms:
                raise ValueError("Query analyzers differ between fields")
            combined = score + weight * other
            if not math.isfinite(combined):
                raise ValueError("Combined score overflow")
            writer.writerow([*key, combined, terms])


def pair_metrics(baseline: list[dict], variant: list[dict]) -> list[dict]:
    def keyed(rows):
        result = {(row["locale"], row["query_id"]): row for row in rows}
        if len(result) != len(rows) or not rows:
            raise ValueError("Empty or duplicated query metrics")
        return result
    old, new = keyed(baseline), keyed(variant)
    if old.keys() != new.keys():
        raise ValueError("Query identities differ")
    paired = []
    for key in sorted(old):
        before, after = old[key], new[key]
        if any(before[field] != after[field] for field in ("group", "terms", "candidates", "zero_ideal", "reference")):
            raise ValueError("Evaluation protocol differs")
        paired.append(dict(after, reference=before["bm25"], baseline_all_zero=before["all_zero"],
                           baseline_loss=before["bm25"] - before["reference"] < -1e-12))
    return paired


def comparison(rows: list[dict], policy: dict) -> dict:
    result = summarize(rows, policy["confidence"]["resamples"], policy["confidence"]["seed"])
    # The shared metric helper calls the new system 'bm25' and comparator 'reference'.
    # Rename these immediately so this report cannot be mistaken for the ID-order comparison.
    result["title_bullet_ndcg_at_10"] = result.pop("bm25_ndcg_at_10")
    result["title_only_ndcg_at_10"] = result.pop("reference_ndcg_at_10")
    result["title_bullet_ci95"] = result.pop("bm25_ci95")
    tolerance = policy["outcome_tolerance"]
    deltas = [row["bm25"] - row["reference"] for row in rows]
    result["improved_queries"] = sum(value > tolerance for value in deltas)
    result["regressed_queries"] = sum(value < -tolerance for value in deltas)
    result["tied_queries"] = sum(abs(value) <= tolerance for value in deltas)
    return result


def adoption_passes(overall: dict, locales: dict) -> bool:
    return overall["paired_delta_ci95"][0] > 0 and all(
        result["delta_ndcg_at_10"] >= 0 for result in locales.values())


def write_metrics(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=ROOT / "data/processed/bm25-title-v1")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/bm25-title-bullet-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/bm25-title-bullet-v1.json")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("Use a fresh --output-dir to preserve experiment evidence")
    policy = json.loads(CONFIG.read_text())
    if (policy["evaluation_partition"] != "validation" or policy["bullet_weight"] != .25
            or policy["outcome_tolerance"] != 1e-12):
        raise ValueError("Only the predeclared validation experiment is supported")
    baseline_path = ROOT / "reports/bm25-title-v1.json"
    baseline = json.loads(baseline_path.read_text())
    for path, checksum in baseline["provenance"]["source_sha256"].items():
        if file_digest(ROOT / path) != checksum:
            raise ValueError(f"Frozen baseline source changed: {path}")
    specs = {artifact["name"]: artifact for artifact in baseline["artifacts"]}
    for name in ("queries.tsv", "scores.tsv", "per-query.csv"):
        verify_file(args.baseline_dir / name, specs[name])
    raw = ROOT / "data/raw/esci/shopping_queries_dataset_products.parquet"
    validation = ROOT / "data/processed/esci-ranking-v1/validation.parquet"
    verify_file(raw, baseline["provenance"]["products_input"])
    verify_file(validation, baseline["provenance"]["validation_input"])
    subprocess.run([str(ROOT / "mvnw"), "--batch-mode", "--no-transfer-progress", "-q", "-DskipTests", "package"],
                   cwd=ROOT, check=True)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".title-bullet-", dir=args.output_dir.parent) as temporary:
        stage = Path(temporary) / "run"
        stage.mkdir()
        started = time.perf_counter()
        with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection:
            connection.read_parquet(str(validation)).create_view("validation")
            groups = validation_groups(records(connection, """SELECT product_locale,query_id,query,product_id,
                esci_label,split,evaluation_split FROM validation ORDER BY product_locale,query_id,product_id"""))
            # Reconstruct the scorer input from verified validation, then require byte equality.
            query_path = stage / "queries.tsv"
            with query_path.open("w", encoding="utf-8", newline="") as handle:
                for (locale, query_id), group in sorted(groups.items()):
                    handle.write(f"{locale}\t{query_id}\t{encode(group['query'])}\t{','.join(sorted(group['labels']))}\n")
            verify_file(query_path, specs["queries.tsv"])
            connection.read_parquet(str(raw)).create_view("products")
            catalog = records(connection, """SELECT product_locale, count(*) AS products,
                count(DISTINCT product_id) AS unique_ids,
                count(*) FILTER (WHERE product_id IS NULL OR product_title IS NULL) AS null_rows,
                count(*) FILTER (WHERE product_bullet_point IS NULL) AS null_bullets,
                count(*) FILTER (WHERE product_bullet_point IS NOT NULL
                    AND trim(product_bullet_point) = '') AS blank_bullets
                FROM products GROUP BY product_locale ORDER BY product_locale""")
            if [{key: row[key] for key in baseline["catalog"][0]} for row in catalog] != baseline["catalog"]:
                raise ValueError("Catalog differs from frozen baseline")
            candidate_coverage = records(connection, """SELECT p.product_locale, count(*) AS products,
                count(*) FILTER (WHERE p.product_bullet_point IS NULL) AS null_bullets,
                count(*) FILTER (WHERE p.product_bullet_point IS NOT NULL
                    AND trim(p.product_bullet_point) = '') AS blank_bullets
                FROM products p SEMI JOIN validation v USING(product_locale, product_id)
                GROUP BY p.product_locale ORDER BY p.product_locale""")
            cursor = connection.execute("SELECT product_locale, product_id, coalesce(product_bullet_point, '') FROM products ORDER BY product_locale, product_id")
            with (stage / "bullets.tsv").open("w", encoding="utf-8", newline="") as handle:
                while batch := cursor.fetchmany(10_000):
                    for locale, product_id, text in batch:
                        check_product_id(product_id)
                        handle.write(f"{locale}\t{product_id}\t{encode(text)}\n")
        export_seconds = time.perf_counter() - started
        old = evaluate(groups, args.baseline_dir / "scores.tsv", baseline["config"])
        write_metrics(stage / "baseline-per-query.csv", old)
        verify_file(stage / "baseline-per-query.csv", specs["per-query.csv"])
        print(f"Exported bullets for {sum(row['products'] for row in catalog):,} products; verified {len(groups):,} baseline query metrics", flush=True)
        started = time.perf_counter()
        metadata = java("score", str(args.baseline_dir / "index"), str(query_path),
                        str(stage / "title-rescored.tsv"), "1.2", "0.75")
        title_rescore_seconds = time.perf_counter() - started
        verify_file(stage / "title-rescored.tsv", specs["scores.tsv"])
        if int(metadata["queries"]) != len(groups):
            raise ValueError("Title query coverage mismatch")
        started = time.perf_counter()
        metadata = java("index", str(stage / "bullets.tsv"), str(stage / "bullet-index"), "1.2", "0.75")
        index_seconds = time.perf_counter() - started
        if metadata.get("lucene") != baseline["config"]["lucene_version"] or int(metadata["products"]) != sum(row["products"] for row in catalog):
            raise ValueError("Bullet index coverage or engine version mismatch")
        started = time.perf_counter()
        metadata = java("score", str(stage / "bullet-index"), str(query_path), str(stage / "bullet-scores.tsv"), "1.2", "0.75")
        score_seconds = time.perf_counter() - started
        if int(metadata["queries"]) != len(groups):
            raise ValueError("Bullet query coverage mismatch")
        # Strict evaluation also checks complete judged-candidate coverage and query term counts.
        evaluate(groups, stage / "bullet-scores.tsv", baseline["config"])
        started = time.perf_counter()
        combine_scores(stage / "title-rescored.tsv", stage / "bullet-scores.tsv", stage / "scores.tsv", policy["bullet_weight"])
        combine_seconds = time.perf_counter() - started
        variant = evaluate(groups, stage / "scores.tsv", baseline["config"])
        paired = pair_metrics(old, variant)
        write_metrics(stage / "per-query.csv", paired)
        locales = {locale: comparison([row for row in paired if row["locale"] == locale], policy) for locale in LOCALES}
        overall = comparison(paired, policy)
        cohorts = {name: comparison([row for row in paired if row[flag]], policy)
                   for name, flag in (("baseline_zero_scores", "baseline_all_zero"), ("baseline_losses_vs_id_order", "baseline_loss"))}
        def case(row):
            return {"locale": row["locale"], "query_id": row["query_id"], "title_only_ndcg_at_10": row["reference"],
                    "title_bullet_ndcg_at_10": row["bm25"], "delta": row["bm25"] - row["reference"]}
        gains = sorted((row for row in paired if row["bm25"] - row["reference"] > policy["outcome_tolerance"]),
                       key=lambda row: (-(row["bm25"] - row["reference"]), row["locale"], row["query_id"]))
        losses = sorted((row for row in paired if row["bm25"] - row["reference"] < -policy["outcome_tolerance"]),
                        key=lambda row: (row["bm25"] - row["reference"], row["locale"], row["query_id"]))
        title_bytes = directory_bytes(args.baseline_dir / "index")
        if title_bytes != baseline["execution"]["index_bytes"]:
            raise ValueError("Frozen title index size changed")
        bullet_bytes = directory_bytes(stage / "bullet-index")
        report = {
            "config": policy, "partition": "validation", "official_test_read": False,
            "catalog": catalog, "unique_validation_candidate_bullet_coverage": candidate_coverage,
            "overall_query_weighted": overall, "metrics_by_locale": locales,
            "locale_macro_mean": {key: fmean(result[key] for result in locales.values()) for key in
                                  ("title_only_ndcg_at_10", "title_bullet_ndcg_at_10", "delta_ndcg_at_10")},
            "development_cohorts": cohorts,
            "largest_gains": [case(row) for row in gains[:10]], "largest_regressions": [case(row) for row in losses[:10]],
            "adoption_rule_passed": adoption_passes(overall, locales),
            "checks": {"baseline_query_input_exact": True, "baseline_per_query_metrics_exact": True,
                       "title_rescore_exact": True, "judged_candidate_coverage_exact": True},
            "execution": {"export_and_validation_read_seconds": export_seconds,
                          "title_rescoring_batch_seconds": title_rescore_seconds,
                          "bullet_index_build_seconds": index_seconds, "bullet_scoring_batch_seconds": score_seconds,
                          "combine_score_files_seconds": combine_seconds,
                          "reused_title_index_bytes": title_bytes, "bullet_index_bytes": bullet_bytes,
                          "combined_index_bytes": title_bytes + bullet_bytes,
                          "java_heap_limit": "4 GiB", "duckdb_memory_setting": "4 GB",
                          "note": "Single local batches, JVM startup included; title build reused. Hashing, Maven, evaluation and bootstrap excluded from stage timings. File combination includes I/O. Not API latency, QPS, peak RAM or a load benchmark."},
            "provenance": {"baseline_report_sha256": file_digest(baseline_path),
                           "products_input": baseline["provenance"]["products_input"],
                           "validation_input": baseline["provenance"]["validation_input"],
                           "source_sha256": {path: file_digest(ROOT / path) for path in (
                               "config/bm25-title-bullet-v1.json", "docs/decisions/0002-title-bullet-ablation.md",
                               "scripts/run_title_bullet.py", "scripts/download_esci.py", "tests/test_title_bullet.py")},
                           "frozen_source_sha256": baseline["provenance"]["source_sha256"],
                           "jar_sha256": file_digest(JAR), "python": platform.python_version(), "duckdb": duckdb.__version__,
                           "java": subprocess.run(["java", "-version"], capture_output=True, text=True, check=True).stderr.strip()},
            "artifacts": [{"name": name, "bytes": (stage / name).stat().st_size, "sha256": file_digest(stage / name)}
                          for name in ("bullets.tsv", "queries.tsv", "baseline-per-query.csv", "title-rescored.tsv",
                                       "bullet-scores.tsv", "scores.tsv", "per-query.csv")],
            "limits": "One validation-development experiment following error analysis; no held-out generalization claim, p-value, online experiment or full-catalog retrieval relevance estimate. Cohorts overlap and were discovered on validation. Null/blank coverage is textual, not analyzer-token coverage. Two independent BM25 indexes, not BM25F.",
        }
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (stage / "manifest.json").write_text(serialized)
        stage.rename(args.output_dir)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    print(json.dumps({"overall": overall, "by_locale": locales, "adoption_rule_passed": report["adoption_rule_passed"]}, indent=2))
    print(f"Aggregate report: {args.report}")


if __name__ == "__main__":
    main()
