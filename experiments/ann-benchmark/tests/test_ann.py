import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import faiss
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'experiments/ann-benchmark'))
from ann_core import agreement_rows, choose_candidate, locale_chunks, ordered_search
from benchmark import latency_summary
from catalog_embedding_store import CatalogExport
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest
from worker import build, search, verify_index


class AnnTests(unittest.TestCase):
    def setUp(self):
        faiss.omp_set_num_threads(1)

    def test_exact_expands_all_cutoff_ties_and_handles_negative_scores(self):
        index = faiss.IndexFlatIP(2)
        index.add(np.tile(np.array([[1., 0.]], dtype=np.float32), (301, 1)))
        ids, scores, calls = ordered_search(index, np.array([[1., 0.]], dtype=np.float32), exact=True)
        np.testing.assert_array_equal(ids, np.arange(100))
        np.testing.assert_array_equal(scores, np.ones(100))
        self.assertGreater(calls, 1)
        negative = faiss.IndexFlatIP(2)
        negative.add(np.array([[-1., 0.], [0., -1.]], dtype=np.float32))
        ids, scores, _ = ordered_search(negative, np.array([[1., 0.]], dtype=np.float32), exact=True, cutoff=2)
        np.testing.assert_array_equal(ids, [1, 0])
        np.testing.assert_array_equal(scores, [0., -1.])

    def test_invalid_native_neighbors_are_rejected(self):
        class BadIndex:
            ntotal, d = 100, 2
            def search(self, query, k):
                return np.ones((1, k), dtype=np.float32), np.full((1, k), -1, dtype=np.int64)
        with self.assertRaises(ValueError):
            ordered_search(BadIndex(), np.array([[1., 0.]], dtype=np.float32))

    def test_set_agreement_is_not_relevance_and_requires_matching_unique_queries(self):
        ids = [str(i) for i in range(100)]
        actual = {('es', 1): ids, ('jp', 1): ids}
        reference = {('es', 1): ids, ('jp', 1): ids[1:] + ['other']}
        self.assertEqual([r['agreement_at_100'] for r in agreement_rows(actual, reference)], [1., .99])
        with self.assertRaises(ValueError):
            agreement_rows(actual, {('es', 1): ids})
        with self.assertRaises(ValueError):
            agreement_rows({('es', 1): ['a'] * 100}, {('es', 1): ids})

    def test_selection_requires_both_agreement_and_hybrid_budgets_and_chooses_lowest(self):
        point = {'overall_agreement': .96, 'locale_agreement': {'es': .92, 'jp': .98, 'us': .97},
                 'hybrid_paired_lower': -.004, 'locale_hybrid_delta': {'es': -.003, 'jp': 0., 'us': .001}}
        self.assertEqual(choose_candidate({512: point, 256: point})['selected_efSearch'], 256)
        for field, bad in [('overall_agreement', .94), ('locale_agreement', {'es': .99}),
                           ('hybrid_paired_lower', -.006), ('locale_hybrid_delta', {'es': -.006})]:
            invalid = dict(point, **{field: bad})
            self.assertIsNone(choose_candidate({128: invalid})['selected_efSearch'])

    def fixture(self, root):
        rng = np.random.default_rng(5)
        matrix = rng.normal(size=(40, 4)).astype('<f4')
        matrix /= np.linalg.norm(matrix, axis=1)[:, None]
        catalog = root / 'catalog'
        contract = {'catalog': [{'locale': 'es', 'products': 20}, {'locale': 'jp', 'products': 20}],
                    'dimensions': 4, 'shard_rows': 10}
        with CatalogExport(catalog, contract) as store:
            for start in range(0, 40, 10):
                locale = 'es' if start < 20 else 'jp'
                rows = [{'locale': locale, 'product_id': f'{i%20:03d}', 'title': 'title'} for i in range(start, start + 10)]
                store.consume(rows, lambda rows, start=start: (matrix[start:start + 10].tobytes(), {}))
            manifest = {'completion': store.completion(), 'shards': store.entries}
        atomic_json(catalog / 'export-manifest.json', manifest)
        return catalog, manifest, matrix

    def test_shards_reject_corrupt_or_misrouted_id_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog, manifest, _ = self.fixture(Path(tmp))
            chunks = list(locale_chunks(catalog, manifest, 'jp', 4))
            self.assertEqual(sum(len(ids) for ids, _ in chunks), 20)
            self.assertEqual(chunks[0][0][0], '000')
            path = catalog / 'shards/00002/ids.jsonl'
            path.write_bytes(path.read_bytes().replace(b'jp', b'es'))
            with self.assertRaises(ValueError):
                list(locale_chunks(catalog, manifest, 'jp', 4))

    def test_native_build_reload_worker_passes_preserve_locale_id_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, manifest, vectors = self.fixture(root)
            policy = {'build_threads': 1, 'query_threads': 1, 'dimensions': 4, 'M': 8,
                      'efConstruction': 40, 'seed': 8, 'cutoff': 5, 'timing_order_seed': 12,
                      'warmup_queries': 1, 'timed_passes': 3}
            contract = {'policy': policy, 'catalog_manifest_sha256': file_digest(catalog / 'export-manifest.json')}
            directory = root / 'indices/jp'
            build(directory, contract, catalog, 'jp')
            original = file_digest(directory / 'hnsw.index')
            build(directory, contract, catalog, 'jp')
            self.assertEqual(original, file_digest(directory / 'hnsw.index'))
            dense = root / 'dense'
            dense.mkdir()
            atomic_json(dense / 'queries.json', [{'locale': 'jp', 'query_id': 1, 'query': '靴'}])
            vectors[20:21].tofile(dense / 'query-vectors.f32')
            for method in ('flat', 'hnsw-128'):
                search(directory, contract, 'jp', method, root / method, dense)
                session = json.loads((root / method / 'session.json').read_text())
                self.assertTrue(session['identical_rankings_all_passes'])
                self.assertEqual(session['threads'], 1)
                lines = (root / method / 'results.tsv').read_text().splitlines()
                self.assertEqual(lines[1].split('\t')[:4], ['jp', '1', '1', '000'])
            path = directory / 'hnsw.index'
            with path.open('ab') as handle:
                handle.write(b'x')
            with self.assertRaises(ValueError):
                verify_index(directory, contract, 'jp')

    def test_latency_percentiles_have_explicit_units_and_sample_count(self):
        result = latency_summary([1., 2., 3., 4., 5.])
        self.assertEqual(result['p50_ms'], 3.)
        self.assertEqual(result['samples'], 5)
        self.assertAlmostEqual(result['sum_seconds'], .015)


if __name__ == '__main__':
    unittest.main()
