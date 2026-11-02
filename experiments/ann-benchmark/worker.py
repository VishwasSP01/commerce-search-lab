"""Isolated build/query workers; each process loads one locale and one index."""

import argparse
import csv
import gc
import json
from pathlib import Path
import random
import resource
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import faiss
import numpy as np

from ann_core import locale_chunks, ordered_search, valid_vectors
from download_esci import verify_file
from embedding_checkpoint import atomic_json, fingerprint
from prepare_esci_splits import file_digest


def peak_rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def verify_index(directory, contract, locale):
    record = json.loads((directory / 'build.json').read_text())
    if record['contract_fingerprint'] != fingerprint(contract) or record['locale'] != locale:
        raise ValueError('Index contract or locale changed')
    for artifact in record['artifacts']:
        verify_file(directory / artifact['name'], artifact)
    return record


def build(directory, contract, catalog_dir, locale):
    if directory.exists():
        verify_index(directory, contract, locale)
        print(f'Verified completed {locale} index', flush=True)
        return
    started = time.perf_counter()
    policy = contract['policy']
    faiss.omp_set_num_threads(policy['build_threads'])
    manifest = json.loads((catalog_dir / 'export-manifest.json').read_text())
    if file_digest(catalog_dir / 'export-manifest.json') != contract['catalog_manifest_sha256']:
        raise ValueError('Catalog manifest changed')
    index = faiss.IndexHNSWFlat(policy['dimensions'], policy['M'], faiss.METRIC_INNER_PRODUCT)
    index.hnsw.efConstruction = policy['efConstruction']
    index.hnsw.rng = faiss.RandomGenerator(policy['seed'])
    ids, add_seconds = [], 0.0
    for shard, (chunk_ids, vectors) in enumerate(locale_chunks(catalog_dir, manifest, locale, policy['dimensions'])):
        tick = time.perf_counter()
        index.add(vectors)
        add_seconds += time.perf_counter() - tick
        ids.extend(chunk_ids)
        if (shard + 1) % 8 == 0:
            print(f'Building {locale}: {index.ntotal:,}/{manifest["completion"]["counts"][locale]:,}', flush=True)
    if index.ntotal != len(ids) or index.ntotal != manifest['completion']['counts'][locale]:
        raise ValueError('Incomplete constructed index')
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{locale}-build-', dir=directory.parent) as tmp:
        stage = Path(tmp) / 'index'
        stage.mkdir()
        faiss.write_index(index, str(stage / 'hnsw.index'))
        faiss.write_index(faiss.downcast_index(index.storage), str(stage / 'flat.index'))
        atomic_json(stage / 'ids.json', {'locale': locale, 'ids': ids})
        record = {'contract_fingerprint': fingerprint(contract), 'locale': locale, 'products': len(ids),
                  'add_seconds': add_seconds, 'build_wall_seconds': time.perf_counter() - started,
                  'max_process_rss_bytes': peak_rss(), 'threads': faiss.omp_get_max_threads(),
                  'artifacts': [{'name': name, 'bytes': (stage / name).stat().st_size, 'sha256': file_digest(stage / name)}
                                for name in ('hnsw.index', 'flat.index', 'ids.json')],
                  'note': 'Build wall includes input verification, insertion and persistence, excludes final checksums/record serialization. Multithread graph construction is not claimed bit reproducible.'}
        atomic_json(stage / 'build.json', record)
        stage.rename(directory)
    print(f'Completed {locale}: {len(ids):,} products, {add_seconds:.1f}s add work', flush=True)


def search(directory, contract, locale, method, output, dense_dir):
    if output.exists():
        raise FileExistsError('Query output already exists')
    started = time.perf_counter()
    record = verify_index(directory, contract, locale)
    policy = contract['policy']
    faiss.omp_set_num_threads(policy['query_threads'])
    queries = json.loads((dense_dir / 'queries.json').read_text())
    vectors = np.fromfile(dense_dir / 'query-vectors.f32', dtype='<f4').reshape(len(queries), policy['dimensions'])
    positions = [i for i, row in enumerate(queries) if row['locale'] == locale]
    queries = [queries[i] for i in positions]
    vectors = vectors[positions].copy()
    valid_vectors(vectors, policy['dimensions'])
    ids = json.loads((directory / 'ids.json').read_text())
    if ids['locale'] != locale or len(ids['ids']) != record['products']:
        raise ValueError('Index ID map mismatch')
    exact = method == 'flat'
    tick = time.perf_counter()
    index = faiss.read_index(str(directory / ('flat.index' if exact else 'hnsw.index')))
    load_seconds = time.perf_counter() - tick
    if index.ntotal != len(ids['ids']) or index.d != policy['dimensions'] or index.metric_type != faiss.METRIC_INNER_PRODUCT:
        raise ValueError('Native index coverage/dimension/metric mismatch')
    if not exact:
        index.hnsw.efSearch = int(method.removeprefix('hnsw-'))
    order = list(range(len(queries)))
    random.Random(policy['timing_order_seed']).shuffle(order)
    for q in order[:min(policy['warmup_queries'], len(order))]:
        ordered_search(index, vectors[q:q + 1], exact, policy['cutoff'])
    first = [None] * len(queries)
    passes = []
    for number in range(policy['timed_passes']):
        order = list(range(len(queries)))
        random.Random(policy['timing_order_seed'] + number).shuffle(order)
        timings, native_calls = [], []
        for q in order:
            tick = time.perf_counter_ns()
            neighbors, distances, calls = ordered_search(index, vectors[q:q + 1], exact, policy['cutoff'])
            timings.append((time.perf_counter_ns() - tick) / 1e6)
            native_calls.append(calls)
            if first[q] is None:
                first[q] = (neighbors, distances)
            elif not np.array_equal(neighbors, first[q][0]) or not np.array_equal(distances, first[q][1]):
                raise ValueError('Repeated singleton searches changed ranked outputs')
        passes.append({'pass': number, 'query_order': [queries[q]['query_id'] for q in order],
                       'milliseconds': timings, 'native_calls': native_calls})
        print(f'{locale} {method} pass {number + 1}: {sum(timings)/1000:.2f}s retrieval', flush=True)
    output.mkdir(parents=True)
    with (output / 'results.tsv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['product_locale', 'query_id', 'rank', 'product_id', 'score'])
        for row, (neighbors, distances) in zip(queries, first, strict=True):
            for rank, (neighbor, score) in enumerate(zip(neighbors, distances, strict=True), 1):
                writer.writerow([locale, row['query_id'], rank, ids['ids'][int(neighbor)], repr(float(score))])
    atomic_json(output / 'latencies.json', passes)
    atomic_json(output / 'session.json', {'locale': locale, 'method': method, 'load_seconds': load_seconds,
                                        'worker_wall_seconds': time.perf_counter() - started,
                                        'max_process_rss_bytes': peak_rss(), 'threads': faiss.omp_get_max_threads(),
                                        'identical_rankings_all_passes': True, 'queries': len(queries),
                                        'index_build_sha256': file_digest(directory / 'build.json'),
                                        'result_sha256': file_digest(output / 'results.tsv')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build', 'search'))
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--locale', required=True)
    parser.add_argument('--catalog-dir', type=Path)
    parser.add_argument('--dense-dir', type=Path)
    parser.add_argument('--method')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text())
    if faiss.__version__ != contract['policy']['faiss_version']:
        raise ValueError('Unexpected native library version')
    if args.mode == 'build':
        build(args.directory, contract, args.catalog_dir, args.locale)
    else:
        search(args.directory, contract, args.locale, args.method, args.output, args.dense_dir)


if __name__ == '__main__':
    main()
