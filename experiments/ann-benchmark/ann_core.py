"""Verified vector input, native Faiss search and explicit approximation budgets."""

import hashlib
import json
from pathlib import Path

import faiss
import numpy as np


def valid_vectors(matrix, dimensions):
    if (matrix.ndim != 2 or matrix.shape[1] != dimensions or not len(matrix)
            or matrix.dtype != np.dtype('<f4') or not np.isfinite(matrix).all()
            or np.max(np.abs(np.linalg.norm(matrix, axis=1) - 1)) > 1e-5):
        raise ValueError('Invalid vector shape/dtype/norm/finite values')


def locale_chunks(directory, manifest, locale, dimensions):
    previous, total = None, 0
    for entry in manifest['shards']:
        if entry['locale'] != locale:
            continue
        shard = directory / 'shards' / f"{entry['index']:05d}"
        checkpoint, identities, payload = [(shard / name).read_bytes()
                                           for name in ('checkpoint.json', 'ids.jsonl', 'shard-00000.f32')]
        if (hashlib.sha256(checkpoint).hexdigest() != entry['checkpoint_sha256']
                or hashlib.sha256(identities).hexdigest() != entry['metrics']['ids_sha256']
                or len(identities) != entry['metrics']['ids_bytes']
                or hashlib.sha256(payload).hexdigest() != entry['vector_sha256']
                or len(payload) != entry['vector_bytes']):
            raise ValueError('Catalog shard checksum/size mismatch')
        rows = [json.loads(line) for line in identities.decode().splitlines()]
        if len(rows) != entry['rows'] or len(payload) != len(rows) * dimensions * 4:
            raise ValueError('Catalog row alignment mismatch')
        ids = []
        for row in rows:
            if (set(row) != {'locale', 'product_id'} or row['locale'] != locale
                    or not isinstance(row['product_id'], str) or not row['product_id']
                    or previous is not None and row['product_id'] <= previous):
                raise ValueError('Catalog locale/ID/order mismatch')
            ids.append(row['product_id'])
            previous = row['product_id']
        matrix = np.frombuffer(payload, dtype='<f4').reshape(len(rows), dimensions).copy()
        valid_vectors(matrix, dimensions)
        total += len(rows)
        yield ids, matrix
    if total != manifest['completion']['counts'][locale]:
        raise ValueError('Incomplete locale vector coverage')


def ordered_search(index, query, exact=False, cutoff=100):
    """Exact control expands complete cutoff ties; ANN orders its returned set only."""
    if index.ntotal < cutoff or query.shape != (1, index.d) or query.dtype != np.float32:
        raise ValueError('Invalid query shape/type or undersized index')
    requested = min(index.ntotal, cutoff + 1) if exact else cutoff
    calls = 0
    while True:
        distances, neighbors = index.search(query, requested)
        distances, neighbors = distances[0], neighbors[0]
        calls += 1
        if (len(set(neighbors.tolist())) != requested or not np.isfinite(distances).all()
                or np.any(neighbors < 0) or np.any(neighbors >= index.ntotal)):
            raise ValueError('Invalid native neighbors/scores')
        order = np.lexsort((neighbors, -distances))
        if exact and requested < index.ntotal and distances.min() >= distances[order[cutoff - 1]]:
            requested = min(index.ntotal, requested * 2)
            continue
        selected = order[:cutoff]
        return neighbors[selected].copy(), distances[selected].copy(), calls


def agreement_rows(actual, reference):
    if set(actual) != set(reference):
        raise ValueError('Agreement query coverage mismatch')
    rows = []
    for key in sorted(actual):
        a, b = actual[key], reference[key]
        if len(a) != 100 or len(b) != 100 or len(set(a)) != 100 or len(set(b)) != 100:
            raise ValueError('Agreement requires unique top-100 lists')
        rows.append({'locale': key[0], 'query_id': key[1], 'agreement_at_100': len(set(a) & set(b)) / 100})
    return rows


def choose_candidate(points):
    checks = {}
    for ef, point in sorted(points.items()):
        checks[ef] = {'locale_coverage': set(point['locale_agreement']) == {'es', 'jp', 'us'}
                       and set(point['locale_hybrid_delta']) == {'es', 'jp', 'us'},
                      'overall_agreement': point['overall_agreement'] >= .95,
                      'locale_agreement': all(v >= .90 for v in point['locale_agreement'].values()),
                      'hybrid_paired_lower_budget': point['hybrid_paired_lower'] >= -.005,
                      'locale_hybrid_loss_budget': all(v >= -.005 for v in point['locale_hybrid_delta'].values())}
    passing = [ef for ef, flags in checks.items() if all(flags.values())]
    return {'selected_efSearch': min(passing) if passing else None, 'checks': checks,
            'production_or_final_test_adoption': False}
