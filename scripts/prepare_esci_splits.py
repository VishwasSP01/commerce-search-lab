"""Create reproducible ESCI ranking splits and verify the exported rows."""

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import platform
import tempfile
import unicodedata

import duckdb

from audit_esci import normalize_query, records
from download_esci import DEFAULT_DATA_DIR, ROOT, SOURCE, verify_file

POLICY = ROOT / "config/esci-split-policy.json"
PARTITIONS = ("train", "validation", "test", "excluded")
ASSIGNMENT_FIELDS = ("product_locale", "query_id", "official_split", "query_group_sha256",
                     "evaluation_split", "exclusion_reason")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def assign_queries(queries: list[tuple], protected: list[tuple], policy: dict) -> list[dict]:
    """Inputs contain identities only: locale, ID, text, and official split."""
    if policy.get("subset") != "small_version = 1":
        raise ValueError("Only the ranking subset is supported by this policy version")
    if not policy["locales"] or len(set(policy["locales"])) != len(policy["locales"]):
        raise ValueError("locales must contain distinct locale names")
    if not set(policy["locales"]) <= {"es", "jp", "us"} or not policy["seed"]:
        raise ValueError("Unexpected locale names or empty seed")
    try:
        fraction = Decimal(policy["validation_fraction"])
    except InvalidOperation as error:
        raise ValueError("validation_fraction must be a decimal number") from error
    if not fraction.is_finite() or not 0 < fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    protected_ids = {(locale, query_id) for locale, query_id, _ in protected}
    protected_text = {(locale, normalize_query(text)) for locale, _, text in protected}
    seen = set()
    groups = {locale: {} for locale in policy["locales"]}
    assignments = []
    # The source ordering and labels have no role in selecting validation groups.
    for locale, query_id, text, split in sorted(queries, key=lambda row: (row[0], row[1], row[3])):
        key = (locale, query_id, split)
        if key in seen:
            raise ValueError("Repeated query identity or multiple query texts for one identity")
        seen.add(key)
        if locale not in groups or split not in ("train", "test") or query_id is None or not text:
            raise ValueError("Unexpected locale, split, or empty query identity")
        normalized = normalize_query(text)
        if not normalized:
            raise ValueError("Query normalizes to empty text")
        id_match = (locale, query_id) in protected_ids
        text_match = (locale, normalized) in protected_text
        reason = ""
        if split == "test":
            if not (id_match and text_match):
                raise ValueError("Ranking test identity is absent from the protected registry")
            partition = "test"
        elif id_match or text_match:
            partition = "excluded"
            reason = "heldout_id_and_text" if id_match and text_match else (
                "heldout_id" if id_match else "heldout_normalized_text")
        else:
            partition = "train"
        assignment = dict(zip(ASSIGNMENT_FIELDS, (
            locale, query_id, split, digest([locale, normalized]), partition, reason)))
        assignments.append(assignment)
        if partition == "train":
            groups[locale].setdefault(normalized, []).append(assignment)

    # If one ID is excluded, also exclude every equivalent training text group.
    excluded_groups = {a["query_group_sha256"] for a in assignments if a["evaluation_split"] == "excluded"}
    for locale_groups in groups.values():
        for normalized in list(locale_groups):
            if locale_groups[normalized][0]["query_group_sha256"] in excluded_groups:
                for assignment in locale_groups.pop(normalized):
                    assignment.update(evaluation_split="excluded", exclusion_reason="equivalent_excluded_group")
    for locale, locale_groups in groups.items():
        if len(locale_groups) < 2:
            raise ValueError(f"Need at least two eligible query groups for locale {locale}")
        ordered = sorted(locale_groups, key=lambda text: (digest([policy["seed"], locale, text]), text))
        n_validation = max(1, min(len(ordered) - 1, int(Decimal(len(ordered)) * fraction)))
        for normalized in ordered[:n_validation]:
            for assignment in locale_groups[normalized]:
                assignment["evaluation_split"] = "validation"
    return assignments


def verify_exports(connection, directory: Path, policy: dict) -> dict:
    """Re-read written artifacts; compare original rows and independently normalize text."""
    checks = {}
    identities = {}
    for partition in PARTITIONS:
        connection.read_parquet(str(directory / f"{partition}.parquet")).create_view(f"export_{partition}", replace=True)
        checks[f"wrong_partition_rows_{partition}"] = connection.execute(
            f"SELECT count(*) FROM export_{partition} WHERE evaluation_split IS NULL OR evaluation_split <> ?",
            [partition],
        ).fetchone()[0]
        rows = connection.execute(
            f"SELECT DISTINCT product_locale, query_id, query FROM export_{partition}"
        ).fetchall()
        identities[partition] = {
            "ids": {(locale, query_id) for locale, query_id, _ in rows},
            "normalized": {(locale, normalize_query(text)) for locale, _, text in rows},
        }
        if partition != "excluded" and {row[0] for row in rows} != set(policy["locales"]):
            raise ValueError(f"A required locale is missing from {partition}")
    connection.execute("CREATE OR REPLACE TEMP VIEW all_exports AS " +
                       " UNION ALL ".join(f"SELECT * FROM export_{partition}" for partition in PARTITIONS))
    original = "SELECT * FROM examples WHERE small_version = 1"
    exported = "SELECT * EXCLUDE(evaluation_split) FROM all_exports"
    original_test = original + " AND split = 'test'"
    exported_test = "SELECT * EXCLUDE(evaluation_split) FROM export_test"
    for name, left, right in (
        ("missing_ranking_rows", original, exported),
        ("extra_or_changed_ranking_rows", exported, original),
        ("missing_test_rows", original_test, exported_test),
        ("extra_or_changed_test_rows", exported_test, original_test),
    ):
        checks[name] = connection.execute(f"SELECT count(*) FROM (({left}) EXCEPT ALL ({right}))").fetchone()[0]
    checks["wrong_original_split_rows"] = connection.execute("""
        SELECT count(*) FROM all_exports
        WHERE (evaluation_split = 'test' AND split <> 'test')
           OR (evaluation_split <> 'test' AND split <> 'train')
    """).fetchone()[0]
    checks["query_groups_split_across_partitions"] = connection.execute("""
        SELECT count(*) FROM (
            SELECT product_locale, query_id, split FROM all_exports
            GROUP BY ALL HAVING count(DISTINCT evaluation_split) > 1
        )
    """).fetchone()[0]
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"),
                        ("train", "excluded"), ("validation", "excluded")):
        for identity in ("ids", "normalized"):
            checks[f"{left}_{right}_{identity}_overlap"] = len(
                identities[left][identity] & identities[right][identity])
    heldout = connection.execute(
        "SELECT DISTINCT product_locale, query_id, query FROM examples WHERE split = 'test'"
    ).fetchall()
    protected = {
        "ids": {(locale, query_id) for locale, query_id, _ in heldout},
        "normalized": {(locale, normalize_query(text)) for locale, _, text in heldout},
    }
    for partition in ("train", "validation"):
        for identity in ("ids", "normalized"):
            checks[f"{partition}_full_test_{identity}_overlap"] = len(
                identities[partition][identity] & protected[identity])
    failures = {key: value for key, value in checks.items() if value}
    if failures:
        raise ValueError(f"Split verification failed: {failures}")
    return checks


