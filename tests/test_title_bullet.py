"""Verify weighted-field behavior through the shipped ranker and strict score protocol."""

import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_bm25 import CONFIG as BASELINE_CONFIG, JAR, encode, evaluate, java, validation_groups
from run_title_bullet import CONFIG, HEADER, adoption_passes, combine_scores, comparison, pair_metrics, read_scores


def write_scores(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


class WeightedProtocolTests(unittest.TestCase):
    def test_weighted_sum_keeps_zero_candidates_and_uses_identity_not_row_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_scores(root / "title", [("us", 1, "A", 2, 1), ("us", 1, "B", 0, 1), ("us", 1, "C", 0, 1)])
            write_scores(root / "bullet", [("us", 1, "C", 0, 1), ("us", 1, "B", 4, 1), ("us", 1, "A", 3, 1)])
            combine_scores(root / "title", root / "bullet", root / "out", .25)
            scores = read_scores(root / "out")
            self.assertEqual(scores, {("us", 1, "A"): (2.75, 1), ("us", 1, "B"): (1, 1), ("us", 1, "C"): (0, 1)})

    def test_missing_extra_duplicate_invalid_and_analyzer_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = [("us", 1, "A", 2, 1), ("us", 1, "B", 0, 1)]
            write_scores(root / "title", original)
            bad_inputs = [original[:1], original + [("jp", 1, "C", 0, 1)], original + original[:1],
                          [("us", 1, "A", "nan", 1), original[1]],
                          [("us", 1, "A", -1, 1), original[1]],
                          [("us", 1, "A", 0, 2), original[1]]]
            for rows in bad_inputs:
                with self.subTest(rows=rows):
                    write_scores(root / "bullet", rows)
                    with self.assertRaises(ValueError):
                        combine_scores(root / "title", root / "bullet", root / "out", .25)
            write_scores(root / "bullet", original)
            for weight in (-.1, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    combine_scores(root / "title", root / "bullet", root / "out", weight)

    def test_pairing_compares_with_title_not_id_control_and_rejects_drift(self):
        row = {"locale": "us", "query_id": 1, "group": "group", "bm25": .6, "reference": .2,
               "terms": 1, "candidates": 2, "zero_ideal": False, "all_zero": False}
        after = dict(row, bm25=.7)
        paired = pair_metrics([row], [after])
        self.assertEqual(paired[0]["reference"], .6)
        summary = comparison(paired, json.loads(CONFIG.read_text()))
        self.assertAlmostEqual(summary["delta_ndcg_at_10"], .1)
        self.assertEqual(summary["title_only_ndcg_at_10"], .6)
        self.assertEqual(summary["improved_queries"], 1)
        self.assertNotIn("reference_ndcg_at_10", summary)
        for field, value in (("query_id", 2), ("group", "different"), ("candidates", 3), ("reference", .3)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                pair_metrics([row], [dict(after, **{field: value})])
        with self.assertRaises(ValueError):
            pair_metrics([row, row], [after])

    def test_cohorts_use_baseline_not_variant_outcome(self):
        row = {"locale": "us", "query_id": 1, "group": "g", "bm25": .3, "reference": .5,
               "terms": 1, "candidates": 2, "zero_ideal": False, "all_zero": False}
        paired = pair_metrics([row], [dict(row, bm25=.9)])[0]
        self.assertTrue(paired["baseline_loss"])
        self.assertFalse(paired["baseline_all_zero"])
        zero = dict(row, all_zero=True, bm25=.5)
        paired = pair_metrics([zero], [dict(zero, all_zero=False, bm25=.8)])[0]
        self.assertTrue(paired["baseline_all_zero"])
        self.assertFalse(paired["baseline_loss"])

    def test_adoption_requires_positive_interval_and_no_locale_mean_regression(self):
        overall = {"paired_delta_ci95": [.001, .02]}
        locales = {"us": {"delta_ndcg_at_10": .02}, "jp": {"delta_ndcg_at_10": 0}}
        self.assertTrue(adoption_passes(overall, locales))
        self.assertFalse(adoption_passes({"paired_delta_ci95": [0, .02]}, locales))
        self.assertFalse(adoption_passes(overall, dict(locales, jp={"delta_ndcg_at_10": -.001})))


@unittest.skipUnless(JAR.is_file() or os.environ.get("REQUIRE_BM25_JAR") == "1", "Build the Java artifact first")
class PackagedWeightedTests(unittest.TestCase):
    def test_multilingual_bullets_rescue_title_zero_while_empty_bullets_preserve_scores(self):
        self.assertTrue(JAR.is_file(), f"Expected packaged Java artifact: {JAR}")
        baseline_config = json.loads(BASELINE_CONFIG.read_text())
        policy = json.loads(CONFIG.read_text())
        examples = {"us": ("shoe", "red\nshoes"), "es": ("zapato", "zapatos rojos"),
                    "jp": ("シューズ", "赤い\tランニングシューズ")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            titles, bullets, empty, queries, labels = [], [], [], [], []
            for qid, (locale, (query, bullet)) in enumerate(examples.items(), 1):
                for product, label in (("A", "I"), ("B", "E"), ("C", "I")):
                    titles.append(f"{locale}\t{product}\t{encode('xyzzy')}")
                    bullets.append(f"{locale}\t{product}\t{encode(bullet if product == 'B' else '')}")
                    empty.append(f"{locale}\t{product}\t")
                    labels.append({"product_locale": locale, "query_id": qid, "query": query,
                                   "product_id": product, "esci_label": label, "split": "train",
                                   "evaluation_split": "validation"})
                queries.append(f"{locale}\t{qid}\t{encode(query)}\tA,B,C")
            (root / "queries").write_text("\n".join(queries) + "\n", encoding="utf-8")
            for name, rows in (("title", titles), ("bullet", bullets), ("empty", empty)):
                (root / name).write_text("\n".join(rows) + "\n", encoding="utf-8")
                metadata = java("index", str(root / name), str(root / f"{name}-index"), "1.2", ".75")
                self.assertEqual(metadata["products"], "9")
                java("score", str(root / f"{name}-index"), str(root / "queries"), str(root / f"{name}-scores"), "1.2", ".75")
            groups = validation_groups(labels)
            before = evaluate(groups, root / "title-scores", baseline_config)
            combine_scores(root / "title-scores", root / "bullet-scores", root / "combined", policy["bullet_weight"])
            after = evaluate(groups, root / "combined", baseline_config)
            for row in before:
                self.assertTrue(row["all_zero"])
                self.assertLess(row["bm25"], 1)
            for row in after:
                self.assertEqual(row["bm25"], 1)
                self.assertFalse(row["all_zero"])
            combine_scores(root / "bullet-scores", root / "empty-scores", root / "unchanged", policy["bullet_weight"])
            self.assertEqual(read_scores(root / "unchanged"), read_scores(root / "bullet-scores"))


if __name__ == "__main__":
    unittest.main()
