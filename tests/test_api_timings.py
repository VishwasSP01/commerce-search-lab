from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from api_timings import correlate


class TimingCorrelationTests(unittest.TestCase):
    def fixture(self, directory):
        identity = '00000000-0000-0000-0000-000000000001'
        data = {'request_id': identity, 'locale': 'us', 'status': 200, 'outcome': 'success', 'filter_ns': 1500000,
                'stages_ns': {'search.http_wait': 500000}, 'es_took_ms': 0}
        (directory / 'server').write_text('REQUEST_TIMING ' + json.dumps(data))
        (directory / 'client').write_text('CLIENT_TIMING ' + json.dumps({'request_id': identity, 'locale': 'us', 'query_id': '1', 'status': 200, 'ok': True, 'client_ms': 1}))
        (directory / 'raw').write_text(json.dumps({'type': 'Point', 'metric': 'api_attempt_ms', 'data': {'value': 1, 'tags': {'locale': 'us', 'query_id': '1', 'status': '200'}}}))
        (directory / 'before').write_text('')
        (directory / 'after').write_text('commerce_search_request_seconds_count{locale="us"} 1\n')

    def test_pairing_preserves_negative_residual_and_unknown_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); self.fixture(directory)
            result = correlate(*(directory / p for p in ('raw', 'client', 'server', 'before', 'after')))
        self.assertEqual(result['paired_requests'], 1); self.assertEqual(result['negative_paired_residuals'], 1)
        self.assertIsNone(result['duration_summaries']['search.json_decode_ms'])
        self.assertEqual(result['duration_summaries']['es_took_ms']['p50_ms'], 0)

    def test_missing_identity_or_scrape_count_never_silently_drops_a_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); self.fixture(directory)
            (directory / 'after').write_text('commerce_search_request_seconds_count{} 2\n')
            with self.assertRaises(ValueError): correlate(*(directory / p for p in ('raw', 'client', 'server', 'before', 'after')))
            self.fixture(directory); (directory / 'server').write_text('')
            with self.assertRaises(ValueError): correlate(*(directory / p for p in ('raw', 'client', 'server', 'before', 'after')))

    def test_k6_structured_console_wrapper_is_decoded_before_event_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); self.fixture(directory)
            message = (directory / 'client').read_text()
            (directory / 'client').write_text(json.dumps({'level': 'info', 'msg': message, 'source': 'console'}))
            result = correlate(*(directory / p for p in ('raw', 'client', 'server', 'before', 'after')))
            self.assertEqual(result['paired_requests'], 1)


if __name__ == '__main__': unittest.main()
