"""Audit diagnostic event joins, raw metrics and Prometheus timer units offline."""
import json
import math
from pathlib import Path
from api_load import parse_metrics
from api_timings import STAGES, correlate, events
from download_esci import ROOT
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest


def metric_sum(text, name, labels=()):
    return sum(float(line.rsplit(' ', 1)[1]) for line in text.splitlines()
               if (line.startswith(name + '{') or line.startswith(name + ' ')) and all(label in line for label in labels))


def audit_meters(folder):
    before = (folder / 'before.prom').read_text(); after = (folder / 'after.prom').read_text()
    rows = events(folder / 'api.log', 'REQUEST_TIMING ')
    checks = [('commerce_search_request_seconds', (), len(rows), sum(r['filter_ns'] for r in rows) / 1e9)]
    for stage in STAGES:
        observed = [row['stages_ns'][stage] for row in rows if stage in row['stages_ns']]
        checks.append(('commerce_backend_stage_seconds', ('operation="search"', f'stage="{stage.split(".")[1]}"'), len(observed), sum(observed) / 1e9))
    took = [r['es_took_ms'] for r in rows if r['es_took_ms'] is not None]
    checks.append(('commerce_elasticsearch_took_seconds', (), len(took), sum(took) / 1000))
    for name, labels, count, seconds in checks:
        actual_count = metric_sum(after, name + '_count', labels) - metric_sum(before, name + '_count', labels)
        actual_sum = metric_sum(after, name + '_sum', labels) - metric_sum(before, name + '_sum', labels)
        if actual_count != count or not math.isclose(actual_sum, seconds, abs_tol=1e-6, rel_tol=1e-9):
            raise ValueError(f'Meter count/seconds disagree with events: {name}/{labels}')
    if metric_sum(after, 'commerce_backend_inflight') != 0: raise ValueError('Backend permits/inflight did not drain')
    if any(tag in after for tag in ('request_id=', 'query=', 'product_id=')): raise ValueError('Unbounded sensitive metric label')
    return {'request_backend_and_es_took_counts_and_seconds_match': True, 'backend_inflight_drained': True}


def main():
    report_path = ROOT / 'reports/api-timings-v1.json'; directory = ROOT / 'data/processed/api-timings-v1'
    output = ROOT / 'reports/api-timings-v1-verification.json'
    if output.exists(): raise FileExistsError('Preserve prior verification')
    report = json.loads(report_path.read_text())
    for name, sha in report['provenance']['source_sha256'].items():
        if file_digest(ROOT / name) != sha: raise ValueError(f'Diagnostic source changed: {name}')
    if file_digest(directory / 'workload.json') != report['workload_sha256']: raise ValueError('Workload changed')
    artifacts, audited = [], []
    for i, (version, saved) in enumerate(zip(report['config']['order'], report['bursts'], strict=True)):
        folder = directory / f'{i}-{version}'
        actual = parse_metrics(folder / 'raw.ndjson', report['config']['rate'], report['config']['duration_seconds'])
        if actual != saved['metrics']: raise ValueError('Raw metric recomputation differs')
        for name, field in [('raw.ndjson', 'raw_sha256'), ('api.log', 'server_log_sha256'), ('client.log', 'client_log_sha256'), ('resources.json', 'resources_sha256')]:
            if file_digest(folder / name) != saved[field]: raise ValueError('Raw diagnostic artifact changed')
            artifacts.append({'name': str((folder / name).relative_to(directory)), 'sha256': file_digest(folder / name)})
        if version == 'instrumented':
            paired = correlate(folder / 'raw.ndjson', folder / 'client.log', folder / 'api.log', folder / 'before.prom', folder / 'after.prom')
            if paired != saved['correlation']: raise ValueError('Per-request correlation differs')
            audited.append({'burst': i, **audit_meters(folder)})
            for name in ('before.prom', 'after.prom'): artifacts.append({'name': str((folder / name).relative_to(directory)), 'sha256': file_digest(folder / name)})
    if file_digest(directory / 'functional/results.tsv') != report['functional']['ranked_sha256']: raise ValueError('Functional result changed')
    result = {'report_sha256': file_digest(report_path), 'verification_script_sha256': file_digest(Path(__file__)),
              'all_raw_metrics_and_correlations_recomputed': True, 'meter_audits': audited, 'artifacts': artifacts,
              'limits': 'Accounting/unit/correlation audit, not repeated timings or proof of historical causality. Nested timers and marginal percentiles are not additive; paired residuals retain unmeasured boundaries.'}
    atomic_json(output, result)
    print(json.dumps({'raw_metrics_correlations_recomputed': True, 'prometheus_count_and_unit_audits': len(audited)}))


if __name__ == '__main__': main()
