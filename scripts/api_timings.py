"""Correlate local per-request events without subtracting aggregate percentiles."""
from collections import Counter
import json
import math
import re
from api_load import distribution

UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
STAGES = ('search.request_build', 'search.http_wait', 'search.json_decode', 'search.result_validation', 'search.operation')


def events(path, marker):
    output = []
    for line in path.read_text().splitlines():
        try:
            wrapper = json.loads(line)
            if isinstance(wrapper, dict) and isinstance(wrapper.get('msg'), str): line = wrapper['msg']
        except json.JSONDecodeError:
            pass
        if marker in line: output.append(json.loads(line.split(marker, 1)[1]))
    return output


def scrape_count(text):
    return sum(float(line.rsplit(' ', 1)[1]) for line in text.splitlines()
               if line.startswith('commerce_search_request_seconds_count{') or line.startswith('commerce_search_request_seconds_count '))


def correlate(raw_path, client_log, server_log, before, after):
    clients = events(client_log, 'CLIENT_TIMING ')
    raw = []
    with raw_path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row['type'] == 'Point' and row['metric'] == 'api_attempt_ms':
                tags = row['data']['tags']; raw.append((tags['locale'], tags['query_id'], int(tags['status']), row['data']['value']))
    if Counter(raw) != Counter((c['locale'], c['query_id'], c['status'], c['client_ms']) for c in clients):
        raise ValueError('Client event/raw metric multisets differ')
    rows = events(server_log, 'REQUEST_TIMING ')
    by_id = {}
    for row in rows:
        if not UUID.fullmatch(row['request_id']) or row['request_id'] in by_id: raise ValueError('Invalid/duplicate server request identity')
        if set(row) != {'request_id', 'locale', 'status', 'outcome', 'filter_ns', 'stages_ns', 'es_took_ms'}: raise ValueError('Unexpected potentially sensitive event field')
        if type(row['filter_ns']) is not int or row['filter_ns'] < 0: raise ValueError('Invalid filter duration')
        if any(key not in STAGES or type(value) is not int or value < 0 for key, value in row['stages_ns'].items()): raise ValueError('Invalid stage event')
        if row['es_took_ms'] is not None and (type(row['es_took_ms']) is not int or row['es_took_ms'] < 0): raise ValueError('Invalid ES took observation')
        by_id[row['request_id']] = row
    if scrape_count(after.read_text()) - scrape_count(before.read_text()) != len(rows): raise ValueError('Server log/scrape request counts differ')
    paired = []
    used = set()
    for client in clients:
        identity = client['request_id']
        if identity is None: continue
        if identity in used or identity not in by_id: raise ValueError('Client correlation header missing/duplicated in server events')
        used.add(identity); server = by_id[identity]
        if server['status'] != client['status'] or server['locale'] != client['locale']: raise ValueError('Paired status/locale differs')
        paired.append({'client_ms': client['client_ms'], 'filter_ms': server['filter_ns'] / 1e6,
                       'paired_client_minus_filter_ms': client['client_ms'] - server['filter_ns'] / 1e6,
                       'es_took_ms': server['es_took_ms'], **{key + '_ms': server['stages_ns'].get(key, None) / 1e6 if key in server['stages_ns'] else None for key in STAGES}})
    keys = ['client_ms', 'filter_ms', 'paired_client_minus_filter_ms', 'es_took_ms'] + [key + '_ms' for key in STAGES]
    summaries = {key: distribution([row[key] for row in paired if row[key] is not None]) for key in keys}
    # distribution retains negative residuals: durations differ in resolution/finalization scope.
    if any(not math.isfinite(row['paired_client_minus_filter_ms']) for row in paired): raise ValueError('Nonfinite paired residual')
    return {'client_events': len(clients), 'server_events': len(rows), 'paired_requests': len(paired),
            'clients_without_returned_id': sum(c['request_id'] is None for c in clients), 'unmatched_server_events': len(by_id) - len(used),
            'negative_paired_residuals': sum(row['paired_client_minus_filter_ms'] < 0 for row in paired),
            'duration_summaries': summaries, 'slowest_paired_requests_anonymized': sorted(paired, key=lambda row: row['client_ms'], reverse=True)[:10]}
