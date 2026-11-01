"""Single-writer, checksummed float32 shards for restartable local embedding jobs."""

from array import array
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def validate_vectors(payload: bytes, rows: int, dimensions: int, tolerance: float = 1e-5) -> None:
    if rows < 1 or dimensions < 1 or len(payload) != rows * dimensions * 4:
        raise ValueError("Embedding shape/byte count mismatch")
    values = array("f")
    values.frombytes(payload)
    if sys.byteorder != "little":
        values.byteswap()
    for start in range(0, len(values), dimensions):
        vector = values[start:start + dimensions]
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("Nonfinite embedding")
        if abs(math.sqrt(sum(value * value for value in vector)) - 1) > tolerance:
            raise ValueError("Embedding is not unit normalized")


class Checkpoint:
    """Manifest replacement commits a shard; uncommitted orphan payloads are recomputed."""

    def __init__(self, directory: Path, contract: dict, chunks: list[list[dict]]):
        self.directory, self.contract, self.chunks = directory, contract, chunks
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = (directory / ".writer.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.path = directory / "checkpoint.json"
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                if self.state["contract"] != contract or self.state["fingerprint"] != fingerprint(contract):
                    raise ValueError("Checkpoint contract changed; use a fresh run directory")
            else:
                if list(directory.glob("shard-*.f32")):
                    raise ValueError("Payloads exist without a run contract")
                self.state = {"contract": contract, "fingerprint": fingerprint(contract), "shards": []}
                atomic_json(self.path, self.state)
            if len(self.state["shards"]) > len(chunks):
                raise ValueError("Too many committed shards")
            for index, entry in enumerate(self.state["shards"]):
                if (entry["index"] != index or entry["input_sha256"] != fingerprint(chunks[index])
                        or entry["rows"] != len(chunks[index])):
                    raise ValueError("Checkpoint input identity/order changed")
                payload = self.payload_path(index).read_bytes()
                if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                    raise ValueError("Committed embedding checksum mismatch")
                validate_vectors(payload, len(chunks[index]), contract["dimensions"])
        except BaseException:
            self.close()
            raise

    def payload_path(self, index: int) -> Path:
        return self.directory / f"shard-{index:05d}.f32"

    @property
    def shards(self) -> list[dict]:
        return self.state["shards"]

    def commit(self, payload: bytes, metrics: dict) -> dict:
        index = len(self.shards)
        if index >= len(self.chunks):
            raise ValueError("All shards are already committed")
        rows = len(self.chunks[index])
        validate_vectors(payload, rows, self.contract["dimensions"])
        path = self.payload_path(index)
        temporary = path.with_suffix(".f32.tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        entry = {"index": index, "rows": rows, "input_sha256": fingerprint(self.chunks[index]),
                 "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "metrics": metrics}
        # Publish state only after the complete validated payload. Process interruption before
        # this point leaves an orphan at the next index, which commit() safely replaces.
        next_state = dict(self.state, shards=self.shards + [entry])
        atomic_json(self.path, next_state)
        self.state = next_state
        return entry

    def close(self) -> None:
        if not self.lock.closed:
            self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
