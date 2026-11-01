from array import array
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from catalog_embedding_store import CatalogExport


def row(locale, product):
    return {"locale": locale, "product_id": product, "title": "shoe"}


def encode(rows):
    values = array("f", [1, 0] * len(rows))
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes(), {"encode_seconds": .1}


class CatalogEmbeddingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.contract = {"dimensions": 2, "shard_rows": 2,
                         "catalog": [{"locale": "es", "products": 3}, {"locale": "jp", "products": 1}]}
        self.chunks = [[row("es", "001"), row("es", "002")], [row("es", "003")], [row("jp", "001")]]

    def test_stream_resume_identity_map_and_completed_replay(self):
        with CatalogExport(self.root, self.contract) as export:
            export.consume(self.chunks[0], encode)
            with self.assertRaises(ValueError):
                export.completion()
        with CatalogExport(self.root, self.contract) as export:
            _, _, reused = export.consume(self.chunks[0], lambda _: self.fail("Committed rows re-encoded"))
            self.assertTrue(reused)
            for chunk in self.chunks[1:]:
                export.consume(chunk, encode)
            complete = export.completion()
            self.assertEqual(complete["vector_bytes"], 32)
            self.assertEqual(export.new_shards, 2)
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with CatalogExport(self.root, self.contract) as export:
            for chunk in self.chunks:
                export.consume(chunk, lambda _: self.fail("Completed export re-encoded"))
            self.assertEqual(export.completion(), complete)
            self.assertEqual(export.reused_shards, 3)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        ids = [json.loads(line) for line in (self.root / "shards/00000/ids.jsonl").read_text().splitlines()]
        self.assertEqual(ids[0], {"locale": "es", "product_id": "001"})

    def test_corrupt_or_reordered_id_map_fails(self):
        with CatalogExport(self.root, self.contract) as export:
            export.consume(self.chunks[0], encode)
        path = self.root / "shards/00000/ids.jsonl"
        path.write_bytes(b"".join(reversed(path.read_bytes().splitlines(keepends=True))))
        with CatalogExport(self.root, self.contract) as export:
            with self.assertRaisesRegex(ValueError, "ID map"):
                export.consume(self.chunks[0], encode)

    def test_out_of_order_duplicate_mixed_and_nonfinal_short_shards_fail(self):
        bad = [[row("es", "002"), row("es", "001")], [row("es", "001"), row("es", "001")],
               [row("es", "001"), row("jp", "001")]]
        for index, chunk in enumerate(bad):
            with CatalogExport(self.root / str(index), self.contract) as export:
                with self.assertRaises(ValueError):
                    export.consume(chunk, encode)
        with CatalogExport(self.root / "short", self.contract) as export:
            export.consume([row("es", "001")], encode)
            with self.assertRaises(ValueError):
                export.consume([row("es", "002")], encode)

    def test_changed_contract_and_concurrent_writer_fail(self):
        with CatalogExport(self.root, self.contract):
            with self.assertRaises(BlockingIOError):
                CatalogExport(self.root, self.contract)
        with self.assertRaises(ValueError):
            CatalogExport(self.root, dict(self.contract, dimensions=3))


if __name__ == "__main__":
    unittest.main()
