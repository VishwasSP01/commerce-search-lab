"""Bounded immutable Elasticsearch catalog loader; no retry of ambiguous writes."""

import base64
from collections import Counter
import csv
import time

from es_pilot import LOCALES, bulk_payload, index_definition, owned_index, validate_bulk


def export_rows(path):
    previous = None
    with path.open(encoding='utf-8', newline='') as handle:
        for parts in csv.reader(handle, delimiter='\t'):
            if len(parts) != 3:
                raise ValueError('Invalid export schema')
            locale, product, title = parts
            key = (locale, product)
            if locale not in LOCALES or not product or previous is not None and key <= previous:
                raise ValueError('Invalid, duplicate or unordered export identity')
            previous = key
            yield {'locale': locale, 'product_id': product, 'title': base64.b64decode(title, validate=True).decode('utf-8')}


def batches(rows, max_documents=500, max_bytes=1048576):
    if max_documents < 1 or max_bytes < 1:
        raise ValueError('Invalid batch bounds')
    pending, payload, size = [], [], 0
    for row in rows:
        text = bulk_payload([row])
        length = len(text.encode('utf-8'))
        if length > max_bytes:
            raise ValueError('Single document exceeds byte budget')
        if pending and (len(pending) >= max_documents or size + length > max_bytes):
            yield pending, ''.join(payload)
            pending, payload, size = [], [], 0
        pending.append(row); payload.append(text); size += length
    if pending:
        yield pending, ''.join(payload)


def wait_merge(client, task, seconds=600):
    if not isinstance(task, str) or not task or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-' for c in task):
        raise ValueError('Invalid merge task identity')
    started = time.monotonic()
    while True:
        response = client.request('GET', f'/_tasks/{task}')
        if type(response.get('completed')) is not bool or 'error' in response:
            raise ValueError('Merge task failed or returned malformed status')
        if response['completed']:
            shards = response.get('response', {}).get('_shards', {})
            if shards.get('failed') != 0 or shards.get('successful') != 1 or shards.get('total') != 1:
                raise ValueError('Merge task shard result failed')
            return response
        if time.monotonic() - started >= seconds:
            raise TimeoutError('Uncancellable merge task deadline exceeded; inspect task without resubmitting')
        time.sleep(1)


def verify_index(client, index, counts):
    if client.request('GET', f'/{index}/_count')['count'] != sum(counts.values()):
        raise ValueError('Total index count mismatch')
    for locale, count in counts.items():
        if client.request('POST', f'/{index}/_count', {'query': {'term': {'locale': locale}}})['count'] != count:
            raise ValueError('Locale index count mismatch')
    health = client.request('GET', f'/_cluster/health/{index}?wait_for_status=green&timeout=30s')
    stats = client.request('GET', f'/{index}/_stats/docs,store,segments')['indices'][index]['primaries']
    if (health.get('timed_out') or health['status'] != 'green' or health['active_primary_shards'] != 1
            or stats['docs']['deleted'] != 0 or stats['docs']['count'] != sum(counts.values()) or stats['segments']['count'] != 1):
        raise ValueError('Immutable index health/deletion/segment mismatch')
    settings = client.request('GET', f'/{index}/_settings')[index]['settings']['index']
    if settings.get('blocks', {}).get('write') != 'true':
        raise ValueError('Immutable index is not write-blocked')
    return {'uuid': settings['uuid'], 'stats': stats, 'counts': dict(counts), 'health': health}


def load(client, index, rows, policy, observe=lambda: None):
    owned_index(index)
    if client.request('GET', f'/{index}', missing=True) is not None:
        raise FileExistsError('Use a fresh concrete task-owned index')
    created = client.request('PUT', f'/{index}', index_definition(policy))
    if not created.get('acknowledged') or not created.get('shards_acknowledged'):
        raise ValueError('Index creation was not acknowledged')
    started = time.monotonic()
    counts, catalog, titles = Counter(), set(), []
    batch_count, next_progress = 0, 100000
    for chunk, payload in batches(rows, policy['bulk_max_documents'], policy['bulk_max_bytes']):
        if time.monotonic() - started > policy['maximum_indexing_seconds']:
            raise TimeoutError('Indexing budget exceeded; preserve partial index')
        response = client.request('POST', f'/{index}/_bulk', payload, ndjson=True)
        validate_bulk(response, len(chunk))
        for row, item in zip(chunk, response['items'], strict=True):
            key = (row['locale'], row['product_id'])
            if item['create'].get('_id') != ':'.join(key) or item['create'].get('_index') != index or key in catalog:
                raise ValueError('Bulk response identity drift or repeated product')
            catalog.add(key); counts[row['locale']] += 1
            if counts[row['locale']] <= 30:
                titles.append(row)
        batch_count += 1
        if len(catalog) >= next_progress:
            observe(); print(f'{index}: {len(catalog):,} products indexed', flush=True); next_progress += 100000
    if dict(counts) != policy['catalog_by_locale']:
        raise ValueError('Export locale counts differ from declared corpus')
    bulk_seconds = time.monotonic() - started
    client.request('POST', f'/{index}/_refresh')
    if not client.request('PUT', f'/{index}/_settings', {'index.blocks.write': True}).get('acknowledged'):
        raise ValueError('Write block not acknowledged')
    tick = time.monotonic()
    task = client.request('POST', f'/{index}/_forcemerge?max_num_segments=1&wait_for_completion=false')['task']
    wait_merge(client, task, policy['maximum_merge_seconds'])
    merge_seconds = time.monotonic() - tick
    client.request('POST', f'/{index}/_flush')
    return {'index': verify_index(client, index, counts), 'catalog': catalog, 'titles': titles,
            'bulk_batches': batch_count, 'bulk_seconds': bulk_seconds, 'merge_seconds': merge_seconds, 'merge_task': task}
