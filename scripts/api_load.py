"""Deterministic query sampling and independent granular k6 metric accounting."""
from collections import Counter
import hashlib
import json
import math
from ranking_metrics import percentile


def select_workload(queries, expected, seed, per_locale=100):
    def key(q): return (hashlib.sha256(f"{seed}\t{q['locale']}\t{q['query_id']}".encode()).hexdigest(), q['locale'], q['query_id'])
    selected = []
    for locale in ('es', 'jp', 'us'):
        rows = sorted([q for q in queries if q['locale'] == locale], key=key)
        if len(rows) < per_locale: raise ValueError('Insufficient locale queries')
        selected += rows[:per_locale]
    return [dict(q, expected=expected[(q['locale'], q['query_id'])][:10]) for q in sorted(selected, key=key)]


def distribution(values):
    if not values: return None
    values = sorted(values)
    return {'samples': len(values), 'p50_ms': percentile(values, .5), 'p95_ms': percentile(values, .95),
            'p99_ms': percentile(values, .99), 'max_ms': values[-1]}


def parse_metrics(path, rate, seconds):
    counts = Counter(); statuses = Counter(); codes = Counter(); locales = Counter(); identities = set()
    attempts, correct = [], []
    timings = {key: [] for key in ('http_req_duration', 'http_req_blocked', 'http_req_connecting', 'http_req_waiting')}
    vus = 0
    with path.open() as handle:
        for line in handle:
            record = json.loads(line)
            if record['type'] != 'Point': continue
            name, data = record['metric'], record['data']
            value, tags = data['value'], data.get('tags') or {}
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0: raise ValueError('Invalid raw metric value')
            if name in ('api_started', 'api_completed', 'api_wrong_results', 'dropped_iterations', 'http_reqs'): counts[name] += value
            elif name == 'vus': vus = max(vus, value)
            elif name in timings: timings[name].append(value)
            elif name == 'api_attempt_ms':
                if tags.get('ok') not in ('true', 'false') or tags.get('locale') not in ('es', 'jp', 'us'): raise ValueError('Invalid attempt classification')
                attempts.append(value)
                if tags['ok'] == 'true':
                    if tags['status'] != '200': raise ValueError('Success must be HTTP200')
                    correct.append(value)
                statuses[tags['status']] += 1
                if tags.get('code'): codes[tags['code']] += 1
                locales[tags['locale']] += 1; identities.add((tags['locale'], tags['query_id']))
    for key, value in counts.items():
        if value != int(value): raise ValueError('Noninteger counter')
    started = int(counts['api_started']); dropped = int(counts['dropped_iterations']); planned = rate * seconds; scheduled = started + dropped
    if not (started == counts['api_completed'] == counts['http_reqs'] == len(attempts)):
        raise ValueError('Started/completed/HTTP/latency samples do not reconcile')
    if scheduled < 1 or abs(scheduled - planned) > 1: raise ValueError('Arrival schedule does not reconcile within declared endpoint tolerance')
    if any(len(rows) != started for rows in timings.values()): raise ValueError('Missing raw HTTP timing samples')
    wrong = statuses['200'] - len(correct)
    if wrong != counts['api_wrong_results']: raise ValueError('Incorrect-result accounting mismatch')
    return {'offered_rps': rate, 'duration_seconds': seconds, 'planned_slots': planned, 'scheduled_slots': scheduled, 'started': started, 'completed': started,
            'dropped': dropped, 'correct_http200': len(correct), 'incorrect_http200': wrong,
            'non200_or_transport_failures': started - statuses['200'], 'loss_fraction': (scheduled - len(correct)) / scheduled,
            'achieved_started_rps': started / seconds, 'correct_completion_rps_per_scheduled_window': len(correct) / seconds,
            'statuses': dict(statuses), 'error_codes': dict(codes), 'locale_requests': dict(locales), 'unique_queries': len(identities),
            'all_attempt_wall_ms': distribution(attempts), 'correct_http200_wall_ms': distribution(correct),
            'k6_timings_ms': {key: distribution(rows) for key, rows in timings.items()}, 'observed_max_vus': vus,
            'qualified': wrong == 0 and len(correct) / scheduled >= .99}
