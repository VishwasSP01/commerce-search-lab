import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dense_metrics import evaluate_dense, paired_comparison, summarize_dense


class DenseMetricTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'confidence': {'resamples': 100, 'seed': 7}}
        self.groups = {('es', 1): {'query': 'zapatos', 'labels': {'E1': 'E', 'E2': 'E', 'S': 'S'}},
                       ('jp', 1): {'query': '靴', 'labels': {'S': 'S'}}}
        self.header = 'product_locale\tquery_id\trank\tproduct_id\tscore\n'
        self.valid = 'es\t1\t1\tunknown\t0.8\nes\t1\t2\tE1\t-0.1\njp\t1\t1\tS\t0.4\njp\t1\t2\tunknown\t0.1\n'

    def evaluate(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hits.tsv'
            path.write_text(self.header + text)
            return evaluate_dense(self.groups, path, cutoff=2, coverage_cutoff=2)

    def test_unknowns_retain_ranks_and_no_exact_is_excluded_only_from_recall(self):
        rows = self.evaluate(self.valid)
        self.assertEqual(rows[0]['known_exact_recall_at_100'], .5)
        self.assertIsNone(rows[1]['known_exact_recall_at_100'])
        self.assertEqual([r['judgment_coverage_at_10'] for r in rows], [.5, .5])
        summary = summarize_dense(rows, self.policy)
        self.assertEqual(summary['known_exact_recall_at_100']['defined_queries'], 1)
        self.assertEqual(summary['queries_without_known_exact'], 1)
        self.assertNotIn('zero_analyzed_term_queries', summary)

    def test_missing_duplicate_invalid_scores_ranks_and_ties_fail(self):
        for text in [self.valid.replace('jp\t1\t2\tunknown\t0.1\n', ''),
                     self.valid.replace('E1\t-0.1', 'unknown\t-0.1'),
                     self.valid.replace('-0.1', 'nan'), self.valid.replace('-0.1', '1.2'),
                     self.valid.replace('es\t1\t2', 'es\t1\t3'),
                     self.valid.replace('E1\t-0.1', 'E1\t0.8'),
                     self.valid.replace('jp\t1', 'us\t1')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.evaluate(text)

    def test_paired_interval_matches_constant_delta_and_rejects_misalignment(self):
        rows = self.evaluate(self.valid)
        reference = copy.deepcopy(rows)
        reference[0]['known_exact_recall_at_100'] = .25
        result = paired_comparison(rows, reference, self.policy)
        self.assertEqual(result['known_exact_recall_at_100']['paired_delta_ci95'], [.25, .25])
        self.assertEqual(result['judgment_coverage_at_10']['paired_delta_ci95'], [0., 0.])
        for field, value in [('query_id', 2), ('group', 'wrong'), ('known_exact_products', 3),
                             ('known_exact_recall_at_100', None)]:
            invalid = copy.deepcopy(reference)
            invalid[0][field] = value
            with self.assertRaises(ValueError):
                paired_comparison(rows, invalid, self.policy)
        with self.assertRaises(ValueError):
            paired_comparison(rows, reference + reference[:1], self.policy)
        with self.assertRaises(ValueError):
            paired_comparison(rows, reference[:1], self.policy)


if __name__ == '__main__':
    unittest.main()