def build_splits(connection, directory: Path, policy: dict) -> dict:
    queries = connection.execute("""
        SELECT DISTINCT product_locale, query_id, query, split
        FROM examples WHERE small_version = 1
    """).fetchall()
    protected = connection.execute("""
        SELECT DISTINCT product_locale, query_id, query FROM examples WHERE split = 'test'
    """).fetchall()
    assignments = assign_queries(queries, protected, policy)
    assignment_path = directory / "query-assignments.csv"
    with assignment_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSIGNMENT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(assignments)
    connection.read_csv(str(assignment_path), header=True).create_view("assignments", replace=True)
    for partition in PARTITIONS:
        connection.sql(f"""
            SELECT e.*, a.evaluation_split
            FROM examples e JOIN assignments a
              ON e.product_locale = a.product_locale AND e.query_id = a.query_id
             AND e.split = a.official_split
            WHERE e.small_version = 1 AND a.evaluation_split = '{partition}'
            ORDER BY e.product_locale, e.query_id, e.example_id
        """).write_parquet(str(directory / f"{partition}.parquet"), compression="zstd")
    checks = verify_exports(connection, directory, policy)
    counts = records(connection, """
        SELECT product_locale, evaluation_split, count(*) AS judgments,
               count(DISTINCT query_id) AS queries
        FROM all_exports GROUP BY ALL ORDER BY product_locale, evaluation_split
    """)
    return {
        "policy": policy,
        "original_ranking_counts": records(connection, """
            SELECT product_locale, split, count(*) AS judgments, count(DISTINCT query_id) AS queries
            FROM examples WHERE small_version = 1 GROUP BY ALL ORDER BY product_locale, split
        """),
        "counts": counts,
        "normalized_groups": records(connection, """
            SELECT product_locale, evaluation_split, count(DISTINCT query_group_sha256) AS groups
            FROM assignments GROUP BY ALL ORDER BY product_locale, evaluation_split
        """),
        "exclusions": records(connection, """
            SELECT product_locale, exclusion_reason, count(*) AS queries
            FROM assignments WHERE evaluation_split = 'excluded'
            GROUP BY ALL ORDER BY product_locale, exclusion_reason
        """),
        "protected_test_query_keys": len({(locale, query_id) for locale, query_id, _ in protected}),
        "checks": checks,
        "files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": file_digest(path)}
            for path in sorted(directory.iterdir())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/esci-ranking-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/esci-split-report.json")
    args = parser.parse_args()
    policy = json.loads(POLICY.read_text())
    source = json.loads(SOURCE.read_text())
    spec = next(file for file in source["files"] if file["name"].endswith("examples.parquet"))
    verified = verify_file(args.data_dir / spec["name"], spec)
    # Stage all outputs so failed verification never publishes a partial dataset.
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".esci-splits-", dir=args.output_dir.parent) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection:
            connection.read_parquet(str(args.data_dir / spec["name"])).create_view("examples_raw")
            connection.execute("CREATE TEMP TABLE examples AS SELECT * FROM examples_raw")
            report = build_splits(connection, stage, policy)
        report["provenance"] = {
            "repository": source["repository"], "revision": source["revision"], "input": verified,
            "python": platform.python_version(), "duckdb": duckdb.__version__,
            "unicode_version": unicodedata.unidata_version,
            "split_script_sha256": file_digest(Path(__file__)),
            "normalization_module_sha256": file_digest(ROOT / "scripts/audit_esci.py"),
            "policy_sha256": file_digest(POLICY),
            "dependency_lock_sha256": file_digest(ROOT / "uv.lock"),
        }
        serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        (stage / "manifest.json").write_text(serialized)
        if args.output_dir.exists():
            existing = json.loads((args.output_dir / "manifest.json").read_text())
            if existing != report:
                raise ValueError("Existing output differs; use a new --output-dir and review the policy/version")
            for artifact in report["files"]:
                verify_file(args.output_dir / artifact["name"], artifact)
            print("Existing output independently regenerated and verified.")
        else:
            stage.rename(args.output_dir)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    print(f"Verified splits: {args.output_dir}")
    print(f"Aggregate report: {args.report}")
    print(json.dumps(report["counts"], indent=2))


if __name__ == "__main__":
    main()
