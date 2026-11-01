import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ranking_metrics import ndcg_at_k, rank_candidates, summarize


class RankingMetricsTests(unittest.TestCase):
    def test_linear_esci_gains_match_hand_calculation(self):
        gains = {"E": 1, "S": .1, "C": .01, "I": 0}
        actual = ndcg_at_k(["S", "E", "I", "C"], gains)
        expected = (.1 + 1 / math.log2(3) + .01 / math.log2(5)) / (1 + .1 / math.log2(3) + .01 / 2)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(ndcg_at_k(["E", "S", "C", "I"], gains), 1)

    def test_zero_ideal_and_cutoff(self):
        self.assertEqual(ndcg_at_k(["A", "B"], {"A": 0, "B": 0}), 0)
        self.assertEqual(ndcg_at_k(["A", "B"], {"A": 0, "B": 1}, k=1), 0)

    def test_ties_use_product_id_and_never_labels(self):
        self.assertEqual(rank_candidates({"B": 0, "A": 0, "C": 2}), ["C", "A", "B"])

    def test_missing_duplicate_and_invalid_scores_are_rejected(self):
        for ranked in (["A"], ["A", "A"], ["A", "C"]):
            with self.assertRaises(ValueError):
                ndcg_at_k(ranked, {"A": 1, "B": .1})
        for bad in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                rank_candidates({"A": bad})

    def test_paired_bootstrap_retains_identical_method_differences(self):
        rows = [{"group": "same", "bm25": .8, "reference": .8, "zero_ideal": False,
                 "terms": 1, "all_zero": False, "candidates": 2},
                {"group": "same", "bm25": .2, "reference": .2, "zero_ideal": False,
                 "terms": 1, "all_zero": False, "candidates": 2}]
        result = summarize(rows, 100, 123)
        self.assertEqual(result["normalized_query_groups"], 1)
        self.assertEqual(result["bm25_ci95"], [.5, .5])
        self.assertEqual(result["paired_delta_ci95"], [0, 0])
        self.assertEqual(result, summarize(rows, 100, 123))


if __name__ == "__main__":
    unittest.main()
