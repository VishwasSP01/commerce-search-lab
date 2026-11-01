"""Exercise the shipped Java artifact through the same Python bridge as the real run."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_bm25 import CONFIG, JAR, encode, evaluate, java, validation_groups


@unittest.skipUnless(JAR.is_file() or os.environ.get("REQUIRE_BM25_JAR") == "1",
                     "Build the Java artifact first with ./mvnw verify")
class PackagedBaselineTests(unittest.TestCase):
    def test_packaged_multilingual_ranker_to_python_evaluation(self):
        self.assertTrue(JAR.is_file(), f"Expected packaged Java artifact: {JAR}")
        config = json.loads(CONFIG.read_text())
        examples = {"us": ("shoe", "red shoes"), "es": ("zapato", "zapatos rojos"),
                    "jp": ("シューズ", "赤い\nランニングシューズ")}
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            products, queries, rows = [], [], []
            for query_id, (locale, (query, title)) in enumerate(examples.items(), start=1):
                products.extend([f"{locale}\tA\t{encode('hat')}", f"{locale}\tB\t{encode(title)}"])
                queries.append(f"{locale}\t{query_id}\t{encode(query)}\tA,B")
                for product_id, label in (("A", "I"), ("B", "E")):
                    rows.append({"product_locale": locale, "query_id": query_id, "query": query,
                                 "product_id": product_id, "esci_label": label, "split": "train",
                                 "evaluation_split": "validation"})
            (directory / "products.tsv").write_text("\n".join(products) + "\n", encoding="utf-8")
            (directory / "queries.tsv").write_text("\n".join(queries) + "\n", encoding="utf-8")
            metadata = java("index", str(directory / "products.tsv"), str(directory / "index"), "1.2", "0.75")
            self.assertEqual(metadata, {"products": "6", "lucene": config["lucene_version"]})
            metadata = java("score", str(directory / "index"), str(directory / "queries.tsv"),
                            str(directory / "scores.tsv"), "1.2", "0.75")
            self.assertEqual(metadata["queries"], "3")
            result = evaluate(validation_groups(rows), directory / "scores.tsv", config)
            self.assertEqual(len(result), 3)
            for row in result:
                self.assertEqual(row["bm25"], 1)
                self.assertLess(row["reference"], 1)
