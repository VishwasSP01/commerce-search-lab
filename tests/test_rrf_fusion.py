import copy
from fractions import Fraction
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dense_metrics import evaluate_dense
from rrf_fusion import compare_rrf, nomination, read_rankings, recovery_rows, rrf_rank, summarize_recovery
from run_rrf import frozen_cohort


class RrfTests(unittest.TestCase):
    def test_formula_combines_overlap_and_gives_absent_products_zero_contribution(self):
        result = rrf_rank(['A', 'B'], ['B', 'C'], window=2, cutoff=2)
        self.assertEqual(result, [('B', float(Fraction(1, 61) + Fraction(1, 62))), ('A', 1 / 61)])
        self.assertEqual(rrf_rank([], ['001', '002'], window=2, cutoff=2), [('001', 1 / 61), ('002', 1 / 62)])
        self.assertEqual(rrf_rank([], []), [])

    def test_exact_ties_at_cutoff_use_id_and_swapping_sources_does_not_change_order(self):
        a, b = ['002', '001'], ['001', '002']
        self.assertEqual(rrf_rank(a, b, window=2, cutoff=1)[0][0], '001')
        self.assertEqual(rrf_rank(a, b), rrf_rank(b, a))
        # A product cannot gain two contributions by appearing twice in one list.
        for left, right in [(['A', 'A'], []), ([''], []), (['A', 'B', 'C'], [])]:
            with self.assertRaises(ValueError):
                rrf_rank(left, right, window=2, cutoff=2)
        with self.assertRaises(ValueError):
            rrf_rank(['A'], [], constant=0)

    def read(self, rows, keys=(('es', 1), ('jp', 1))):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'results.tsv'
            path.write_text('product_locale\tquery_id\trank\tproduct_id\tscore\n' + rows)
            return read_rankings(path, keys, window=2)

    def test_raw_score_scales_do_not_affect_fusion_and_empty_lists_remain_explicit(self):
        a = self.read('es\t1\t1\t001\t1000\nes\t1\t2\t002\t10\n')
        b = self.read('es\t1\t1\t001\t0.8\nes\t1\t2\t002\t-0.4\n')
        self.assertEqual(a, b)
        self.assertEqual(a[('jp', 1)], [])
        self.assertEqual(rrf_rank(a[('es', 1)], ['002']), rrf_rank(b[('es', 1)], ['002']))

    def test_source_protocol_rejects_wrong_query_ranks_duplicates_and_score_order(self):
        for text in ['us\t1\t1\tA\t1\n', 'es\t1\t2\tA\t1\n', 'es\t1\t1\tA\tnan\n',
                     'es\t1\t1\tA\t1\nes\t1\t2\tA\t0.5\n',
                     'es\t1\t1\tB\t1\nes\t1\t2\tA\t1\n',
                     'es\t1\t1\tA\t0.5\nes\t1\t2\tB\t1\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text)

    def test_recovery_distinguishes_new_and_lost_products_from_net_change(self):
        groups = {('es', 1): {'labels': {'A': 'E', 'B': 'E', 'C': 'E'}},
                  ('jp', 1): {'labels': {'A': 'E', 'S': 'S'}}}
        rankings = {'bm25': {('es', 1): ['A', 'B'], ('jp', 1): ['S']},
                    'dense': {('es', 1): ['B', 'C'], ('jp', 1): ['A']},
                    'rrf': {('es', 1): ['B', 'C'], ('jp', 1): ['A']}}
        rows = recovery_rows(groups, rankings, {('es', 1)})
        overall = summarize_recovery(rows)
        cohort = summarize_recovery([r for r in rows if r['vocabulary_gap_cohort']])
        self.assertEqual(cohort['new_known_exact_vs_bm25'], 1)
        self.assertEqual(cohort['lost_known_exact_vs_bm25'], 1)
        self.assertEqual(cohort['net_recall_tied_queries'], 1)
        self.assertEqual(overall['bm25_zero_to_rrf_positive_queries'], 1)
        self.assertEqual(overall['candidate_union_known_exact'], 4)
        self.assertEqual(overall['available_known_exact_dropped_by_rrf'], 1)
        bad = copy.deepcopy(rankings)
        bad['rrf'][('es', 1)] = ['unknown']
        with self.assertRaises(ValueError):
            recovery_rows(groups, bad, set())

    def test_locale_isolation_roundtrip_preserves_unknowns_and_same_product_ids(self):
        groups = {('es', 1): {'query': 'zapato', 'labels': {'same': 'E'}},
                  ('jp', 1): {'query': '靴', 'labels': {'same': 'I', 'other': 'E'}}}
        bm25 = self.read('es\t1\t1\tsame\t1\njp\t1\t1\tother\t1\n')
        dense = self.read('es\t1\t1\tunknown\t0.8\nes\t1\t2\tsame\t0.5\njp\t1\t1\tsame\t0.8\njp\t1\t2\tother\t0.5\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rrf.tsv'
            lines = ['product_locale\tquery_id\trank\tproduct_id\tscore\n']
            for (loc, query), hits in ((key, rrf_rank(bm25[key], dense[key], window=2, cutoff=2)) for key in groups):
                lines += [f'{loc}\t{query}\t{rank}\t{p}\t{s}\n' for rank, (p, s) in enumerate(hits, 1)]
            path.write_text(''.join(lines))
            rows = evaluate_dense(groups, path, cutoff=2, coverage_cutoff=2)
        self.assertEqual([r['known_exact_recall_at_100'] for r in rows], [1., 1.])
        self.assertEqual([r['judgment_coverage_at_10'] for r in rows], [.5, 1.])

    def test_frozen_cohort_uses_original_kind_and_rejects_missing_duplicate_or_drift(self):
        cases = [{'locale': 'es', 'query_id': 1, 'all_zero': True, 'zero_kind': 'no_title_token_overlap'},
                 {'locale': 'es', 'query_id': 2, 'all_zero': True, 'zero_kind': 'empty_query_analysis'}]
        keys = {('es', 1), ('es', 2)}
        self.assertEqual(frozen_cohort(cases, keys, expected=1), {('es', 1)})
        for bad in (cases[:1], cases + cases[:1], [dict(cases[0], all_zero=False), cases[1]]):
            with self.assertRaises(ValueError):
                frozen_cohort(bad, keys, expected=1)

    def test_comparison_names_reference_correctly_and_requires_identical_queries(self):
        row = {'locale': 'es', 'query_id': 1, 'group': 'g', 'known_exact_products': 4,
               'known_exact_recall_at_100': .75, 'judgment_coverage_at_10': .5}
        reference = dict(row, known_exact_recall_at_100=.25)
        result = compare_rrf([row], [reference], {'confidence': {'seed': 4, 'resamples': 100}})
        self.assertEqual(result['known_exact_recall_at_100']['rrf_minus_reference'], .5)
        self.assertEqual(result['known_exact_recall_at_100']['paired_delta_ci95'], [.5, .5])
        self.assertNotIn('dense_minus_bm25', result['known_exact_recall_at_100'])
        with self.assertRaises(ValueError):
            compare_rrf([row], [dict(reference, group='wrong')], {'confidence': {'seed': 4, 'resamples': 100}})

    def test_nomination_rejects_locale_regression_or_uncertain_gain(self):
        metric = 'known_exact_recall_at_100'
        comparisons = {m: {metric: {'paired_delta_ci95': [.01, .03]}} for m in ('bm25', 'dense')}
        baseline = {'es': {metric: {'mean': .5}}}
        self.assertTrue(nomination(comparisons, baseline, baseline)['nominate_rrf_as_offline_reference'])
        self.assertFalse(nomination(comparisons, {'es': {metric: {'mean': .49}}}, baseline)['nominate_rrf_as_offline_reference'])
        comparisons['dense'][metric]['paired_delta_ci95'][0] = 0
        self.assertFalse(nomination(comparisons, baseline, baseline)['nominate_rrf_as_offline_reference'])
        with self.assertRaises(ValueError):
            nomination({'bm25': comparisons['bm25']}, baseline, baseline)


if __name__ == '__main__':
    unittest.main()
