"""Audit pinned ESCI data without changing it or creating retrieval results."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import unicodedata

import duckdb

from download_esci import DEFAULT_DATA_DIR, ROOT, SOURCE, verify_file


def records(connection, sql: str) -> list[dict]:
    result = connection.execute(sql)
    names = [column[0] for column in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def one(connection, sql: str) -> dict:
    return records(connection, sql)[0]


def normalize_query(text: str) -> str:
    """Audit identity only; this is not a search-language analyzer."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def query_overlap(connection, where: str) -> dict:
    groups = {split: {"ids": set(), "exact": set(), "normalized": set()} for split in ("train", "test")}
    for locale, query_id, query, split in connection.execute(
        f"SELECT DISTINCT product_locale, query_id, query, split FROM examples WHERE {where}"
    ).fetchall():
        if split not in groups:
            continue
        groups[split]["ids"].add((locale, query_id))
        if query is not None:
            groups[split]["exact"].add((locale, query))
            groups[split]["normalized"].add((locale, normalize_query(query)))
    result = {
        name: len(groups["train"][name] & groups["test"][name])
        for name in ("ids", "exact", "normalized")
    }
    result["by_locale"] = {
        locale: {
            name: sum(key[0] == locale for key in groups["train"][name] & groups["test"][name])
            for name in ("ids", "exact", "normalized")
        }
        for locale in sorted({key[0] for group in groups.values() for key in group["ids"]})
    }
    return result


