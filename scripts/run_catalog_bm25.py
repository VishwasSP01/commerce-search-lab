"""Measure title BM25 retrieval across the full locale catalog on validation only."""

import argparse
import csv
import json
from pathlib import Path
import platform
from statistics import fmean
import subprocess
import tempfile
import time

import duckdb

from audit_esci import records
from catalog_metrics import evaluate_retrieval, summarize_retrieval
from download_esci import ROOT, verify_file
from prepare_esci_splits import file_digest
from run_bm25 import JAR, LOCALES, encode, evaluate, java, validation_groups

CONFIG = ROOT / "config/catalog-bm25-title-v1.json"


def catalog_java(*args: str) -> dict:
    result = subprocess.run(["java", "-Xmx4g", "--enable-native-access=ALL-UNNAMED", "-cp", str(JAR),
                             "lab.commerce.search.CatalogCli", *args], check=True, text=True, stdout=subprocess.PIPE)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def validate_catalog_membership(connection, results_path: Path) -> int:
    # Explicit strings protect product identity from CSV numeric inference.
    connection.read_csv(str(results_path), header=True, all_varchar=True, delimiter="\t").create_view("retrieved", replace=True)
    invalid = connection.execute("""SELECT count(*) FROM retrieved r ANTI JOIN products p
        ON r.product_locale = p.product_locale AND r.product_id = p.product_id""").fetchall()[0][0]
    if invalid:
        raise ValueError("Retrieved product is absent from its locale catalog")
    return connection.execute("SELECT count(*) FROM retrieved").fetchall()[0][0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=ROOT / "data/processed/bm25-title-v1")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/catalog-bm25-title-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/catalog-bm25-title-v1.json")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("Use a fresh --output-dir to preserve retrieval evidence")
    policy = json.loads(CONFIG.read_text())
    if (policy["partition"] != "validation" or policy["retrieval_cutoff"] != 100
            or policy["coverage_cutoff"] != 10 or policy["k1"] != 1.2 or policy["b"] != .75):
        raise ValueError("Only the predeclared validation protocol is supported")
    baseline_path = ROOT / "reports/bm25-title-v1.json"
    baseline = json.loads(baseline_path.read_text())
    for path, checksum in baseline["provenance"]["source_sha256"].items():
        if file_digest(ROOT / path) != checksum:
            raise ValueError(f"Frozen baseline source changed: {path}")
    specs = {artifact["name"]: artifact for artifact in baseline["artifacts"]}
    for artifact in specs.values():
        verify_file(args.baseline_dir / artifact["name"], artifact)
    raw = ROOT / "data/raw/esci/shopping_queries_dataset_products.parquet"
    validation = ROOT / "data/processed/esci-ranking-v1/validation.parquet"
    verify_file(raw, baseline["provenance"]["products_input"])
    verify_file(validation, baseline["provenance"]["validation_input"])
    subprocess.run([str(ROOT / "mvnw"), "--batch-mode", "--no-transfer-progress", "-q", "-DskipTests", "package"],
                   cwd=ROOT, check=True)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".catalog-bm25-", dir=args.output_dir.parent) as temporary:
        stage = Path(temporary) / "run"
        stage.mkdir()
        with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection:
            connection.read_parquet(str(validation)).create_view("validation")
            groups = validation_groups(records(connection, """SELECT product_locale,query_id,query,product_id,
                esci_label,split,evaluation_split FROM validation ORDER BY product_locale,query_id,product_id"""))
            connection.read_parquet(str(raw)).create_view("products")
            catalog = records(connection, """SELECT product_locale, count(*) AS products,
                count(DISTINCT product_id) AS unique_ids,
                count(*) FILTER (WHERE product_id IS NULL OR product_title IS NULL) AS null_rows
                FROM products GROUP BY product_locale ORDER BY product_locale""")
            if catalog != baseline["catalog"]:
                raise ValueError("Catalog scope differs from frozen baseline")
            old = evaluate(groups, args.baseline_dir / "scores.tsv", baseline["config"])
            # Require every query's original metric, not just its mean, using the frozen CSV format.
            with (stage / "baseline-per-query.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=old[0].keys(), lineterminator="\n")
                writer.writeheader()
                writer.writerows(old)
            verify_file(stage / "baseline-per-query.csv", specs["per-query.csv"])
            query_path = stage / "queries.tsv"
            with query_path.open("w", encoding="utf-8", newline="") as handle:
                for (locale, query_id), group in sorted(groups.items()):
                    handle.write(f"{locale}\t{query_id}\t{encode(group['query'])}\n")
            print(f"Building full catalog for {len(groups):,} validation queries; retrieval input has no candidate IDs", flush=True)
            started = time.perf_counter()
            metadata = catalog_java("index", str(args.baseline_dir / "products.tsv"), str(stage / "index"), "1.2", ".75")
            build_seconds = time.perf_counter() - started
            if metadata.get("lucene") != baseline["config"]["lucene_version"] or int(metadata["products"]) != sum(row["products"] for row in catalog):
                raise ValueError("Catalog index coverage or engine mismatch")
            started = time.perf_counter()
            parity = java("score", str(stage / "index"), str(args.baseline_dir / "queries.tsv"),
                          str(stage / "candidate-parity.tsv"), "1.2", ".75")
            parity_seconds = time.perf_counter() - started
            verify_file(stage / "candidate-parity.tsv", specs["scores.tsv"])
            if int(parity["queries"]) != len(groups):
                raise ValueError("Candidate parity query count mismatch")
            print("Frozen candidate score artifact matches exactly; searching without candidate filters", flush=True)
            started = time.perf_counter()
            metadata = catalog_java("retrieve", str(stage / "index"), str(query_path), str(stage / "results.tsv"),
                                    str(stage / "query-status.tsv"), "100", "1.2", ".75")
            retrieval_seconds = time.perf_counter() - started
            if int(metadata["queries"]) != len(groups) or metadata["lucene"] != baseline["config"]["lucene_version"]:
                raise ValueError("Retrieval query coverage or engine mismatch")
            terms = {(row["locale"], row["query_id"]): row["terms"] for row in old}
            per_query = evaluate_retrieval(groups, stage / "results.tsv", stage / "query-status.tsv", terms)
            returned = validate_catalog_membership(connection, stage / "results.tsv")
            if returned != sum(row["returned_at_100"] for row in per_query):
                raise ValueError("Catalog-membership validation coverage mismatch")
        (stage / "per-query.json").write_text(json.dumps(per_query, ensure_ascii=False, indent=2) + "\n")
        overall = summarize_retrieval(per_query, policy)
        locales = {locale: summarize_retrieval([row for row in per_query if row["locale"] == locale], policy)
                   for locale in LOCALES}
        report = {
            "config": policy, "partition": "validation", "official_test_read": False,
            "catalog": catalog, "overall_query_weighted": overall, "metrics_by_locale": locales,
            "locale_macro_mean": {metric: fmean(result[metric]["mean"] for result in locales.values()
                                                 if result[metric]["mean"] is not None)
                                  for metric in ("known_exact_recall_at_100", "judgment_coverage_at_10")},
            "checks": {"frozen_candidate_scores_exact": True, "frozen_per_query_metrics_exact": True,
                       "all_returned_products_in_locale_catalog": True, "query_coverage_exact": True,
                       "score_id_order_and_cutoffs_valid": True},
            "execution": {"index_build_seconds": build_seconds, "candidate_parity_batch_seconds": parity_seconds,
                          "catalog_retrieval_batch_seconds": retrieval_seconds,
                          "index_bytes": sum(path.stat().st_size for path in (stage / "index").rglob("*") if path.is_file()),
                          "java_heap_limit": "4 GiB", "duckdb_memory_setting": "4 GB",
                          "note": "Single local batches including JVM startup; reuse verified title export. Timings exclude hashing, Maven, Python evaluation and bootstrap. Not API latency, QPS, concurrent capacity or peak memory."},
            "provenance": {"baseline_report_sha256": file_digest(baseline_path),
                           "products_input": baseline["provenance"]["products_input"],
                           "validation_input": baseline["provenance"]["validation_input"],
                           "reused_title_export": specs["products.tsv"],
                           "source_sha256": {path: file_digest(ROOT / path) for path in (
                               "config/catalog-bm25-title-v1.json", "docs/decisions/0003-full-catalog-evaluation.md",
                               "scripts/catalog_metrics.py", "scripts/run_catalog_bm25.py", "scripts/download_esci.py",
                               "src/main/java/lab/commerce/search/CatalogCli.java",
                               "src/test/java/lab/commerce/search/CatalogCliTest.java", "tests/test_catalog_retrieval.py")},
                           "frozen_source_sha256": baseline["provenance"]["source_sha256"],
                           "jar_sha256": file_digest(JAR), "python": platform.python_version(), "duckdb": duckdb.__version__,
                           "java": subprocess.run(["java", "-version"], capture_output=True, text=True, check=True).stderr.strip()},
            "artifacts": [{"name": name, "bytes": (stage / name).stat().st_size, "sha256": file_digest(stage / name)}
                          for name in ("queries.tsv", "baseline-per-query.csv", "candidate-parity.tsv", "results.tsv", "query-status.tsv", "per-query.json")],
            "limits": "Known positives and incomplete judgments only; no exhaustive relevance, cross-language retrieval, held-out final test, online effect or gain over dense/hybrid claim. Coverage is not precision. The candidate-ranking NDCG values are a separate evaluation track.",
        }
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (stage / "manifest.json").write_text(serialized)
        stage.rename(args.output_dir)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    print(json.dumps({"overall": overall, "by_locale": locales}, indent=2))
    print(f"Aggregate report: {args.report}")


if __name__ == "__main__":
    main()
