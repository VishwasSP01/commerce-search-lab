"""Build the pinned title index and evaluate only the prepared validation set."""

import argparse
import base64
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

from audit_esci import normalize_query, records
from download_esci import ROOT, SOURCE, verify_file
from prepare_esci_splits import digest, file_digest
from ranking_metrics import ndcg_at_k, rank_candidates, summarize

CONFIG = ROOT / "config/bm25-title-v1.json"
JAR = ROOT / "target/commerce-search-baseline-0.1.0-all.jar"
LOCALES = ("es", "jp", "us")


def encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def check_product_id(product_id: str) -> None:
    if not product_id or any(char in product_id for char in "\t\r\n,"):
        raise ValueError("Product ID cannot be empty or contain protocol delimiters")


def validation_groups(rows: list[dict]) -> dict:
    groups = {}
    for row in rows:
        if row["evaluation_split"] != "validation" or row["split"] != "train":
            raise ValueError("This baseline accepts prepared validation rows only")
        check_product_id(row["product_id"])
        key = (row["product_locale"], row["query_id"])
        group = groups.setdefault(key, {"query": row["query"], "labels": {}})
        if group["query"] != row["query"] or row["product_id"] in group["labels"]:
            raise ValueError("Inconsistent query text or duplicated candidate")
        group["labels"][row["product_id"]] = row["esci_label"]
    if not groups:
        raise ValueError("Validation set is empty")
    return groups


def evaluate(groups: dict, scores_path: Path, config: dict) -> list[dict]:
    scores, terms = {}, {}
    with scores_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            key = (row["product_locale"], int(row["query_id"]))
            product_id, score = row["product_id"], float(row["score"])
            count = int(row["analyzed_terms"])
            if key not in groups or product_id not in groups[key]["labels"]:
                raise ValueError("Unjudged or unexpected score identity")
            if product_id in scores.setdefault(key, {}) or not math.isfinite(score) or score < 0 or count < 0:
                raise ValueError("Duplicate or invalid score")
            if key in terms and terms[key] != count:
                raise ValueError("Inconsistent analyzed term count")
            scores[key][product_id] = score
            terms[key] = count
    if set(scores) != set(groups):
        raise ValueError("Missing query scores")
    result = []
    for key, group in sorted(groups.items()):
        if set(scores[key]) != set(group["labels"]):
            raise ValueError("Missing candidate scores")
        gains = {product_id: config["gain"][label] for product_id, label in group["labels"].items()}
        result.append({
            "locale": key[0], "query_id": key[1],
            "group": digest([key[0], normalize_query(group["query"])]),
            "bm25": ndcg_at_k(rank_candidates(scores[key]), gains),
            "reference": ndcg_at_k(sorted(gains), gains),
            "terms": terms[key], "all_zero": all(score == 0 for score in scores[key].values()),
            "zero_ideal": not any(gains.values()), "candidates": len(gains),
        })
    return result


