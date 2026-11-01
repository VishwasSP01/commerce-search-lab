import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_bm25 import evaluate, validation_groups


class CandidateEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"product_locale": "us", "query_id": 1, "query": "shoe", "product_id": product,
                      "esci_label": label, "split": "train", "evaluation_split": "validation"}
                     for product, label in (("A", "I"), ("B", "E"))]
        self.config = {"gain": {"I": 0, "E": 1}}

    def test_test_and_training_rows_are_rejected(self):
        for partition in ("test", "train", "excluded"):
            rows = [dict(self.rows[0], evaluation_split=partition)]
            with self.assertRaisesRegex(ValueError, "validation rows only"):
                validation_groups(rows)

    def test_score_coverage_and_duplicate_detection(self):
        for values in ([('A', 1)], [('A', 1), ('A', 1), ('B', 0)], [('A', 1), ('B', 0), ('extra', 2)]):
            with self.subTest(values=values), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "scores.tsv"
                self.write_scores(path, values)
                with self.assertRaises(ValueError):
                    evaluate(validation_groups(self.rows), path, self.config)

    def test_actual_scores_determine_order_and_reference_is_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scores.tsv"
            self.write_scores(path, [('A', 0), ('B', 1)])
            result = evaluate(validation_groups(self.rows), path, self.config)[0]
            self.assertEqual(result["bm25"], 1)
            self.assertLess(result["reference"], result["bm25"])
            self.assertEqual(result["candidates"], 2)

    @staticmethod
    def write_scores(path, values):
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["product_locale", "query_id", "product_id", "score", "analyzed_terms"])
            writer.writerows(["us", 1, product, score, 1] for product, score in values)


if __name__ == "__main__":
    unittest.main()
