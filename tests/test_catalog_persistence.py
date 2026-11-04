from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_catalog_serving import restored_index


class RestoredPrimaryTests(unittest.TestCase):
    def test_node_response_does_not_substitute_for_recovered_index_health(self):
        client = Mock()
        for health in [{'status': 'yellow', 'timed_out': True, 'active_primary_shards': 0},
                       {'status': 'green', 'timed_out': False, 'active_primary_shards': 0}]:
            client.request.return_value = health
            with patch('verify_catalog_serving.verify_index') as counts:
                with self.assertRaises(ValueError): restored_index(client, 'csl-pilot-catalog-v1-a', {'us': 1})
                counts.assert_not_called()
        client.request.return_value = {'status': 'green', 'timed_out': False, 'active_primary_shards': 1}
        with patch('verify_catalog_serving.verify_index', return_value={'uuid': 'retained'}) as counts:
            self.assertEqual(restored_index(client, 'csl-pilot-catalog-v1-a', {'us': 1}), {'uuid': 'retained'})
            counts.assert_called_once()


if __name__ == '__main__': unittest.main()
