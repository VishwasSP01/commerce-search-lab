"""Bounded, row-aligned catalog shards using the frozen pilot checkpoint protocol."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from embedding_checkpoint import Checkpoint, atomic_json, fingerprint


def identity_bytes(rows: list[dict]) -> bytes:
    return "".join(json.dumps({"locale": row["locale"], "product_id": row["product_id"]},
                              ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows).encode()


class CatalogExport:
    def __init__(self, directory: Path, contract: dict):
        self.directory, self.contract = directory, contract
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = (directory / ".export-writer.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = directory / "contract.json"
            if path.exists():
                if json.loads(path.read_text()) != contract:
                    raise ValueError("Export contract changed; use a fresh output directory")
            else:
                if list((directory / "shards").glob("*")):
                    raise ValueError("Shard output exists without its export contract")
                atomic_json(path, contract)
            self.entries = []
            self.counts = {row["locale"]: 0 for row in contract["catalog"]}
            self.last_key = None
            self.previous_size = 0
            self.new_shards = self.reused_shards = 0
            self.new_rows = 0
            self.io_validation_seconds = 0.0
            self.vector_digest, self.id_digest = hashlib.sha256(), hashlib.sha256()
        except BaseException:
            self.close()
            raise

    def consume(self, rows: list[dict], encode) -> tuple[Path, dict, bool]:
        started, callback_seconds = time.perf_counter(), 0.0
        if not rows or len(rows) > self.contract["shard_rows"] or len({r["locale"] for r in rows}) != 1:
            raise ValueError("Invalid shard size or mixed locales")
        locale = rows[0]["locale"]
        if (self.last_key and self.last_key[0] == locale
                and self.previous_size != self.contract["shard_rows"]):
            raise ValueError("A short shard must be last in its locale")
        for row in rows:
            key = (row["locale"], row["product_id"])
            if (locale not in self.counts or not isinstance(row["title"], str)
                    or not row["product_id"] or (self.last_key is not None and key <= self.last_key)):
                raise ValueError("Invalid or nonincreasing locale-product identity")
            self.last_key = key
        index = len(self.entries)
        shard_dir = self.directory / "shards" / f"{index:05d}"
        child_contract = {"export_fingerprint": fingerprint(self.contract), "shard_index": index,
                          "locale": locale, "dimensions": self.contract["dimensions"]}
        ids = identity_bytes(rows)
        ids_sha = hashlib.sha256(ids).hexdigest()
        with Checkpoint(shard_dir, child_contract, [rows]) as checkpoint:
            reused = bool(checkpoint.shards)
            if reused:
                entry = checkpoint.shards[0]
                if (entry["metrics"]["ids_sha256"] != ids_sha
                        or entry["metrics"]["ids_bytes"] != len(ids)
                        or (shard_dir / "ids.jsonl").read_bytes() != ids):
                    raise ValueError("Committed ID map differs from its vector rows")
                self.reused_shards += 1
            else:
                callback_started = time.perf_counter()
                payload, metrics = encode(rows)
                callback_seconds = time.perf_counter() - callback_started
                temporary = shard_dir / "ids.jsonl.tmp"
                with temporary.open("wb") as handle:
                    handle.write(ids)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(shard_dir / "ids.jsonl")
                entry = checkpoint.commit(payload, dict(metrics, locale=locale, ids_sha256=ids_sha, ids_bytes=len(ids)))
                self.new_shards += 1
                self.new_rows += len(rows)
            payload_path = checkpoint.payload_path(0)
            self.vector_digest.update(payload_path.read_bytes())
            self.id_digest.update(ids)
            self.entries.append({"index": index, "locale": locale, "rows": entry["rows"],
                                 "input_sha256": entry["input_sha256"], "vector_sha256": entry["sha256"],
                                 "vector_bytes": entry["bytes"], "metrics": entry["metrics"],
                                 "checkpoint_sha256": hashlib.sha256(checkpoint.path.read_bytes()).hexdigest()})
        self.counts[locale] += len(rows)
        self.previous_size = len(rows)
        self.io_validation_seconds += time.perf_counter() - started - callback_seconds
        return payload_path, self.entries[-1], reused

    def completion(self) -> dict:
        expected = {row["locale"]: row["products"] for row in self.contract["catalog"]}
        if self.counts != expected:
            raise ValueError("Catalog export is incomplete")
        # Unexpected committed or orphan directories must not hide outside the declared sequence.
        actual = {path.name for path in (self.directory / "shards").iterdir() if path.is_dir()}
        if actual != {f"{index:05d}" for index in range(len(self.entries))}:
            raise ValueError("Unexpected shard directories")
        return {"counts": self.counts, "shards": len(self.entries),
                "vector_bytes": sum(entry["vector_bytes"] for entry in self.entries),
                "ids_bytes": sum(entry["metrics"]["ids_bytes"] for entry in self.entries),
                "vector_sequence_sha256": self.vector_digest.hexdigest(),
                "id_sequence_sha256": self.id_digest.hexdigest()}

    def close(self):
        if not self.lock.closed:
            self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
