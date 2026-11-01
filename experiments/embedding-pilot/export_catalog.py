"""Stream every pinned product title into verified, restartable vector/ID shards."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import resource
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import duckdb
import numpy as np
from catalog_embedding_store import CatalogExport
from download_esci import verify_file
from embedding_checkpoint import atomic_json, fingerprint
from prepare_esci_splits import file_digest

CONFIG = ROOT / "config/catalog-embeddings-v1.json"
PILOT_POLICY = ROOT / "config/embedding-pilot-v1.json"
MODEL_DIR = ROOT / "models/multilingual-e5-small-614241f"
SOURCE_PATHS = ("config/catalog-embeddings-v1.json", "docs/decisions/0005-full-catalog-embeddings.md",
                "experiments/embedding-pilot/export_catalog.py", "scripts/catalog_embedding_store.py",
                "tests/test_catalog_embedding_store.py", "experiments/embedding-pilot/tests/test_catalog_export.py")


def catalog_batches(connection, catalog: list[dict], shard_rows: int):
    for locale in sorted(row["locale"] for row in catalog):
        cursor = connection.execute("""SELECT product_locale, product_id, product_title FROM products
            WHERE product_locale = ? ORDER BY product_id""", [locale])
        while batch := cursor.fetchmany(shard_rows):
            yield [dict(zip(("locale", "product_id", "title"), row)) for row in batch]


def load_reference(pilot_report: dict, pilot_dir: Path) -> dict:
    for artifact in pilot_report["artifacts"]:
        if artifact["name"] in ("sample.jsonl", "clean/checkpoint.json"):
            verify_file(pilot_dir / artifact["name"], artifact)
    samples = [json.loads(line) for line in (pilot_dir / "sample.jsonl").read_text().splitlines()]
    checkpoint = json.loads((pilot_dir / "clean/checkpoint.json").read_text())
    reference, offset = {}, 0
    for entry in checkpoint["shards"]:
        path = pilot_dir / "clean" / f"shard-{entry['index']:05d}.f32"
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError("Pilot reference checksum mismatch")
        vectors = np.frombuffer(payload, dtype="<f4").reshape(entry["rows"], 384).copy()
        chunk = samples[offset:offset + entry["rows"]]
        if fingerprint(chunk) != entry["input_sha256"]:
            raise ValueError("Pilot reference identity mismatch")
        for row, vector in zip(chunk, vectors, strict=True):
            key = (row["locale"], row["product_id"])
            if key in reference:
                raise ValueError("Duplicate pilot reference key")
            reference[key] = vector
        offset += len(chunk)
    if offset != 3072 or len(reference) != 3072:
        raise ValueError("Incomplete pilot reference")
    return reference


class ReferenceComparison:
    def __init__(self, reference: dict):
        self.reference, self.seen = reference, set()
        self.max_absolute_difference, self.minimum_cosine = 0.0, 1.0

    def check(self, rows: list[dict], vectors: np.ndarray):
        positions, targets = [], []
        for index, row in enumerate(rows):
            key = (row["locale"], row["product_id"])
            if key in self.reference:
                if key in self.seen:
                    raise ValueError("Reference identity compared twice")
                self.seen.add(key)
                positions.append(index)
                targets.append(self.reference[key])
        if targets:
            actual, expected = vectors[positions].astype(np.float64), np.stack(targets).astype(np.float64)
            self.max_absolute_difference = max(self.max_absolute_difference, float(np.max(np.abs(actual - expected))))
            cosines = np.sum(actual * expected, axis=1) / (np.linalg.norm(actual, axis=1) * np.linalg.norm(expected, axis=1))
            if not np.isfinite(cosines).all():
                raise ValueError("Invalid pilot reference cosine")
            self.minimum_cosine = min(self.minimum_cosine, float(cosines.min()))

    def result(self):
        if (len(self.seen) != len(self.reference) or self.max_absolute_difference > 1e-5
                or self.minimum_cosine < .99999):
            raise ValueError("Pilot/full-catalog reference agreement failed")
        return {"identities_compared": len(self.seen), "max_absolute_difference": self.max_absolute_difference,
                "minimum_cosine": self.minimum_cosine}


def export(directory: Path, report_path: Path, stop_after: int = 0,
           pilot_path: Path = ROOT / "reports/embedding-pilot-v1.json",
           pilot_dir: Path = ROOT / "data/processed/embedding-pilot-v1") -> int:
    started = time.perf_counter()
    from encoder import E5Encoder
    import torch
    import transformers

    config, policy = json.loads(CONFIG.read_text()), json.loads(PILOT_POLICY.read_text())
    if config["shard_rows"] != 4096 or file_digest(PILOT_POLICY) != config["encoding_policy_sha256"]:
        raise ValueError("Only the predeclared export/encoding configuration is supported")
    pilot = json.loads(pilot_path.read_text())
    if pilot["config"] != policy:
        raise ValueError("Pilot reference encoding policy differs")
    for group in ("source_sha256", "frozen_source_sha256"):
        for name, sha in pilot[group].items():
            if file_digest(ROOT / name) != sha:
                raise ValueError(f"Frozen pilot/baseline source changed: {name}")
    raw = ROOT / "data/raw/esci/shopping_queries_dataset_products.parquet"
    source = json.loads((ROOT / "config/esci-source.json").read_text())
    product_spec = next(item for item in source["files"] if item["name"].endswith("products.parquet"))
    if verify_file(raw, product_spec) != pilot["sample"]["products_input"]:
        raise ValueError("Pilot metadata differs from pinned ESCI source")
    model_source = json.loads((ROOT / config["model_source"]).read_text())
    model_files = [verify_file(MODEL_DIR / item["name"], item) for item in model_source["files"]]
    if model_files != pilot["model_files"]:
        raise ValueError("Model files differ from pilot")
    reference = ReferenceComparison(load_reference(pilot, pilot_dir))
    runtime = {"python": platform.python_version(), "torch": torch.__version__, "transformers": transformers.__version__,
               "numpy": np.__version__, "duckdb": duckdb.__version__, "platform": platform.platform(),
               "device": "mps" if torch.backends.mps.is_available() else "cpu"}
    sources = {name: file_digest(ROOT / name) for name in SOURCE_PATHS}
    catalog = pilot["sample"]["catalog"]
    contract = {"config": config, "encoding_policy": policy, "dimensions": 384, "shard_rows": config["shard_rows"],
                "catalog": catalog, "products_input": pilot["sample"]["products_input"], "model_files": model_files,
                "source_sha256": sources, "pilot_source_sha256": pilot["source_sha256"],
                "frozen_source_sha256": pilot["frozen_source_sha256"], "runtime": runtime,
                "pilot_report_sha256": file_digest(pilot_path), "dtype": "little-endian float32, row-major"}
    directory.parent.mkdir(parents=True, exist_ok=True)
    if not directory.exists() and shutil.disk_usage(directory.parent).free < sum(r["products"] for r in catalog) * (384 * 4 + 128) + 2**30:
        raise ValueError("Insufficient free space for vectors, ID maps and temporary output")
    encoder = None
    warmed, load_seconds, warmup_seconds = set(), 0.0, 0.0

    def encode(rows):
        nonlocal encoder, load_seconds, warmup_seconds
        if encoder is None:
            load_started = time.perf_counter()
            encoder = E5Encoder(MODEL_DIR, policy, runtime["device"])
            load_seconds += time.perf_counter() - load_started
        locale = rows[0]["locale"]
        titles = [row["title"] for row in rows]
        if locale not in warmed:
            warm_started = time.perf_counter()
            encoder.encode(titles[:policy["batch_size"]])
            warmup_seconds += time.perf_counter() - warm_started
            warmed.add(locale)
        lengths = encoder.lengths(titles)
        arrays, seconds = [], 0.0
        for start in range(0, len(titles), policy["batch_size"]):
            tick = time.perf_counter()
            arrays.append(encoder.encode(titles[start:start + policy["batch_size"]]))
            seconds += time.perf_counter() - tick
        matrix = np.concatenate(arrays)
        norms = np.linalg.norm(matrix, axis=1)
        return matrix.astype("<f4", copy=False).tobytes(), {
            "encode_seconds": seconds, "truncated_rows": sum(length > policy["max_length"] for length in lengths),
            "untruncated_tokens_sum": sum(lengths), "untruncated_tokens_max": max(lengths),
            "norm_min": float(norms.min()), "norm_max": float(norms.max())}

    with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection, CatalogExport(directory, contract) as store:
        connection.read_parquet(str(raw)).create_view("products")
        counts = connection.execute("""SELECT product_locale, count(*), count(DISTINCT product_id),
            count(*) FILTER (WHERE product_id IS NULL OR product_title IS NULL)
            FROM products GROUP BY product_locale ORDER BY product_locale""").fetchall()
        if ([{"locale": row[0], "products": row[1]} for row in counts] != catalog
                or any(row[1] != row[2] or row[3] for row in counts)):
            raise ValueError("Catalog coverage/identity differs from pilot source")
        stopped = False
        for rows in catalog_batches(connection, catalog, config["shard_rows"]):
            path, entry, reused = store.consume(rows, encode)
            reference.check(rows, np.fromfile(path, dtype="<f4").reshape(len(rows), 384))
            done = sum(store.counts.values())
            print(f"{'Verified' if reused else 'Encoded'} shard {len(store.entries)}: {done:,}/1,814,924 titles ({rows[0]['locale']})", flush=True)
            if stop_after and store.new_shards == stop_after:
                stopped = True
                break
        if store.new_shards:
            existing = list(directory.glob("session-*.json"))
            number = max((int(path.stem.split("-")[1]) for path in existing), default=-1) + 1
            session = {"status": "controlled_stop" if stopped else "finished", "new_shards": store.new_shards,
                       "reused_shards": store.reused_shards,
                       "new_rows": store.new_rows,
                       "main_wall_seconds": time.perf_counter() - started, "model_load_seconds": load_seconds,
                       "warmup_seconds": warmup_seconds, "shard_io_validation_seconds": store.io_validation_seconds,
                       "process_max_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)),
                       "observed_mps_tensor_bytes_after_forward": encoder.observed_mps_tensor_bytes,
                       "observed_mps_driver_bytes_after_forward": encoder.observed_mps_driver_bytes}
            atomic_json(directory / f"session-{number:04d}.json", session)
        if stopped:
            print("Controlled stop; committed shards can be resumed with the same command", flush=True)
            return 75
        completion = store.completion()
        if completion["vector_bytes"] != 1_814_924 * 384 * 4:
            raise ValueError("Full-catalog vector byte count mismatch")
        agreement = reference.result()
        manifest = {"contract_fingerprint": fingerprint(contract), "completion": completion,
                    "pilot_reference_comparison": agreement, "shards": store.entries}
        manifest_path = directory / "export-manifest.json"
        if manifest_path.exists():
            if json.loads(manifest_path.read_text()) != manifest:
                raise ValueError("Completed export manifest differs on replay")
        else:
            atomic_json(manifest_path, manifest)
        sessions = [json.loads(path.read_text()) for path in sorted(directory.glob("session-*.json"))]
        per_locale = {}
        for locale in ("es", "jp", "us"):
            entries = [entry for entry in store.entries if entry["locale"] == locale]
            total = sum(entry["rows"] for entry in entries)
            seconds = sum(entry["metrics"]["encode_seconds"] for entry in entries)
            per_locale[locale] = {"titles": total, "shards": len(entries), "encode_seconds": seconds,
                                  "titles_per_encode_second": total / seconds,
                                  "truncated_titles": sum(e["metrics"]["truncated_rows"] for e in entries),
                                  "mean_untruncated_tokens": sum(e["metrics"]["untruncated_tokens_sum"] for e in entries) / total,
                                  "max_untruncated_tokens": max(e["metrics"]["untruncated_tokens_max"] for e in entries),
                                  "norm_min": min(e["metrics"]["norm_min"] for e in entries),
                                  "norm_max": max(e["metrics"]["norm_max"] for e in entries)}
        files = [p for p in directory.rglob("*") if p.is_file()]
        report = {"config": config, "official_test_read": False, "relevance_evaluated": False,
                  "completion": completion, "by_locale": per_locale, "pilot_reference_comparison": agreement,
                  "execution": {"runtime": runtime, "sessions": sessions,
                                "sum_recorded_session_wall_seconds": sum(s["main_wall_seconds"] for s in sessions),
                                "all_rows_attributed_to_recorded_sessions": sum(s["new_rows"] for s in sessions) == 1_814_924,
                                "max_process_rss_bytes": max(s["process_max_rss_bytes"] for s in sessions),
                                "max_observed_mps_tensor_bytes": max(s["observed_mps_tensor_bytes_after_forward"] for s in sessions),
                                "max_observed_mps_driver_bytes": max(s["observed_mps_driver_bytes_after_forward"] for s in sessions),
                                "note": "Wall time includes source/model/reference checks, metadata scan/sort, encoding, validation, persistence and per-session setup; excludes between-invocation downtime, interpreter bootstrap and final report serialization. MPS observations are not true GPU peaks or additive to RSS; RSS is process, not whole host."},
                  "storage": {"logical_export_file_bytes": sum(p.stat().st_size for p in files),
                              "reported_file_block_bytes": sum(p.stat().st_blocks * 512 for p in files),
                              "note": "File bytes/blocks exclude directory metadata, original catalog, model cache and public report; not a measurement of whole-filesystem physical flash use."},
                  "provenance": {"contract_sha256": file_digest(directory / "contract.json"),
                                 "export_manifest_sha256": file_digest(manifest_path),
                                 "source_sha256": sources, "pilot_report_sha256": file_digest(pilot_path)},
                  "limits": "One full-catalog title export with fixed 128-token truncation; no ANN index, retrieval relevance, fusion, training or serving capacity measured. Full-run restart was a controlled stop; prior SIGKILL evidence belongs to the pilot."}
        if report_path.exists():
            if json.loads(report_path.read_text()) != report:
                raise ValueError("Existing report differs; preserve original measurements")
        else:
            atomic_json(report_path, report)
        print(json.dumps({"completion": completion, "pilot_agreement": agreement,
                          "session_wall_seconds": report["execution"]["sum_recorded_session_wall_seconds"]}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/catalog-embeddings-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/catalog-embeddings-v1.json")
    parser.add_argument("--stop-after-shards", type=int, default=0)
    parser.add_argument("--pilot-report", type=Path, default=ROOT / "reports/embedding-pilot-v1.json")
    parser.add_argument("--pilot-dir", type=Path, default=ROOT / "data/processed/embedding-pilot-v1")
    args = parser.parse_args()
    sys.exit(export(args.output_dir, args.report, args.stop_after_shards, args.pilot_report, args.pilot_dir))
