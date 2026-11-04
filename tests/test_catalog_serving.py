import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from catalog_serving import batches, export_rows, load, verify_index, wait_merge


class CatalogServingTests(unittest.TestCase):
    def test_export_stream_preserves_string_ids_and_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'products.tsv'
            path.write_text('jp\t001\t' + base64.b64encode('空港'.encode()).decode() + '\n')
            self.assertEqual(list(export_rows(path)), [{'locale': 'jp', 'product_id': '001', 'title': '空港'}])

    def test_export_rejects_duplicate_order_schema_locale_and_base64_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'products.tsv'
            for text in ['us\t001\teA==\nus\t001\teA==\n', 'us\t002\teA==\nes\t001\teA==\n',
                         'bad\t001\teA==\n', 'us\t001\t!\n', 'us\t001\n']:
                path.write_text(text)
                with self.assertRaises(ValueError): list(export_rows(path))

    def test_batches_enforce_utf8_bytes_and_document_bounds_without_dropping_rows(self):
        rows = [{'locale': 'jp', 'product_id': str(i), 'title': '空港' * 10} for i in range(7)]
        chunks = list(batches(iter(rows), 2, 500))
        self.assertEqual([row for chunk, _ in chunks for row in chunk], rows)
        self.assertTrue(all(len(chunk) <= 2 and len(payload.encode()) <= 500 for chunk, payload in chunks))
        with self.assertRaises(ValueError): list(batches(rows, 1, 10))

    def test_partial_bulk_failure_stops_without_retry_or_merge(self):
        client = Mock()
        client.request.side_effect = [None, {'acknowledged': True, 'shards_acknowledged': True},
                                      {'errors': True, 'items': [{'create': {'status': 429}}]}]
        with self.assertRaises(ValueError):
            load(client, 'csl-fixture-failure', iter([{'locale': 'us', 'product_id': '001', 'title': 'x'}]),
                 {'k1': 1.2, 'b': .75, 'bulk_max_documents': 500, 'bulk_max_bytes': 1048576, 'maximum_indexing_seconds': 60})
        self.assertEqual(client.request.call_count, 3)

    def test_existing_index_is_never_overwritten(self):
        client = Mock(); client.request.return_value = {'index': {}}
        with self.assertRaises(FileExistsError): load(client, 'csl-fixture-existing', iter([]), {})
        self.assertEqual(client.request.call_count, 1)

    def test_async_merge_success_failure_and_deadline_do_not_resubmit(self):
        client = Mock()
        client.request.side_effect = [{'completed': False}, {'completed': True, 'response': {'_shards': {'failed': 0, 'successful': 1, 'total': 1}}}]
        with patch('catalog_serving.time.sleep'): self.assertTrue(wait_merge(client, 'node:1')['completed'])
        self.assertTrue(all(call.args[0] == 'GET' for call in client.request.call_args_list))
        client.request.side_effect = None; client.request.return_value = {'completed': True, 'error': {'type': 'failed'}}
        with self.assertRaises(ValueError): wait_merge(client, 'node:1')
        client.request.return_value = {'completed': False}
        with patch('catalog_serving.time.monotonic', side_effect=[0, 601]):
            with self.assertRaises(TimeoutError): wait_merge(client, 'node:1')

    def test_locale_count_drift_is_rejected(self):
        client = Mock(); client.request.side_effect = [{'count': 3}, {'count': 0}]
        with self.assertRaises(ValueError): verify_index(client, 'csl-fixture-counts', {'es': 1, 'jp': 1, 'us': 1})


if __name__ == '__main__': unittest.main()