def java(*args: str) -> dict:
    result = subprocess.run(["java", "-Xmx4g", "--enable-native-access=ALL-UNNAMED", "-jar", str(JAR), *args],
                            check=True, text=True, stdout=subprocess.PIPE)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw/esci")
    parser.add_argument("--split-dir", type=Path, default=ROOT / "data/processed/esci-ranking-v1")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/bm25-title-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/bm25-title-v1.json")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("Output already exists; use a fresh --output-dir to reproduce without overwriting evidence")
    config = json.loads(CONFIG.read_text())
    if config["evaluation_partition"] != "validation" or config["document_text"] != "product_title only":
        raise ValueError("Unsupported experiment configuration")
    source = json.loads(SOURCE.read_text())
    product_spec = next(file for file in source["files"] if file["name"].endswith("products.parquet"))
    product_input = verify_file(args.raw_dir / product_spec["name"], product_spec)
    split_manifest = json.loads((args.split_dir / "manifest.json").read_text())
    if split_manifest["provenance"]["revision"] != source["revision"] or any(split_manifest["checks"].values()):
        raise ValueError("Split provenance or verification does not match the source")
    validation_spec = next(file for file in split_manifest["files"] if file["name"] == "validation.parquet")
    validation_input = verify_file(args.split_dir / "validation.parquet", validation_spec)
    # Build the current source, preventing use of a stale Java artifact.
    subprocess.run([str(ROOT / "mvnw"), "--batch-mode", "--no-transfer-progress", "-q", "-DskipTests", "package"],
                   cwd=ROOT, check=True)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bm25-", dir=args.output_dir.parent) as temporary:
        stage = Path(temporary) / "run"
        stage.mkdir()
        with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection:
            connection.read_parquet(str(args.raw_dir / product_spec["name"])).create_view("products")
            counts = records(connection, """
                SELECT product_locale, count(*) AS products, count(DISTINCT product_id) AS unique_ids,
                       count(*) FILTER (WHERE product_id IS NULL OR product_title IS NULL) AS null_rows
                FROM products GROUP BY product_locale ORDER BY product_locale
            """)
            if {row["product_locale"] for row in counts} != set(LOCALES) or any(
                row["products"] != row["unique_ids"] or row["null_rows"] for row in counts
            ):
                raise ValueError("Catalog has invalid locales, duplicate identities, or missing required fields")
            product_path = stage / "products.tsv"
            cursor = connection.execute("SELECT product_locale, product_id, product_title FROM products ORDER BY product_locale, product_id")
            with product_path.open("w", encoding="utf-8", newline="") as handle:
                while batch := cursor.fetchmany(10_000):
                    for locale, product_id, title in batch:
                        check_product_id(product_id)
                        handle.write(f"{locale}\t{product_id}\t{encode(title)}\n")
            connection.read_parquet(str(args.split_dir / "validation.parquet")).create_view("validation")
            rows = records(connection, """
                SELECT product_locale, query_id, query, product_id, esci_label, split, evaluation_split
                FROM validation ORDER BY product_locale, query_id, product_id
            """)
        groups = validation_groups(rows)
        expected_counts = {(row["product_locale"], row["queries"], row["judgments"])
                           for row in split_manifest["counts"] if row["evaluation_split"] == "validation"}
        actual_counts = {(locale, sum(key[0] == locale for key in groups),
                          sum(len(group["labels"]) for key, group in groups.items() if key[0] == locale))
                         for locale in LOCALES}
        if actual_counts != expected_counts:
            raise ValueError("Validation coverage differs from the split manifest")
        query_path = stage / "queries.tsv"
        with query_path.open("w", encoding="utf-8", newline="") as handle:
            for (locale, query_id), group in sorted(groups.items()):
                handle.write(f"{locale}\t{query_id}\t{encode(group['query'])}\t{','.join(sorted(group['labels']))}\n")
        print(f"Exported {sum(row['products'] for row in counts):,} titles and {len(groups):,} validation queries", flush=True)
        started = time.perf_counter()
        metadata = java("index", str(product_path), str(stage / "index"), str(config["k1"]), str(config["b"]))
        index_seconds = time.perf_counter() - started
        if metadata.get("lucene") != config["lucene_version"] or int(metadata["products"]) != sum(row["products"] for row in counts):
            raise ValueError("Java engine version or indexed document count differs from the experiment")
        started = time.perf_counter()
        score_metadata = java("score", str(stage / "index"), str(query_path), str(stage / "scores.tsv"),
                              str(config["k1"]), str(config["b"]))
        scoring_seconds = time.perf_counter() - started
        if int(score_metadata["queries"]) != len(groups):
            raise ValueError("Java query coverage mismatch")
        per_query = evaluate(groups, stage / "scores.tsv", config)
        with (stage / "per-query.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=per_query[0].keys(), lineterminator="\n")
            writer.writeheader()
            writer.writerows(per_query)
        bootstrap = config["confidence"]
        metrics = {locale: summarize([row for row in per_query if row["locale"] == locale],
                                    bootstrap["resamples"], bootstrap["seed"]) for locale in LOCALES}
        overall = summarize(per_query, bootstrap["resamples"], bootstrap["seed"])
        report = {
            "config": config, "partition": "validation", "official_test_scored": False,
            "catalog": counts, "metrics_by_locale": metrics, "overall_query_weighted": overall,
            "locale_macro_mean": {key: fmean(metrics[locale][key] for locale in LOCALES)
                                  for key in ("bm25_ndcg_at_10", "reference_ndcg_at_10", "delta_ndcg_at_10")},
            "execution": {"index_build_seconds": index_seconds, "candidate_scoring_batch_seconds": scoring_seconds,
                          "index_bytes": sum(path.stat().st_size for path in (stage / "index").rglob("*") if path.is_file()),
                          "java_heap_limit": "4 GiB", "duckdb_memory_setting": "4 GB",
                          "note": "Single local batch; includes JVM startup; not an API latency or load benchmark"},
            "provenance": {
                "repository": source["repository"], "revision": source["revision"],
                "products_input": product_input, "validation_input": validation_input,
                "split_manifest_sha256": file_digest(args.split_dir / "manifest.json"),
                "python": platform.python_version(), "duckdb": duckdb.__version__,
                "java": subprocess.run(["java", "-version"], capture_output=True, text=True, check=True).stderr.strip(),
                "source_sha256": {path: file_digest(ROOT / path) for path in (
                    "config/bm25-title-v1.json", "pom.xml", "scripts/run_bm25.py", "scripts/ranking_metrics.py",
                    "scripts/audit_esci.py", "scripts/prepare_esci_splits.py",
                    "src/main/java/lab/commerce/search/Bm25Cli.java", "uv.lock")},
                "jar_sha256": file_digest(JAR),
            },
            "artifacts": [{"name": name, "bytes": (stage / name).stat().st_size, "sha256": file_digest(stage / name)}
                          for name in ("products.tsv", "queries.tsv", "scores.tsv", "per-query.csv")],
        }
        serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        (stage / "manifest.json").write_text(serialized)
        stage.rename(args.output_dir)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    print(json.dumps({"overall": overall, "per_locale": metrics}, indent=2))
    print(f"Aggregate report: {args.report}")


if __name__ == "__main__":
    main()
