from pathlib import Path
import sys
import unittest

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_catalog import ReferenceComparison, catalog_batches


class CatalogExportTests(unittest.TestCase):
    def test_stream_is_locale_sorted_with_short_final_shards_and_string_ids(self):
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE products(product_locale VARCHAR, product_id VARCHAR, product_title VARCHAR)")
            connection.execute("INSERT INTO products VALUES ('jp','001','靴'), ('es','003','c'), ('es','001','a'), ('es','002','b')")
            chunks = list(catalog_batches(connection, [{"locale": "jp"}, {"locale": "es"}], 2))
        self.assertEqual([[r["product_id"] for r in chunk] for chunk in chunks], [["001", "002"], ["003"], ["001"]])
        self.assertEqual([chunk[0]["locale"] for chunk in chunks], ["es", "es", "jp"])

    def test_reference_comparison_uses_locale_and_row_identity_not_position(self):
        reference = {("es", "A"): np.array([1., 0.]), ("jp", "A"): np.array([0., 1.])}
        comparison = ReferenceComparison(reference)
        comparison.check([{"locale": "jp", "product_id": "A"}, {"locale": "es", "product_id": "unknown"}],
                         np.array([[0., 1.], [.5, .5]]))
        with self.assertRaises(ValueError):
            comparison.result()
        comparison.check([{"locale": "es", "product_id": "A"}], np.array([[1., 0.]]))
        self.assertEqual(comparison.result()["identities_compared"], 2)
        with self.assertRaises(ValueError):
            comparison.check([{"locale": "es", "product_id": "A"}], np.array([[1., 0.]]))

    def test_reference_difference_fails_even_for_another_unit_vector(self):
        comparison = ReferenceComparison({("es", "A"): np.array([1., 0.])})
        comparison.check([{"locale": "es", "product_id": "A"}], np.array([[0., 1.]]))
        with self.assertRaises(ValueError):
            comparison.result()


if __name__ == "__main__":
    unittest.main()