def audit(connection) -> dict:
    """Input tables/views are examples, products_raw and sources."""
    expected = {
        "examples": {"example_id", "query", "query_id", "product_id", "product_locale", "esci_label", "small_version", "large_version", "split"},
        "products_raw": {"product_id", "product_title", "product_description", "product_bullet_point", "product_brand", "product_color", "product_locale"},
        "sources": {"query_id", "source"},
    }
    schemas = {table: records(connection, f"DESCRIBE {table}") for table in expected}
    for table, fields in expected.items():
        missing = fields - {row["column_name"] for row in schemas[table]}
        if missing:
            raise ValueError(f"Missing required columns in {table}: {sorted(missing)}")

    fields = ["product_title", "product_description", "product_bullet_point", "product_brand", "product_color"]
    indicators = ", ".join(
        f"({field} IS NULL OR trim({field}) = '') AS missing_{field}" for field in fields
    )
    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE products AS SELECT product_locale, product_id, {indicators} FROM products_raw"
    )
    missing_fields = ", ".join(f"count(*) FILTER (WHERE missing_{field}) AS missing_{field}" for field in fields)
    result = {
        "schemas": schemas,
        "sizes": one(connection, """
            SELECT (SELECT count(*) FROM examples) AS example_rows,
                   (SELECT count(DISTINCT query_id) FROM examples) AS distinct_query_ids,
                   (SELECT count(*) FROM products) AS product_rows,
                   (SELECT count(*) FROM sources) AS source_rows
        """),
        "subsets": {},
        "products_by_locale": records(connection, f"""
            SELECT product_locale, count(*) AS rows, count(DISTINCT product_id) AS distinct_product_ids,
                   {missing_fields}
            FROM products GROUP BY product_locale ORDER BY product_locale
        """),
        "integrity": {},
    }
    for name, where in (("all", "TRUE"), ("ranking", "small_version = 1"), ("large", "large_version = 1")):
        result["subsets"][name] = {
            "totals": one(connection, f"""
                SELECT count(*) AS rows, count(DISTINCT query_id) AS distinct_query_ids,
                       count(DISTINCT (product_locale, query_id)) AS locale_query_keys,
                       count(DISTINCT (product_locale, product_id)) AS locale_product_keys
                FROM examples WHERE {where}
            """),
            "by_locale_split": records(connection, f"""
                SELECT product_locale, split, count(*) AS rows, count(DISTINCT query_id) AS queries
                FROM examples WHERE {where}
                GROUP BY product_locale, split ORDER BY product_locale, split
            """),
            "labels": records(connection, f"""
                SELECT product_locale, esci_label, count(*) AS rows FROM examples WHERE {where}
                GROUP BY product_locale, esci_label ORDER BY product_locale, esci_label
            """),
            "train_test_overlap": query_overlap(connection, where),
            "candidate_depth": records(connection, f"""
                SELECT product_locale, split, min(n) AS minimum, max(n) AS maximum,
                       median(n) AS median, avg(n) AS mean
                FROM (SELECT product_locale, split, query_id, count(*) AS n FROM examples
                      WHERE {where} GROUP BY product_locale, split, query_id)
                GROUP BY product_locale, split ORDER BY product_locale, split
            """),
        }

    checks = {
        "duplicate_example_ids": "SELECT count(*) FROM (SELECT example_id FROM examples GROUP BY example_id HAVING count(*) > 1)",
        "duplicate_product_keys": "SELECT count(*) FROM (SELECT product_locale, product_id FROM products GROUP BY ALL HAVING count(*) > 1)",
        "duplicate_query_product_keys": "SELECT count(*) FROM (SELECT product_locale, query_id, product_id FROM examples GROUP BY ALL HAVING count(*) > 1)",
        "conflicting_pair_labels": "SELECT count(*) FROM (SELECT product_locale, query_id, product_id FROM examples GROUP BY ALL HAVING count(DISTINCT esci_label) > 1)",
        "query_ids_with_multiple_texts": "SELECT count(*) FROM (SELECT product_locale, query_id FROM examples GROUP BY ALL HAVING count(DISTINCT query) > 1)",
        "query_ids_used_in_multiple_locales": "SELECT count(*) FROM (SELECT query_id FROM examples GROUP BY query_id HAVING count(DISTINCT product_locale) > 1)",
        "product_ids_used_in_multiple_locales": "SELECT count(*) FROM (SELECT product_id FROM products GROUP BY product_id HAVING count(DISTINCT product_locale) > 1)",
        "unmatched_example_rows": "SELECT count(*) FROM examples e ANTI JOIN products p USING (product_locale, product_id)",
        "unmatched_product_keys": "SELECT count(*) FROM (SELECT DISTINCT e.product_locale, e.product_id FROM examples e ANTI JOIN products p USING (product_locale, product_id))",
        "unreferenced_product_keys": "SELECT count(*) FROM products p ANTI JOIN examples e USING (product_locale, product_id)",
        "duplicate_source_query_ids": "SELECT count(*) FROM (SELECT query_id FROM sources GROUP BY query_id HAVING count(*) > 1)",
        "unmatched_source_rows": "SELECT count(*) FROM examples e ANTI JOIN sources s USING (query_id)",
        "invalid_label_rows": "SELECT count(*) FROM examples WHERE esci_label IS NULL OR esci_label NOT IN ('E', 'S', 'C', 'I')",
        "invalid_split_rows": "SELECT count(*) FROM examples WHERE split IS NULL OR split NOT IN ('train', 'test')",
        "invalid_locale_rows": "SELECT count(*) FROM examples WHERE product_locale IS NULL OR product_locale NOT IN ('us', 'es', 'jp')",
        "invalid_version_rows": "SELECT count(*) FROM examples WHERE small_version IS NULL OR large_version IS NULL OR small_version NOT IN (0, 1) OR large_version NOT IN (0, 1)",
        "small_outside_large_rows": "SELECT count(*) FROM examples WHERE small_version = 1 AND large_version <> 1",
        "null_example_identity_rows": "SELECT count(*) FROM examples WHERE example_id IS NULL OR query_id IS NULL OR product_id IS NULL OR product_locale IS NULL",
        "null_product_identity_rows": "SELECT count(*) FROM products WHERE product_id IS NULL OR product_locale IS NULL",
        "empty_query_rows": "SELECT count(*) FROM examples WHERE query IS NULL OR trim(query) = ''",
    }
    result["integrity"] = {name: connection.execute(sql).fetchone()[0] for name, sql in checks.items()}
    result["sources"] = records(connection, "SELECT source, count(*) AS rows FROM sources GROUP BY source ORDER BY source")
    result["identity_policy"] = {
        "product_key": ["product_locale", "product_id"],
        "query_key": ["product_locale", "query_id"],
        "normalization": "Unicode NFKC, casefold, collapse Unicode whitespace, evaluated within locale",
        "unicode_version": unicodedata.unidata_version,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/esci-data-audit.json")
    args = parser.parse_args()
    source = json.loads(SOURCE.read_text())
    verified = [verify_file(args.data_dir / spec["name"], spec) for spec in source["files"]]
    with duckdb.connect(config={"threads": 4, "memory_limit": "4GB"}) as connection:
        # The examples table is small enough to reuse; large text stays in Parquet.
        connection.read_parquet(str(args.data_dir / source["files"][0]["name"])).create_view("examples_raw")
        connection.execute("CREATE TEMP TABLE examples AS SELECT * FROM examples_raw")
        connection.read_parquet(str(args.data_dir / source["files"][1]["name"])).create_view("products_raw")
        connection.read_csv(str(args.data_dir / source["files"][2]["name"]), header=True).create_view("sources")
        report = audit(connection)
    report["provenance"] = {
        "repository": source["repository"], "revision": source["revision"], "files": verified,
        "python": platform.python_version(), "duckdb": duckdb.__version__,
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "coverage": "Full pinned files; no sampling; no raw product/query text in report",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.output}")
    print(json.dumps({"sizes": report["sizes"], "integrity": report["integrity"],
                      "ranking_overlap": report["subsets"]["ranking"]["train_test_overlap"]}, indent=2))


if __name__ == "__main__":
    main()
