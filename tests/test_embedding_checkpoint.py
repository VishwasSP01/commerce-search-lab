from array import array
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from embedding_checkpoint import Checkpoint, validate_vectors


def blob(values):
    result = array("f", values)
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


class EmbeddingCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.contract = {"dimensions": 2, "model": "fixture", "sample": "fixed"}
        self.chunks = [[{"locale": "us", "id": "A"}], [{"locale": "jp", "id": "A"}]]

    def test_resume_preserves_committed_shards_and_identity(self):
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            original = run.commit(blob([1, 0]), {"fixture": True})
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            self.assertEqual(run.shards, [original])
            run.commit(blob([0, 1]), {})
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            self.assertEqual(len(run.shards), 2)
            self.assertEqual(run.shards[0], original)

    def test_changed_contract_or_input_order_is_rejected(self):
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            run.commit(blob([1, 0]), {})
        with self.assertRaises(ValueError):
            Checkpoint(self.root, dict(self.contract, model="changed"), self.chunks)
        with self.assertRaises(ValueError):
            Checkpoint(self.root, self.contract, list(reversed(self.chunks)))

    def test_corrupted_committed_payload_is_rejected(self):
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            run.commit(blob([1, 0]), {})
            path = run.payload_path(0)
        path.write_bytes(blob([0, 1]))
        with self.assertRaisesRegex(ValueError, "checksum"):
            Checkpoint(self.root, self.contract, self.chunks)

    def test_interruption_before_manifest_commit_recomputes_orphan(self):
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            run.commit(blob([1, 0]), {})
            run.payload_path(1).write_bytes(b"incomplete uncommitted output")
            run.payload_path(1).with_suffix(".f32.tmp").write_bytes(b"partial")
        with Checkpoint(self.root, self.contract, self.chunks) as run:
            self.assertEqual(len(run.shards), 1)
            run.commit(blob([0, 1]), {})
            self.assertEqual(run.payload_path(1).read_bytes(), blob([0, 1]))

    def test_concurrent_writer_is_rejected(self):
        with Checkpoint(self.root, self.contract, self.chunks):
            with self.assertRaises(BlockingIOError):
                Checkpoint(self.root, self.contract, self.chunks)

    def test_invalid_shape_nonfinite_and_unnormalized_vectors_fail(self):
        for payload in [b"partial", blob([float("nan"), 0]), blob([2, 0]), blob([0, 0])]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_vectors(payload, 1, 2)


if __name__ == "__main__":
    unittest.main()
