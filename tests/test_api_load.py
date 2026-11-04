import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from api_load import distribution, parse_metrics, select_workload


class ApiLoadTests(unittest.TestCase):
    def test_sampling_is_locale_balanced_and_independent_of_input_order(self):
        rows = [{'locale': locale, 'query_id': i, 'query': str(i)} for locale in ('es', 'jp', 'us') for i in range(4)]
        expected = {(q['locale'], q['query_id']): [('001', 2.2)] for q in rows}
        self.assertEqual(select_workload(rows, expected, 'seed', 2), select_workload(list(reversed(rows)), expected, 'seed', 2))
        self.assertEqual(len(select_workload(rows, expected, 'seed', 2)), 6)

    def test_percentiles_include_tail_and_empty_is_undefined(self):
        self.assertIsNone(distribution([]))
        self.assertGreater(distribution([1, 2, 3, 100])['p99_ms'], 90)

    def raw(self, directory, *, dropped=0, bad=False, incomplete=False):
        rows = []
        def point(metric, value, tags=None): rows.append({'type': 'Point', 'metric': metric, 'data': {'value': value, 'tags': tags or {}}})
        point('api_started', 2); point('api_completed', 1 if incomplete else 2); point('http_reqs', 2)
        point('api_wrong_results', 1 if bad else 0); point('dropped_iterations', dropped)
        for value, status, ok in [(1, '200', 'false' if bad else 'true'), (1000, '504', 'false')]:
            point('api_attempt_ms', value, {'locale': 'us', 'query_id': '1', 'status': status, 'ok': ok, 'code': ''})
            for name in ('http_req_duration', 'http_req_blocked', 'http_req_connecting', 'http_req_waiting'): point(name, value)
        path = directory / 'raw.json'; path.write_text('\n'.join(json.dumps(r) for r in rows)); return path

    def test_dropped_work_and_non200_are_loss_and_tail_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = parse_metrics(self.raw(Path(tmp), dropped=1), 3, 1)
        self.assertEqual(report['loss_fraction'], 2/3); self.assertFalse(report['qualified'])
        self.assertEqual(report['correct_http200_wall_ms']['samples'], 1)
        self.assertGreater(report['all_attempt_wall_ms']['p99_ms'], 900)

    def test_incomplete_raw_samples_fail_and_wrong_200_is_not_transport_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): parse_metrics(self.raw(Path(tmp), incomplete=True), 2, 1)
            report = parse_metrics(self.raw(Path(tmp), bad=True), 2, 1)
            self.assertEqual(report['incorrect_http200'], 1); self.assertEqual(report['non200_or_transport_failures'], 1)


if __name__ == '__main__': unittest.main()
