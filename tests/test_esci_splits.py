"""Split-integrity tests using fabricated fixtures, never benchmark measurements."""

from pathlib import Path
import random
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_esci_splits import assign_queries, build_splits, verify_exports


def fixture_policy(locales=("us",)):
    return {"version": "fixture-v1", "subset": "small_version = 1", "locales": list(locales),
            "validation_fraction": "0.10", "seed": "fixture-seed"}


def fixture_queries():
    return [("us", number, f"query {number}", "train") for number in range(1, 21)] + [
        ("us", 100, "held out", "test")]


class AssignmentTests(unittest.TestCase):
    def test_selection_is_independent_of_input_order(self):
        queries = fixture_queries()
        expected = assign_queries(queries, [("us", 100, "held out")], fixture_policy())
        random.Random(17).shuffle(queries)
        actual = assign_queries(queries, [("us", 100, "held out")], fixture_policy())
        self.assertEqual(actual, expected)
        self.assertEqual(sum(row["evaluation_split"] == "validation" for row in actual), 2)

    def test_normalized_variants_stay_together(self):
        queries = fixture_queries() + [("us", 21, "ＱＵＥＲＹ   1", "train")]
        result = assign_queries(queries, [("us", 100, "held out")], fixture_policy())
        matching = [row for row in result if row["query_id"] in (1, 21)]
        self.assertEqual(len({row["query_group_sha256"] for row in matching}), 1)
        self.assertEqual(len({row["evaluation_split"] for row in matching}), 1)

    def test_full_test_registry_protects_queries_outside_ranking_test(self):
        result = assign_queries(fixture_queries(), [("us", 100, "held out"), ("us", 999, "QUERY 1")], fixture_policy())
        row = next(row for row in result if row["query_id"] == 1)
        self.assertEqual(row["evaluation_split"], "excluded")
        self.assertEqual(row["exclusion_reason"], "heldout_normalized_text")

    def test_id_collision_excludes_equivalent_training_group(self):
        queries = fixture_queries() + [("us", 21, "ＱＵＥＲＹ 1", "train")]
        result = assign_queries(queries, [("us", 100, "held out"), ("us", 1, "different text")], fixture_policy())
        self.assertEqual({row["query_id"] for row in result if row["evaluation_split"] == "excluded"}, {1, 21})

    def test_text_matching_is_scoped_to_locale(self):
        queries = fixture_queries() + [
            ("es", 201, "query 1", "train"), ("es", 202, "otro", "train"),
            ("es", 203, "tercero", "train"), ("es", 300, "prueba", "test")]
        protected = [("us", 100, "held out"), ("us", 999, "query 1"), ("es", 300, "prueba")]
        result = assign_queries(queries, protected, fixture_policy(("us", "es")))
        spanish = next(row for row in result if row["query_id"] == 201)
        self.assertIn(spanish["evaluation_split"], ("train", "validation"))

    def test_multiple_texts_for_one_query_identity_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "multiple query texts"):
            assign_queries(fixture_queries() + [("us", 1, "different", "train")],
                           [("us", 100, "held out")], fixture_policy())

    def test_unusable_fraction_and_tiny_locale_fail_explicitly(self):
        for fraction in ("0", "1", "-0.1", "NaN", "Infinity", "invalid"):
            with self.subTest(fraction=fraction):
                policy = fixture_policy()
                policy["validation_fraction"] = fraction
                with self.assertRaises(ValueError):
                    assign_queries(fixture_queries(), [("us", 100, "held out")], policy)
        with self.assertRaisesRegex(ValueError, "at least two"):
            assign_queries([("us", 1, "one", "train"), ("us", 100, "held out", "test")],
                           [("us", 100, "held out")], fixture_policy())


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.connection = duckdb.connect(config={"threads": 1})
        self.addCleanup(self.connection.close)
        self.connection.execute("""
            CREATE TABLE examples (
                example_id BIGINT, query VARCHAR, query_id BIGINT, product_id VARCHAR,
                product_locale VARCHAR, esci_label VARCHAR, small_version BIGINT,
                large_version BIGINT, split VARCHAR
            )
        """)
        rows = []
        for locale, query_id, text, split in fixture_queries():
            for candidate, label in ((0, "E"), (1, "I")):
                rows.append((query_id * 10 + candidate, text, query_id, f"product {candidate}",
                             locale, label, 1, 1, split))
        # This held-out identity is outside the ranking subset, but must protect query 1.
        rows.append((9990, "ＱＵＥＲＹ 1", 999, "other", "us", "S", 0, 1, "test"))
        self.connection.executemany("INSERT INTO examples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_complete_query_groups_test_rows_and_labels_are_preserved(self):
        report = build_splits(self.connection, self.directory, fixture_policy())
        self.assertTrue(all(value == 0 for value in report["checks"].values()))
        counts = {row["evaluation_split"]: row for row in report["counts"]}
        self.assertEqual(counts["excluded"]["judgments"], 2)
        self.assertEqual(counts["test"]["judgments"], 2)
        self.assertEqual(counts["test"]["queries"], 1)
        self.assertEqual(sum(row["judgments"] for row in report["counts"]), 42)

    def test_assignment_is_unchanged_when_labels_change(self):
        build_splits(self.connection, self.directory, fixture_policy())
        original = (self.directory / "query-assignments.csv").read_bytes()
        self.connection.execute("UPDATE examples SET esci_label = 'S'")
        with tempfile.TemporaryDirectory() as temporary:
            other = Path(temporary)
            build_splits(self.connection, other, fixture_policy())
            self.assertEqual((other / "query-assignments.csv").read_bytes(), original)

    def test_reverification_rejects_modified_test_label(self):
        build_splits(self.connection, self.directory, fixture_policy())
        self.connection.execute("CREATE TEMP TABLE tampered AS SELECT * FROM export_test")
        self.connection.execute("UPDATE tampered SET esci_label = 'I'")
        self.connection.sql("SELECT * FROM tampered").write_parquet(str(self.directory / "test.parquet"))
        with self.assertRaisesRegex(ValueError, "Split verification failed"):
            verify_exports(self.connection, self.directory, fixture_policy())

    def test_complete_rebuild_has_identical_artifact_hashes(self):
        first = build_splits(self.connection, self.directory, fixture_policy())
        with tempfile.TemporaryDirectory() as temporary:
            second = build_splits(self.connection, Path(temporary), fixture_policy())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
