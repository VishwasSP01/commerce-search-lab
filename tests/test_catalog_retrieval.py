"""Full-catalog retrieval checks on small fixtures, never on test-set labels."""

import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from catalog_metrics import evaluate_retrieval, mean_interval
from run_bm25 import JAR, encode
from run_catalog_bm25 import catalog_java, validate_catalog_membership


def tsv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


class RetrievalMetricTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.results = self.root / "results.tsv"
        self.status = self.root / "status.tsv"
        self.groups = {("us", 1): {"query": "shoe", "labels": {"A": "E", "B": "E", "C": "I"}}}
        self.terms = {("us", 1): 1}
        self.write([("us", 1, 1, "unknown", 2), ("us", 1, 2, "A", 1)], [("us", 1, 1, 2)])

    def write(self, results, status):
        tsv(self.results, ["product_locale", "query_id", "rank", "product_id", "score"], results)
        tsv(self.status, ["product_locale", "query_id", "analyzed_terms", "returned"], status)

    def evaluate(self):
        return evaluate_retrieval(self.groups, self.results, self.status, self.terms)

    def test_unjudged_hits_keep_rank_and_exact_denominator_uses_all_known_positives(self):
        row = self.evaluate()[0]
        self.assertEqual(row["known_exact_recall_at_100"], .5)
        self.assertEqual(row["judgment_coverage_at_10"], .5)
        self.assertEqual(row["returned_at_10"], 2)
        self.assertEqual(row["top_10_slot_fill"], .2)

    def test_irrelevant_is_judged_and_substitute_complement_are_not_exact(self):
        self.groups[("us", 1)]["labels"].update({"unknown": "C", "A": "S"})
        row = self.evaluate()[0]
        self.assertEqual(row["known_exact_recall_at_100"], 0)
        self.assertEqual(row["judgment_coverage_at_10"], 1)
        self.groups[("us", 1)]["labels"]["B"] = "I"
        row = self.evaluate()[0]
        self.assertIsNone(row["known_exact_recall_at_100"])

    def test_empty_results_remain_observations_and_missing_status_fails(self):
        self.write([], [("us", 1, 1, 0)])
        row = self.evaluate()[0]
        self.assertEqual(row["known_exact_recall_at_100"], 0)
        self.assertEqual(row["judgment_coverage_at_10"], 0)
        self.write([], [])
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_rank_identity_score_order_and_query_status_drift_fail(self):
        bad_results = [[("us", 1, 2, "A", 1)],
                       [("us", 1, 1, "A", 2), ("us", 1, 2, "A", 1)],
                       [("us", 1, 1, "A", 1), ("us", 1, 2, "B", 2)],
                       [("us", 1, 1, "B", 1), ("us", 1, 2, "A", 1)],
                       [("us", 1, 1, "A", "nan")], [("jp", 1, 1, "A", 1)]]
        for rows in bad_results:
            with self.subTest(rows=rows):
                self.write(rows, [("us", 1, 1, len(rows))])
                with self.assertRaises(ValueError):
                    self.evaluate()
        self.write([], [("us", 1, 2, 0)])
        with self.assertRaises(ValueError):
            self.evaluate()
        self.write([], [("us", 1, 1, 1)])
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_bootstrap_keeps_query_groups_and_excludes_undefined_recall(self):
        rows = [{"group": "a", "metric": 0}, {"group": "a", "metric": 1},
                {"group": "b", "metric": .5}, {"group": "c", "metric": None}]
        result = mean_interval(rows, "metric", 100, 42)
        self.assertEqual(result, {"mean": .5, "ci95": [.5, .5], "defined_queries": 3, "normalized_query_groups": 2})
        undefined = mean_interval([rows[-1]], "metric", 100, 42)
        self.assertIsNone(undefined["mean"])
        self.assertEqual(undefined["defined_queries"], 0)

    def test_catalog_join_checks_locale_and_preserves_numeric_looking_ids(self):
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE products(product_locale VARCHAR, product_id VARCHAR)")
            connection.execute("INSERT INTO products VALUES ('us', '001'), ('jp', '002')")
            self.write([("us", 1, 1, "001", 1)], [("us", 1, 1, 1)])
            self.assertEqual(validate_catalog_membership(connection, self.results), 1)
            self.write([("us", 1, 1, "002", 1)], [("us", 1, 1, 1)])
            with self.assertRaises(ValueError):
                validate_catalog_membership(connection, self.results)


@unittest.skipUnless(JAR.is_file() or os.environ.get("REQUIRE_BM25_JAR") == "1", "Build the Java artifact first")
class PackagedCatalogTests(unittest.TestCase):
    def test_actual_retrieval_can_return_unjudged_products_and_transports_empty_queries(self):
        self.assertTrue(JAR.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            products, queries, groups, terms = [], [], {}, {}
            for qid, (locale, query, title) in enumerate([
                ("us", "shoe", "red\nshoes"), ("es", "zapato", "zapatos rojos"),
                ("jp", "シューズ", "赤い\tランニングシューズ")], 1):
                products.extend(f"{locale}\t{pid}\t{encode(title)}" for pid in ["Z", "A"])
                queries.append(f"{locale}\t{qid}\t{encode(query)}")
                groups[(locale, qid)] = {"query": query, "labels": {"Z": "E"}}
                terms[(locale, qid)] = 1
            queries.append(f"es\t4\t{encode('no como todos')}")
            groups[("es", 4)] = {"query": "no como todos", "labels": {"Z": "E"}}
            terms[("es", 4)] = 0
            (root / "products").write_text("\n".join(products) + "\n", encoding="utf-8")
            (root / "queries").write_text("\n".join(queries) + "\n", encoding="utf-8")
            self.assertEqual(catalog_java("index", str(root / "products"), str(root / "index"), "1.2", ".75")["products"], "6")
            metadata = catalog_java("retrieve", str(root / "index"), str(root / "queries"), str(root / "results"),
                                    str(root / "status"), "100", "1.2", ".75")
            self.assertEqual(metadata["queries"], "4")
            metrics = evaluate_retrieval(groups, root / "results", root / "status", terms)
            for row in metrics:
                if row["query_id"] == 4:
                    self.assertEqual(row["returned_at_100"], 0)
                    self.assertEqual(row["known_exact_recall_at_100"], 0)
                else:
                    self.assertEqual(row["known_exact_recall_at_100"], 1)
                    self.assertEqual(row["judgment_coverage_at_10"], .5)
                    self.assertEqual(row["returned_at_100"], 2)
            with (root / "results").open() as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    if row["rank"] == "1":
                        self.assertEqual(row["product_id"], "A")


if __name__ == "__main__":
    unittest.main()
