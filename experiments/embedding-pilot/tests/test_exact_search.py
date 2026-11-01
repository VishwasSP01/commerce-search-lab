import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'experiments/embedding-pilot'))
from catalog_embedding_store import CatalogExport
from embedding_checkpoint import fingerprint
from exact_search import ExactTopK, catalog_shards, top_indices


class ExactSearchTests(unittest.TestCase):
    def test_blockwise_matches_exhaustive_order_with_negative_scores_and_cutoff_ties(self):
        torch.set_num_threads(1)
        queries = np.array([[1, 0], [-1, 0], [0, 1]], dtype='<f4')
        documents = np.array([[1, 0], [1, 0], [1, 0], [0, 1], [0, -1], [-1, 0]], dtype='<f4')
        ids = ['001', '002', '003', '004', '005', '006']
        oracle = torch.mm(torch.from_numpy(queries), torch.from_numpy(documents).T).numpy()
        for batch_size in (1, 3):
            engine = ExactTopK(queries, cutoff=2, query_batch_size=batch_size)
            for start in range(0, len(ids), 2):
                engine.consume(documents[start:start + 2], ids[start:start + 2])
            expected = [[(ids[i], float(row[i])) for i in sorted(range(len(ids)), key=lambda i: (-row[i], ids[i]))[:2]]
                        for row in oracle]
            self.assertEqual(engine.results(), expected)
            self.assertEqual(engine.products, 6)
        # All products tie at the cutoff: partition must not arbitrarily retain later IDs.
        np.testing.assert_array_equal(top_indices(np.ones(200, dtype=np.float32), np.arange(200)[::-1], 100),
                                      np.arange(199, 99, -1))
        negative = ExactTopK(np.array([[1., 0.]], dtype='<f4'), cutoff=2)
        negative.consume(np.array([[-1., 0.]], dtype='<f4'), ['000'])
        self.assertEqual(negative.results(), [[('000', -1.0)]])

    def test_random_blockwise_retrieval_matches_independent_float64_oracle(self):
        rng = np.random.default_rng(45)
        queries, documents = rng.normal(size=(7, 12)), rng.normal(size=(113, 12))
        queries = (queries / np.linalg.norm(queries, axis=1)[:, None]).astype('<f4')
        documents = (documents / np.linalg.norm(documents, axis=1)[:, None]).astype('<f4')
        ids = [f'{i:04d}' for i in range(len(documents))]
        engine = ExactTopK(queries, cutoff=10, query_batch_size=3)
        for start in range(0, len(ids), 17):
            engine.consume(documents[start:start + 17], ids[start:start + 17])
        oracle = queries.astype(np.float64) @ documents.astype(np.float64).T
        for hits, row in zip(engine.results(), oracle, strict=True):
            expected = sorted(range(len(ids)), key=lambda i: (-row[i], ids[i]))[:10]
            self.assertEqual([p for p, _ in hits], [ids[i] for i in expected])
            np.testing.assert_allclose([s for _, s in hits], row[expected], atol=2e-7, rtol=0)

    def test_invalid_vectors_and_nonincreasing_ids_fail(self):
        queries = np.array([[1., 0.]], dtype='<f4')
        engine = ExactTopK(queries)
        for documents, ids in [(queries, ['']), (queries * 2, ['a']), (queries.astype('float64'), ['a']),
                               (np.array([[np.nan, 0]], dtype='<f4'), ['a'])]:
            with self.assertRaises(ValueError):
                engine.consume(documents, ids)
        engine.consume(queries, ['a'])
        with self.assertRaises(ValueError):
            engine.consume(queries, ['a'])

    def test_verified_shards_preserve_locale_ids_and_reject_corruption(self):
        contract = {'catalog': [{'locale': 'es', 'products': 2}, {'locale': 'jp', 'products': 1}],
                    'shard_rows': 2, 'dimensions': 2}
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with CatalogExport(directory, contract) as store:
                for locale, ids in [('es', ['001', '002']), ('jp', ['001'])]:
                    rows = [{'locale': locale, 'product_id': p, 'title': p} for p in ids]
                    store.consume(rows, lambda chunk: (np.tile(np.array([[1., 0.]], dtype='<f4'), (len(chunk), 1)).tobytes(), {}))
                manifest = {'contract_fingerprint': fingerprint(contract), 'shards': store.entries, 'completion': store.completion()}
            chunks = list(catalog_shards(directory, contract, manifest))
            self.assertEqual([(locale, ids) for locale, ids, _ in chunks], [('es', ['001', '002']), ('jp', ['001'])])
            bad_manifest = json.loads(json.dumps(manifest))
            bad_manifest['completion']['counts']['es'] = 3
            with self.assertRaises(ValueError):
                list(catalog_shards(directory, contract, bad_manifest))
            for name in ('ids.jsonl', 'shard-00000.f32', 'checkpoint.json'):
                path = directory / 'shards/00000' / name
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.assertRaises(ValueError):
                    list(catalog_shards(directory, contract, manifest))
                path.write_bytes(original)
            # A consistently hashed but misrouted ID map must still fail locale validation.
            path = directory / 'shards/00000/ids.jsonl'
            path.write_bytes(path.read_bytes().replace(b'"es"', b'"jp"'))
            entry = manifest['shards'][0]
            entry['metrics']['ids_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            checkpoint = directory / 'shards/00000/checkpoint.json'
            child = json.loads(checkpoint.read_text())
            child['shards'][0]['metrics'] = entry['metrics']
            checkpoint.write_text(json.dumps(child))
            entry['checkpoint_sha256'] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'locale'):
                list(catalog_shards(directory, contract, manifest))


if __name__ == '__main__':
    unittest.main()
