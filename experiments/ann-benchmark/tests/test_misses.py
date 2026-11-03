import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import faiss
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'experiments/ann-benchmark'))
from analyze_misses import metadata
from miss_diagnostics import CATEGORIES, aggregate, diagnose, representative_pairs, select_cases
from prepare_esci_splits import file_digest


def vector(score, sign=1):
    return np.array([score, sign * math.sqrt(1 - score * score), 0.], dtype='<f4')

QUERY = np.array([1., 0., 0.], dtype='<f4')


class MissTests(unittest.TestCase):
    def test_identical_embeddings_are_credited_without_erasing_relevance_loss(self):
        vectors = {'001': vector(.8), '002': vector(.8)}
        row = diagnose(['001'], ['002'], vectors, QUERY, {'001': 'E'}, cutoff=1)
        self.assertEqual(row['category_counts']['identical_vector'], 1)
        self.assertEqual(row['strict_agreement'], 0.)
        self.assertEqual(row['equivalent_credit_agreement'], 1.)
        self.assertEqual(row['lost_known_exact'], 1)
        self.assertIsNone(row['pairs'][0]['replacement_label'])

    def test_equal_scores_with_different_vectors_are_separate_from_duplicates(self):
        vectors = {'a': vector(.5), 'b': vector(.5, -1)}
        row = diagnose(['a'], ['b'], vectors, QUERY, {'a': 'E', 'b': 'I'}, cutoff=1)
        self.assertEqual(row['category_counts']['score_equivalent'], 1)
        self.assertEqual(row['category_counts']['identical_vector'], 0)
        self.assertEqual(row['lost_known_exact_by_category']['score_equivalent'], 1)

    def test_ties_are_matched_before_leftover_rank_pairing(self):
        vectors = {'a': vector(.9), 'b': vector(.8), 'c': vector(.8, -1), 'd': vector(.7)}
        row = diagnose(['a', 'b'], ['c', 'd'], vectors, QUERY, {}, cutoff=2)
        self.assertEqual(row['category_counts']['score_equivalent'], 1)
        self.assertEqual(row['category_counts']['strict_score_loss'], 1)
        self.assertEqual(row['equivalent_credit_agreement'], .5)
        by_missing = {p['missing_product_id']: p for p in row['pairs']}
        self.assertEqual(by_missing['b']['replacement_product_id'], 'c')
        self.assertEqual(by_missing['a']['replacement_product_id'], 'd')

    def test_common_score_reversal_is_reported_not_called_a_strict_loss(self):
        row = diagnose(['a'], ['b'], {'a': vector(.4), 'b': vector(.6)}, QUERY, {}, cutoff=1)
        self.assertEqual(row['category_counts']['float64_order_reversal'], 1)
        self.assertEqual(row['category_counts']['strict_score_loss'], 0)
        self.assertLess(row['pairs'][0]['score_gap64'], 0)

    def test_invalid_rankings_vectors_labels_and_tolerance_fail(self):
        vectors = {'a': vector(.9), 'b': vector(.8)}
        for exact, approximate, values, query, labels, tolerance in [
            (['a', 'a'], ['a', 'b'], vectors, QUERY, {}, 1e-10),
            (['a'], ['b'], {'a': vectors['a']}, QUERY, {}, 1e-10),
            (['a'], ['b'], dict(vectors, b=np.zeros(3, dtype='<f4')), QUERY, {}, 1e-10),
            (['a'], ['b'], vectors, QUERY.astype(np.float64), {}, 1e-10),
            (['a'], ['b'], vectors, QUERY, {'a': 'bad'}, 1e-10),
            (['a'], ['b'], vectors, QUERY, {}, 0.)]:
            with self.subTest(exact=exact, tolerance=tolerance), self.assertRaises(ValueError):
                diagnose(exact, approximate, values, query, labels, tolerance, cutoff=len(exact))

    def test_native_reconstruction_scoring_pair_conservation_and_determinism(self):
        rng = np.random.default_rng(3)
        matrix = rng.normal(size=(20, 3)).astype('<f4')
        matrix /= np.linalg.norm(matrix, axis=1)[:, None]
        matrix[10:13] = matrix[:3]
        index = faiss.IndexFlatIP(3)
        index.add(matrix)
        recovered = index.reconstruct_batch(np.arange(20, dtype=np.int64))
        np.testing.assert_array_equal(recovered, matrix)
        ids = [f'{i:03d}' for i in range(20)]
        vectors = dict(zip(ids, recovered, strict=True))
        row = diagnose(ids[:10], ids[10:], vectors, QUERY, {ids[0]: 'E'}, cutoff=10)
        repeat = diagnose(list(reversed(ids[:10])), list(reversed(ids[10:])), dict(reversed(list(vectors.items()))), QUERY, {ids[0]: 'E'}, cutoff=10)
        self.assertEqual(row, repeat)
        self.assertEqual(sum(row['category_counts'].values()), 10)
        self.assertEqual(len({p['missing_product_id'] for p in row['pairs']}), 10)
        self.assertEqual(len({p['replacement_product_id'] for p in row['pairs']}), 10)
        for pair in row['pairs']:
            expected = math.fsum(float(x) * float(y) for x, y in zip(vectors[pair['missing_product_id']], QUERY, strict=True))
            self.assertAlmostEqual(pair['missing_score64'], expected, places=14)

    def test_case_rules_and_aggregate_keep_locales_and_ranking_effects_explicit(self):
        base = diagnose(['a'], ['b'], {'a': vector(.9), 'b': vector(.8)}, QUERY, {}, cutoff=1)
        rows = [dict(copy.deepcopy(base), locale=locale, query_id=q, hybrid_recall_delta=delta,
                     hybrid_lost_known_exact=int(delta < 0), hybrid_new_known_exact=0,
                     still_missing_at_shallower_depth={'128': 1, '256': 1})
                for locale, q, delta in [('es', 2, -.1), ('es', 1, 0.), ('jp', 1, -.2)]]
        cases = select_cases(rows)
        self.assertEqual([(r['locale'], r['query_id']) for r in cases], [('es', 1), ('es', 2), ('jp', 1)])
        self.assertEqual(cases[-1]['reasons'], ['strict_score_loss', 'hybrid_recall_loss'])
        totals = aggregate(rows)
        self.assertEqual(totals['hybrid_regressions_with_no_dense_known_exact_loss'], 2)
        self.assertEqual(totals['category_counts']['strict_score_loss'], 3)
        pairs = representative_pairs(rows[0], ['strict_score_loss'])
        self.assertLessEqual(len(pairs), 2)

    def test_metadata_case_join_preserves_locale_and_string_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'products.parquet'
            with duckdb.connect() as db:
                db.execute('CREATE TABLE products(product_locale VARCHAR, product_id VARCHAR, product_title VARCHAR)')
                db.execute("INSERT INTO products VALUES ('es','001','zapato'),('es','002','rojo'),('jp','001','靴'),('jp','002','赤')")
                db.sql('SELECT * FROM products').write_parquet(str(path))
            cases = [{'locale': loc, 'pairs': [{'missing_product_id': '001', 'replacement_product_id': '002'}]} for loc in ('es', 'jp')]
            metadata(cases, path, {'name': path.name, 'bytes': path.stat().st_size, 'sha256': file_digest(path)})
            self.assertEqual([c['pairs'][0]['missing_title'] for c in cases], ['zapato', '靴'])


if __name__ == '__main__':
    unittest.main()
