"""Regression checks for corrupt inputs, incorrect joins and hidden split overlap."""

import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_esci import audit
from download_esci import download_file, verify_file


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.connection = duckdb.connect(config={"threads": 1})
        self.addCleanup(self.connection.close)
        self.connection.execute("""
            CREATE TABLE examples (
                example_id BIGINT, query VARCHAR, query_id BIGINT,
                product_id VARCHAR, product_locale VARCHAR, esci_label VARCHAR,
                small_version INTEGER, large_version INTEGER, split VARCHAR
            );
            CREATE TABLE products_raw (
                product_id VARCHAR, product_title VARCHAR, product_description VARCHAR,
                product_bullet_point VARCHAR, product_brand VARCHAR, product_color VARCHAR,
                product_locale VARCHAR
            );
            CREATE TABLE sources (query_id BIGINT, source VARCHAR);
            INSERT INTO examples VALUES
                (1, 'Running shoes', 10, 'A', 'us', 'E', 1, 1, 'train'),
                (2, 'Sandals', 20, 'B', 'us', 'I', 1, 1, 'test');
            INSERT INTO products_raw VALUES
                ('A', 'Shoes', NULL, '', 'Brand', NULL, 'us'),
                ('B', 'Sandals', 'Description', NULL, NULL, 'Red', 'us');
            INSERT INTO sources VALUES (10, 'fixture'), (20, 'fixture');
        """)

    def test_join_must_use_locale_and_product(self):
        self.connection.execute("UPDATE examples SET product_locale = 'es' WHERE example_id = 2")
        result = audit(self.connection)
        self.assertEqual(result["integrity"]["unmatched_example_rows"], 1)
        self.assertEqual(result["integrity"]["unmatched_product_keys"], 1)

    def test_overlap_detects_normalized_text_despite_different_ids(self):
        self.connection.execute("UPDATE examples SET query = 'ＲＵＮＮＩＮＧ   SHOES' WHERE example_id = 2")
        result = audit(self.connection)["subsets"]["ranking"]["train_test_overlap"]
        self.assertEqual({key: result[key] for key in ("ids", "exact", "normalized")},
                         {"ids": 0, "exact": 0, "normalized": 1})
        self.assertEqual(result["by_locale"]["us"]["normalized"], 1)

    def test_query_id_crossing_train_test_is_reported(self):
        self.connection.execute("UPDATE examples SET query_id = 10 WHERE example_id = 2")
        result = audit(self.connection)
        self.assertEqual(result["subsets"]["ranking"]["train_test_overlap"]["ids"], 1)
        self.assertEqual(result["integrity"]["query_ids_with_multiple_texts"], 1)

    def test_duplicate_metadata_and_conflicting_judgments_are_visible(self):
        self.connection.execute("""
            INSERT INTO products_raw SELECT * FROM products_raw WHERE product_id = 'A';
            INSERT INTO examples VALUES (3, 'Running shoes', 10, 'A', 'us', 'I', 1, 1, 'train');
        """)
        result = audit(self.connection)["integrity"]
        self.assertEqual(result["duplicate_product_keys"], 1)
        self.assertEqual(result["duplicate_query_product_keys"], 1)
        self.assertEqual(result["conflicting_pair_labels"], 1)

    def test_missing_required_schema_is_rejected(self):
        self.connection.execute("ALTER TABLE examples DROP COLUMN split")
        with self.assertRaisesRegex(ValueError, "Missing required columns.*split"):
            audit(self.connection)


class DownloadTests(unittest.TestCase):
    def test_corruption_cannot_replace_a_verified_dataset(self):
        original = b"verified data"
        corrupt = b"corrupt! data"
        spec = {"name": "fixture.parquet", "bytes": len(original),
                "sha256": hashlib.sha256(original).hexdigest(), "url": "https://example.invalid/file"}
        self.assertEqual(len(original), len(corrupt))
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            with patch("download_esci.urllib.request.urlopen", return_value=io.BytesIO(corrupt)):
                with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                    download_file(spec, data_dir)
            self.assertFalse((data_dir / spec["name"]).exists())
            self.assertFalse(list(data_dir.glob("*.partial")))

    def test_size_and_git_blob_hash_are_verified(self):
        content = b"query_id,source\n1,fixture\n"
        spec = {"name": "sources.csv", "bytes": len(content),
                "git_blob_sha1": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / spec["name"]
            path.write_bytes(content)
            self.assertEqual(verify_file(path, spec)["sha256"], hashlib.sha256(content).hexdigest())
            path.write_bytes(content[:-1])
            with self.assertRaisesRegex(ValueError, "Size mismatch"):
                verify_file(path, spec)


if __name__ == "__main__":
    unittest.main()
