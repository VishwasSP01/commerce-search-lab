"""Bounded exhaustive float32 retrieval, with deterministic cutoff ties."""

import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from embedding_checkpoint import fingerprint


def check_vectors(vectors, dimensions):
    if (vectors.ndim != 2 or vectors.shape[1] != dimensions or not len(vectors)
            or vectors.dtype != np.dtype('<f4') or not np.isfinite(vectors).all()
            or np.max(np.abs(np.linalg.norm(vectors, axis=1) - 1)) > 1e-5):
        raise ValueError('Invalid shape, dtype, finite values or unit norms')


def top_indices(scores, identities, k):
    """Select by score, then ID-order ordinal, including every cutoff tie."""
    if k < 1 or len(scores) != len(identities) or not np.isfinite(scores).all():
        raise ValueError('Invalid top-k input')
    if len(scores) > k:
        threshold = np.partition(scores, len(scores) - k)[len(scores) - k]
        eligible = np.flatnonzero(scores >= threshold)
    else:
        eligible = np.arange(len(scores))
    return eligible[np.lexsort((identities[eligible], -scores[eligible]))[:k]]


class ExactTopK:
    def __init__(self, queries, cutoff=100, query_batch_size=128):
        if cutoff < 1 or query_batch_size < 1:
            raise ValueError('Invalid search limits')
        check_vectors(queries, queries.shape[1])
        self.queries = torch.from_numpy(queries)
        self.cutoff, self.query_batch_size = cutoff, query_batch_size
        self.scores = [np.empty(0, dtype=np.float32) for _ in queries]
        self.orders = [np.empty(0, dtype=np.int64) for _ in queries]
        self.ids = [np.empty(0, dtype=object) for _ in queries]
        self.products, self.last_id = 0, None

    def consume(self, vectors, ids):
        check_vectors(vectors, self.queries.shape[1])
        if (len(ids) != len(vectors) or any(not isinstance(x, str) or not x for x in ids)
                or any(a >= b for a, b in zip(ids, ids[1:]))
                or self.last_id is not None and ids[0] <= self.last_id):
            raise ValueError('IDs must match vector rows and strictly increase within locale')
        ordinal = np.arange(self.products, self.products + len(ids), dtype=np.int64)
        ids = np.array(ids, dtype=object)
        documents = torch.from_numpy(vectors).T
        with torch.inference_mode():
            for start in range(0, len(self.queries), self.query_batch_size):
                matrix = torch.mm(self.queries[start:start + self.query_batch_size], documents).numpy()
                for offset, row in enumerate(matrix):
                    q = start + offset
                    local = top_indices(row, ordinal, self.cutoff)
                    scores = np.concatenate((self.scores[q], row[local]))
                    orders = np.concatenate((self.orders[q], ordinal[local]))
                    products = np.concatenate((self.ids[q], ids[local]))
                    keep = top_indices(scores, orders, self.cutoff)
                    self.scores[q], self.orders[q], self.ids[q] = scores[keep], orders[keep], products[keep]
        self.products += len(ids)
        self.last_id = ids[-1]

    def results(self):
        if not self.products:
            raise ValueError('Cannot complete a search without products')
        return [list(zip(ids.tolist(), scores.tolist(), strict=True))
                for ids, scores in zip(self.ids, self.scores, strict=True)]


def catalog_shards(directory: Path, contract: dict, manifest: dict):
    """Read-only verification against the trusted completed export manifest."""
    root_fingerprint = fingerprint(contract)
    if manifest['contract_fingerprint'] != root_fingerprint:
        raise ValueError('Catalog contract fingerprint mismatch')
    expected = {r['locale']: r['products'] for r in contract['catalog']}
    counts = dict.fromkeys(expected, 0)
    vector_hash, id_hash = hashlib.sha256(), hashlib.sha256()
    last_key, previous_size = None, 0
    vector_bytes = id_bytes = 0
    entries = manifest['shards']
    directories = {p.name for p in (directory / 'shards').iterdir() if p.is_dir()}
    if directories != {f'{i:05d}' for i in range(len(entries))}:
        raise ValueError('Missing or unexpected shard directory')
    for index, entry in enumerate(entries):
        locale = entry['locale']
        if (entry['index'] != index or locale not in counts
                or not 0 < entry['rows'] <= contract['shard_rows']):
            raise ValueError('Invalid shard index, locale or size')
        shard = directory / 'shards' / f'{index:05d}'
        checkpoint_bytes = (shard / 'checkpoint.json').read_bytes()
        if hashlib.sha256(checkpoint_bytes).hexdigest() != entry['checkpoint_sha256']:
            raise ValueError('Checkpoint checksum mismatch')
        checkpoint = json.loads(checkpoint_bytes)
        child = {'export_fingerprint': root_fingerprint, 'shard_index': index,
                 'locale': locale, 'dimensions': contract['dimensions']}
        expected_entry = {'index': 0, 'rows': entry['rows'], 'input_sha256': entry['input_sha256'],
                          'sha256': entry['vector_sha256'], 'bytes': entry['vector_bytes'], 'metrics': entry['metrics']}
        if (checkpoint['contract'] != child or checkpoint['fingerprint'] != fingerprint(child)
                or checkpoint['shards'] != [expected_entry]):
            raise ValueError('Checkpoint/export identity mismatch')
        payload, id_payload = (shard / 'shard-00000.f32').read_bytes(), (shard / 'ids.jsonl').read_bytes()
        if (len(payload) != entry['vector_bytes'] or hashlib.sha256(payload).hexdigest() != entry['vector_sha256']
                or len(id_payload) != entry['metrics']['ids_bytes']
                or hashlib.sha256(id_payload).hexdigest() != entry['metrics']['ids_sha256']):
            raise ValueError('Vector or ID map checksum mismatch')
        rows = [json.loads(line) for line in id_payload.decode().splitlines()]
        if len(rows) != entry['rows'] or len(payload) != len(rows) * contract['dimensions'] * 4:
            raise ValueError('Vector/ID byte count mismatch')
        if last_key and last_key[0] == locale and previous_size != contract['shard_rows']:
            raise ValueError('Nonfinal short shard')
        for row in rows:
            if set(row) != {'locale', 'product_id'} or row['locale'] != locale or not isinstance(row['product_id'], str) or not row['product_id']:
                raise ValueError('ID map locale/schema mismatch')
            key = (locale, row['product_id'])
            if last_key is not None and key <= last_key:
                raise ValueError('Catalog key order mismatch')
            last_key = key
        previous_size = len(rows)
        vectors = np.frombuffer(payload, dtype='<f4').reshape(len(rows), contract['dimensions']).copy()
        check_vectors(vectors, contract['dimensions'])
        counts[locale] += len(rows)
        vector_hash.update(payload)
        id_hash.update(id_payload)
        vector_bytes += len(payload)
        id_bytes += len(id_payload)
        yield locale, [row['product_id'] for row in rows], vectors
    completion = {'counts': counts, 'shards': len(entries), 'vector_bytes': vector_bytes, 'ids_bytes': id_bytes,
                  'vector_sequence_sha256': vector_hash.hexdigest(), 'id_sequence_sha256': id_hash.hexdigest()}
    if counts != expected or completion != manifest['completion']:
        raise ValueError('Incomplete catalog or sequence digest mismatch')
