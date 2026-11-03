import copy
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

import faiss
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'experiments/ann-benchmark'))
from ann_core import choose_candidate
from catalog_embedding_store import CatalogExport
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest
from run_depth_1024 import check_control, search_only, transitions, validate_policy, verify_timing
from worker import build, search


class Depth1024Tests(unittest.TestCase):
    def test_one_new_point_authorized_and_only_search_dispatched(self):
        policy = {'partition': 'validation', 'candidate_efSearch': 1024, 'timing_control_efSearch': 512,
                  'confidence': {'resamples': 2000, 'seed': 20261101}}
        validate_policy(policy)
        for field, value in [('candidate_efSearch', 2048), ('timing_control_efSearch', 256), ('partition', 'test')]:
            with self.assertRaises(ValueError):
                validate_policy(dict(policy, **{field: value}))
        with patch('run_depth_1024.worker') as dispatch:
            search_only(Path('original.json'), Path('index'), 'jp', 1024, Path('output'))
            dispatch.assert_called_once_with('search', Path('original.json'), Path('index'), 'jp', 'hnsw-1024', Path('output'))
            with self.assertRaises(ValueError):
                search_only(Path('original.json'), Path('index'), 'jp', 2048, Path('output'))
        point = {'overall_agreement': .95, 'locale_agreement': dict.fromkeys(('es', 'jp', 'us'), .90),
                 'hybrid_paired_lower': -.005, 'locale_hybrid_delta': dict.fromkeys(('es', 'jp', 'us'), -.005)}
        self.assertEqual(choose_candidate({1024: point})['selected_efSearch'], 1024)
        self.assertIsNone(choose_candidate({1024: dict(point, overall_agreement=.949999)})['selected_efSearch'])

    def test_transition_conservation_allows_non_nested_sets_and_scopes_locales(self):
        exact = [f'{i:03d}' for i in range(100)]
        old = exact[:90] + [f'x{i}' for i in range(10)]
        new = exact[1:96] + [f'y{i}' for i in range(5)]
        keys = [('es', 1), ('jp', 1)]
        reference, control = dict.fromkeys(keys, exact), dict.fromkeys(keys, old)
        candidate = {keys[0]: new, keys[1]: old}
        rows = transitions(control, candidate, reference)
        self.assertEqual((rows[0]['recovered_at_1024'], rows[0]['newly_missing'], rows[0]['still_missing']), (6, 1, 4))
        self.assertEqual(rows[0]['missing_at_1024'], 5)
        self.assertEqual(rows[1]['missing_at_1024'], 10)
        with self.assertRaises(ValueError):
            transitions(control, {keys[0]: new}, reference)
        with self.assertRaises(ValueError):
            transitions({keys[0]: ['same'] * 100}, {keys[0]: new}, {keys[0]: exact})

    def test_raw_timing_requires_original_query_orders_complete_passes_and_valid_values(self):
        policy = {'timed_passes': 3, 'timing_order_seed': 9}
        expected, passes = [1, 2, 3], []
        for number in range(3):
            order = expected.copy()
            random.Random(9 + number).shuffle(order)
            passes.append({'pass': number, 'query_order': order, 'milliseconds': [1., 2., 3.], 'native_calls': [1, 1, 1]})
        self.assertEqual(len(verify_timing(passes, expected, policy)), 9)
        for field, value in [('query_order', [1, 1, 3]), ('milliseconds', [1., float('nan'), 3.]),
                             ('milliseconds', [1., -1., 3.]), ('native_calls', [0, 1, 1])]:
            invalid = copy.deepcopy(passes)
            invalid[0][field] = value
            with self.assertRaises(ValueError):
                verify_timing(invalid, expected, policy)
        with self.assertRaises(ValueError):
            verify_timing(passes[:2], expected, policy)

    def test_control_requires_all_rank_and_metric_artifacts_to_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'a', Path(tmp) / 'b'
            first.mkdir(); second.mkdir()
            for name in ('dense.tsv', 'hybrid.tsv', 'per-query.json'):
                (first / name).write_bytes(b'original')
                (second / name).write_bytes(b'original')
            check_control(first, second)
            (second / 'hybrid.tsv').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                check_control(first, second)

    def test_native_512_1024_workers_reuse_graph_and_keep_contract_and_query_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rng = np.random.default_rng(7)
            vectors = rng.normal(size=(32, 4)).astype('<f4')
            vectors /= np.linalg.norm(vectors, axis=1)[:, None]
            catalog, index_dir, dense_dir = root / 'catalog', root / 'index', root / 'dense'
            catalog_contract = {'catalog': [{'locale': 'es', 'products': 32}], 'dimensions': 4, 'shard_rows': 32}
            with CatalogExport(catalog, catalog_contract) as store:
                rows = [{'locale': 'es', 'product_id': f'{i:03d}', 'title': 'fixture'} for i in range(32)]
                store.consume(rows, lambda chunk: (vectors.tobytes(), {}))
                manifest = {'completion': store.completion(), 'shards': store.entries}
            atomic_json(catalog / 'export-manifest.json', manifest)
            policy = {'build_threads': 1, 'query_threads': 1, 'dimensions': 4, 'M': 8, 'efConstruction': 40,
                      'seed': 2, 'cutoff': 5, 'timing_order_seed': 9, 'warmup_queries': 1, 'timed_passes': 3}
            contract = {'policy': policy, 'catalog_manifest_sha256': file_digest(catalog / 'export-manifest.json')}
            build(index_dir, contract, catalog, 'es')
            graph_hash = file_digest(index_dir / 'hnsw.index')
            dense_dir.mkdir()
            atomic_json(dense_dir / 'queries.json', [{'locale': 'es', 'query_id': i, 'query': 'q'} for i in (1, 2)])
            vectors[:2].tofile(dense_dir / 'query-vectors.f32')
            for depth in (512, 1024):
                output = root / str(depth)
                search(index_dir, contract, 'es', f'hnsw-{depth}', output, dense_dir)
                passes = json.loads((output / 'latencies.json').read_text())
                self.assertEqual(len(verify_timing(passes, [1, 2], policy)), 6)
                self.assertEqual(graph_hash, file_digest(index_dir / 'hnsw.index'))
            self.assertEqual(file_digest(root / '512/results.tsv'), file_digest(root / '1024/results.tsv'))


if __name__ == '__main__':
    unittest.main()
