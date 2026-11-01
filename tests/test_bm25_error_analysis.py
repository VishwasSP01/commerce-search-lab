import os
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_bm25_errors import aggregate, analyze_texts, query_diagnostics, sample_cases
from run_bm25 import JAR


def product(title=(), description=(), bullet_point=(), brand=(), color=()):
    return dict(title=list(title), description=list(description), bullet_point=list(bullet_point),
                brand=list(brand), color=list(color))


class ErrorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.group = {"labels": {"A": "E", "B": "I"}}
        self.metric = {"locale": "us", "query_id": 1, "bm25": .5, "reference": .5, "terms": 1}

    def test_empty_analysis_is_not_metadata_recovery(self):
        metric = dict(self.metric, terms=0)
        products = {"A": product(description=("camera",)), "B": product()}
        case = query_diagnostics(self.group, {"A": 0, "B": 0}, [], products, metric, 1e-12)
        self.assertEqual(case["zero_kind"], "empty_query_analysis")
        self.assertFalse(case["metadata_flags"]["description"]["es_full_query_coverage"])
        self.assertEqual(aggregate([case])["zero_with_any_es_extra_overlap"], 0)

    def test_relevant_and_irrelevant_metadata_can_both_match(self):
        products = {"A": product(description=("camera",), bullet_point=("camera",)),
                    "B": product(description=("camera",))}
        case = query_diagnostics(self.group, {"A": 0, "B": 0}, ["camera"], products, self.metric, 1e-12)
        result = aggregate([case])
        self.assertEqual(case["zero_kind"], "no_title_token_overlap")
        self.assertEqual(result["zero_with_any_es_extra_overlap"], 1)
        self.assertEqual(result["extra_field_flags"]["description"]["zero_with_es_and_no_irrelevant_overlap"], 0)
        self.assertEqual(result["extra_field_flags"]["bullet_point"]["zero_with_es_and_no_irrelevant_overlap"], 1)

    def test_loss_can_still_have_an_exact_product_first(self):
        metric = dict(self.metric, bm25=.8, reference=.9)
        products = {"A": product(title=("camera",)), "B": product(title=("camera",))}
        case = query_diagnostics(self.group, {"A": 2, "B": 1}, ["camera"], products, metric, 1e-12)
        result = aggregate([case])
        self.assertEqual(case["outcome"], "loss")
        self.assertEqual(result["loss_top1_labels"], {"E": 1})

    def test_complement_is_not_silently_treated_as_exact_or_substitute(self):
        group = {"labels": {"A": "C", "B": "I"}}
        products = {"A": product(description=("camera",)), "B": product()}
        case = query_diagnostics(group, {"A": 0, "B": 0}, ["camera"], products, self.metric, 1e-12)
        self.assertFalse(case["has_es_candidate"])
        self.assertFalse(case["metadata_flags"]["description"]["es_overlap"])

    def test_analyzer_drift_and_impossible_positive_match_fail(self):
        products = {"A": product(), "B": product()}
        with self.assertRaisesRegex(ValueError, "analyzer differs"):
            query_diagnostics(self.group, {"A": 0, "B": 0}, [], products, self.metric, 1e-12)
        with self.assertRaisesRegex(ValueError, "no shared"):
            query_diagnostics(self.group, {"A": 1, "B": 0}, ["camera"], products, self.metric, 1e-12)

    def test_zero_scores_cannot_be_classified_as_loss(self):
        products = {"A": product(), "B": product()}
        with self.assertRaisesRegex(ValueError, "must equal"):
            query_diagnostics(self.group, {"A": 0, "B": 0}, ["camera"], products,
                              dict(self.metric, bm25=.4), 1e-12)

    def test_sampling_is_repeatable_and_order_independent(self):
        cases = [{"locale": "us", "query_id": i, "all_zero": i < 10,
                  "outcome": "tie" if i < 10 else "loss", "delta": 0 if i < 10 else -i / 100,
                  "zero_kind": "empty_query_analysis" if i == 0 else "no_title_token_overlap" if i < 10 else None}
                 for i in range(20)]
        policy = {"sample_seed": "fixture", "samples_per_locale_per_cohort": 3}
        expected = sample_cases(cases, policy)
        random.Random(7).shuffle(cases)
        self.assertEqual(expected, sample_cases(cases, policy))
        self.assertIn(0, [case["query_id"] for case in expected])
        self.assertEqual([case["query_id"] for case in expected if case["sample_reason"] == "largest_loss"], [17, 18, 19])


@unittest.skipUnless(JAR.is_file() or os.environ.get("REQUIRE_BM25_JAR") == "1",
                     "Build the Java artifact first with ./mvnw verify")
class DiagnosticBridgeTests(unittest.TestCase):
    def test_exact_baseline_analyzers_preserve_unicode_and_empty_tokens(self):
        self.assertTrue(JAR.is_file())
        with tempfile.TemporaryDirectory() as directory:
            tokens = analyze_texts([("us", "Running\nshoes"), ("es", "no como todos"),
                                   ("jp", "ランニングシューズ")], Path(directory))
        self.assertEqual(tokens[0], ["run", "shoe"])
        self.assertEqual(tokens[1], [])
        self.assertIn("シューズ", tokens[2])
