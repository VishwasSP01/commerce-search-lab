"""Recompute load results from raw samples; never rerun or retime a benchmark."""
import json
from pathlib import Path
from api_load import parse_metrics
from download_esci import ROOT
from embedding_checkpoint import atomic_json
from prepare_esci_splits import file_digest


def resource_summary(path):
    raw = json.loads(path.read_text())
    if raw['errors'] or not raw['samples']: raise ValueError('Resource monitoring failed')
    output = {}
    for phase in sorted({row['phase'] for row in raw['samples']}):
        rows = [r for r in raw['samples'] if r['phase'] == phase]
        api = [r['api'] for r in rows if r['api']]
        generators = [r['generator'] for r in rows if r['generator']]
        queues = [r['node']['thread_pool']['search']['queue'] for r in rows]
        rejected = [r['node']['thread_pool']['search']['rejected'] for r in rows]
        output[phase] = {'samples': len(rows), 'sampled_max_api_rss_bytes': max(p['rss_bytes'] for p in api),
                         'sampled_max_generator_rss_bytes': max((p['rss_bytes'] for p in generators), default=None),
                         'max_observed_api_ps_cpu_lifetime_percent': max(p['ps_cpu_percent_lifetime_average'] for p in api),
                         'max_observed_generator_ps_cpu_lifetime_percent': max((p['ps_cpu_percent_lifetime_average'] for p in generators), default=None),
                         'sampled_max_es_cpu_percent': max(r['node']['process']['cpu']['percent'] for r in rows),
                         'sampled_max_es_search_queue': max(queues), 'sampled_es_search_rejections_delta': max(rejected) - min(rejected),
                         'sampled_max_es_heap_used_bytes': max(r['node']['jvm']['mem']['heap_used_in_bytes'] for r in rows),
                         'observed_cgroup_lifetime_peak_bytes': max(r['container']['container_lifetime_cgroup_memory_peak_bytes'] for r in rows)}
    return output


def main():
    path = ROOT / 'reports/api-load-v1.json'
    directory = ROOT / 'data/processed/api-load-v1'
    output = ROOT / 'reports/api-load-v1-verification.json'
    if output.exists(): raise FileExistsError('Preserve previous verification evidence')
    report = json.loads(path.read_text()); policy = report['config']
    for name, sha in report['provenance']['source_sha256'].items():
        if file_digest(ROOT / name) != sha: raise ValueError(f'Measured source changed: {name}')
    if file_digest(directory / 'workload.json') != report['workload_sha256']: raise ValueError('Workload changed')
    artifacts = []
    resources = {}
    for name, saved in zip(('ascending', 'descending'), report['campaigns'], strict=True):
        expected_order = policy['rates'] if name == 'ascending' else list(reversed(policy['rates']))
        if saved['rate_order'] != expected_order: raise ValueError('Campaign order changed')
        for label, result in [('warmup', saved['warmup'])] + [(f"rate-{r['offered_rps']}", r) for r in saved['points']]:
            raw = directory / name / label / 'raw.ndjson'
            actual = parse_metrics(raw, result['offered_rps'], result['duration_seconds'])
            if actual != {k: v for k, v in result.items() if k not in ('raw_sha256', 'k6_exit_code')}: raise ValueError('Stored count/percentile/decision differs from raw samples')
            if file_digest(raw) != result['raw_sha256']: raise ValueError('Raw metric file changed')
            artifacts.append({'name': str(raw.relative_to(directory)), 'bytes': raw.stat().st_size, 'sha256': file_digest(raw)})
        raw = directory / name / 'resources.json'
        if file_digest(raw) != saved['resources_sha256']: raise ValueError('Resource evidence changed')
        resources[name] = resource_summary(raw)
    highest = set(r['offered_rps'] for r in report['campaigns'][0]['points'] if r['qualified']) & set(r['offered_rps'] for r in report['campaigns'][1]['points'] if r['qualified'])
    if (max(highest) if highest else None) != report['highest_qualifying_tested_rate_in_both_runs']: raise ValueError('Qualification differs')
    result = {'report_sha256': file_digest(path), 'verification_script_sha256': file_digest(Path(__file__)),
              'raw_artifacts': artifacts, 'all_counts_percentiles_and_qualifications_recomputed': True,
              'workload_and_sources_unchanged': True, 'resource_observations': resources,
              'limits': 'Metric accounting reproducibility only; timing distributions are distinct measured runs. One-second resource samples/lifetime CPU snapshots cannot isolate Tomcat queueing or prove a bottleneck.'}
    atomic_json(output, result)
    print(json.dumps({'raw_files_verified': len(artifacts), 'counts_percentiles_qualification_recomputed': True}))


if __name__ == '__main__': main()
