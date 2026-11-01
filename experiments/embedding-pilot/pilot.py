"""Prepare, encode and restart one fixed multilingual resource pilot. No relevance labels."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import resource
import signal
from statistics import fmean
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import duckdb
from download_esci import download_file, verify_file
from embedding_checkpoint import Checkpoint, atomic_json, fingerprint
from prepare_esci_splits import file_digest

POLICY = ROOT / "config/embedding-pilot-v1.json"
MODEL_SOURCE = ROOT / "config/multilingual-e5-small-source.json"
MODEL_DIR = ROOT / "models/multilingual-e5-small-614241f"
SOURCE_PATHS = (
    "config/embedding-pilot-v1.json", "config/multilingual-e5-small-source.json",
    "docs/decisions/0004-embedding-pilot.md", "experiments/embedding-pilot/pyproject.toml",
    "experiments/embedding-pilot/uv.lock", "experiments/embedding-pilot/encoder.py",
    "experiments/embedding-pilot/pilot.py", "scripts/embedding_checkpoint.py", "scripts/download_esci.py",
    "tests/test_embedding_checkpoint.py", "experiments/embedding-pilot/tests/test_encoder.py",
)


def settings():
    policy = json.loads(POLICY.read_text())
    if tuple(policy[key] for key in ("sample_per_locale", "batch_size", "max_length", "checkpoint_rows", "embedding_dimensions")) != (1024, 32, 128, 256, 384):
        raise ValueError("Only the predeclared pilot is supported")
    return policy, json.loads(MODEL_SOURCE.read_text())


def source_hashes():
    return {name: file_digest(ROOT / name) for name in SOURCE_PATHS}


def prepare(directory: Path, policy: dict, model_source: dict) -> dict:
    if directory.exists():
        raise FileExistsError("Use a fresh --output-dir; worker restarts are handled inside a prepared run")
    frozen = json.loads((ROOT / "reports/bm25-title-v1.json").read_text())
    for name, sha in frozen["provenance"]["source_sha256"].items():
        if file_digest(ROOT / name) != sha:
            raise ValueError(f"Frozen baseline changed: {name}")
    raw = ROOT / "data/raw/esci/shopping_queries_dataset_products.parquet"
    source = json.loads((ROOT / "config/esci-source.json").read_text())
    product_spec = next(item for item in source["files"] if item["name"].endswith("products.parquet"))
    verified = verify_file(raw, product_spec)
    with duckdb.connect(config={"threads": 1, "memory_limit": "4GB"}) as connection:
        connection.read_parquet(str(raw)).create_view("products")
        catalog = [dict(zip(("locale", "products"), row)) for row in connection.execute(
            "SELECT product_locale, count(*) FROM products GROUP BY product_locale ORDER BY product_locale").fetchall()]
        rows = connection.execute("""WITH hashed AS (
            SELECT product_locale, product_id, product_title,
                sha256(? || chr(9) || product_locale || chr(9) || product_id) AS sample_hash FROM products)
            SELECT * FROM hashed QUALIFY row_number() OVER (
                PARTITION BY product_locale ORDER BY sample_hash, product_id) <= ?
            ORDER BY product_locale, sample_hash, product_id""", [policy["sample_seed"], policy["sample_per_locale"]]).fetchall()
    samples = [dict(zip(("locale", "product_id", "title", "sample_hash"), row)) for row in rows]
    if ({row["locale"] for row in samples} != {"es", "jp", "us"}
            or len({(row["locale"], row["product_id"]) for row in samples}) != 3072
            or any(row["title"] is None for row in samples)
            or any(sum(row["locale"] == locale for row in samples) != 1024 for locale in ("es", "jp", "us"))):
        raise ValueError("Sample identity or locale coverage mismatch")
    directory.mkdir(parents=True)
    with (directory / "sample.jsonl").open("w", encoding="utf-8") as handle:
        for row in samples:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {"policy_sha256": file_digest(POLICY), "products_input": verified, "catalog": catalog,
                "sample_rows": len(samples), "sample_sha256": file_digest(directory / "sample.jsonl")}
    atomic_json(directory / "sample-manifest.json", manifest)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    required_bytes = sum(item["bytes"] for item in model_source["files"] if not (MODEL_DIR / item["name"]).exists())
    started = time.perf_counter()
    model_files = [download_file(item, MODEL_DIR) for item in model_source["files"]]
    return {"sample": manifest, "model_files": model_files, "download_or_verify_seconds": time.perf_counter() - started,
            "uncached_model_file_bytes_before_download": required_bytes,
            "frozen_source_sha256": frozen["provenance"]["source_sha256"]}


def worker(directory: Path, run_name: str, pause_after: int) -> None:
    from encoder import E5Encoder
    import numpy as np
    import torch
    import transformers

    policy, model_source = settings()
    sample_manifest = json.loads((directory / "sample-manifest.json").read_text())
    if sample_manifest["policy_sha256"] != file_digest(POLICY) or sample_manifest["sample_sha256"] != file_digest(directory / "sample.jsonl"):
        raise ValueError("Prepared sample or policy changed")
    samples = [json.loads(line) for line in (directory / "sample.jsonl").read_text().splitlines()]
    chunks = [samples[start:start + policy["checkpoint_rows"]] for start in range(0, len(samples), policy["checkpoint_rows"])]
    if len(samples) != 3072 or any(len({row["locale"] for row in chunk}) != 1 for chunk in chunks):
        raise ValueError("Invalid locale shard boundaries")
    model_files = [verify_file(MODEL_DIR / item["name"], item) for item in model_source["files"]]
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    runtime = {"python": platform.python_version(), "torch": torch.__version__, "transformers": transformers.__version__,
               "numpy": np.__version__, "duckdb": duckdb.__version__, "platform": platform.platform(),
               "device": device, "mps_built": torch.backends.mps.is_built(), "mps_available": torch.backends.mps.is_available()}
    contract = {"dimensions": policy["embedding_dimensions"], "dtype": "little-endian float32, row-major",
                "policy": policy, "sample_sha256": sample_manifest["sample_sha256"], "model_files": model_files,
                "source_sha256": source_hashes(), "runtime": runtime}
    run_dir = directory / run_name
    started = time.perf_counter()
    with Checkpoint(run_dir, contract, chunks) as checkpoint:
        initial = len(checkpoint.shards)
        load_started = time.perf_counter()
        encoder = E5Encoder(MODEL_DIR, policy, device)
        load_seconds = time.perf_counter() - load_started
        warmed, warmup_seconds = set(), 0.0
        for chunk in chunks[initial:]:
            locale = chunk[0]["locale"]
            titles = [row["title"] for row in chunk]
            if locale not in warmed:
                warm_started = time.perf_counter()
                encoder.encode(titles[:policy["batch_size"]])
                warmup_seconds += time.perf_counter() - warm_started
                warmed.add(locale)
            lengths = encoder.lengths(titles)
            vectors, batch_seconds = [], []
            for start in range(0, len(titles), policy["batch_size"]):
                batch_started = time.perf_counter()
                vectors.append(encoder.encode(titles[start:start + policy["batch_size"]]))
                batch_seconds.append(time.perf_counter() - batch_started)
            matrix = np.concatenate(vectors)
            norms = np.linalg.norm(matrix, axis=1)
            metadata = {"locale": locale, "encode_seconds": sum(batch_seconds), "batch_seconds": batch_seconds,
                        "truncated_rows": sum(length > policy["max_length"] for length in lengths),
                        "untruncated_tokens_min": min(lengths), "untruncated_tokens_max": max(lengths),
                        "untruncated_tokens_mean": fmean(lengths), "norm_min": float(norms.min()), "norm_max": float(norms.max())}
            checkpoint.commit(matrix.astype("<f4", copy=False).tobytes(), metadata)
            print(f"Committed {run_name} shard {len(checkpoint.shards)}/{len(chunks)} ({locale})", flush=True)
            if pause_after and len(checkpoint.shards) == pause_after:
                print("INTERRUPT_READY", flush=True)
                signal.pause()  # The parent kills only this worker, then tests restart of committed work.
        stats = {"runtime": runtime, "initial_committed_shards": initial, "new_committed_shards": len(checkpoint.shards) - initial,
                 "model_load_seconds": load_seconds, "warmup_seconds": warmup_seconds,
                 "worker_main_wall_seconds": time.perf_counter() - started,
                 "process_max_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)),
                 "observed_mps_tensor_bytes_after_forward": encoder.observed_mps_tensor_bytes,
                 "observed_mps_driver_bytes_after_forward": encoder.observed_mps_driver_bytes,
                 "memory_note": "Kernel max RSS is worker-process memory; MPS observations are not true GPU peaks, not total host peak, and not additive to RSS on unified memory."}
        atomic_json(run_dir / "session.json", stats)


def run_worker(directory: Path, name: str, pause_after: int = 0) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "worker", "--output-dir", str(directory), "--run-name", name]
    if pause_after:
        command += ["--pause-after-shards", str(pause_after)]
    started = time.perf_counter()
    killed = False
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            if line.strip() == "INTERRUPT_READY":
                if not pause_after:
                    raise ValueError("Unexpected worker pause")
                process.kill()
                killed = True
        code = process.wait()
    if (pause_after and (not killed or code != -signal.SIGKILL)) or (not pause_after and code != 0):
        raise RuntimeError(f"Worker {name} failed, return code {code}")
    return {"exit_code": code, "forced_sigkill": killed, "subprocess_wall_seconds": time.perf_counter() - started}


def run(directory: Path, report_path: Path) -> None:
    import numpy as np

    policy, model_source = settings()
    preparation = prepare(directory, policy, model_source)
    clean_process = run_worker(directory, "clean")
    interruption = run_worker(directory, "resumed", pause_after=2)
    before = json.loads((directory / "resumed/checkpoint.json").read_text())["shards"]
    if len(before) != 2:
        raise ValueError("Interruption did not leave exactly two committed shards")
    resumed_process = run_worker(directory, "resumed")
    clean = json.loads((directory / "clean/checkpoint.json").read_text())
    resumed = json.loads((directory / "resumed/checkpoint.json").read_text())
    if clean["contract"] != resumed["contract"] or resumed["shards"][:2] != before or len(resumed["shards"]) != 12:
        raise ValueError("Restart changed the contract or committed work")
    max_difference, min_cosine = 0.0, 1.0
    for old, new in zip(clean["shards"], resumed["shards"], strict=True):
        if old["input_sha256"] != new["input_sha256"]:
            raise ValueError("Resumed vector identities differ")
        a = np.fromfile(directory / "clean" / f"shard-{old['index']:05d}.f32", dtype="<f4").reshape(-1, 384).astype(np.float64)
        b = np.fromfile(directory / "resumed" / f"shard-{new['index']:05d}.f32", dtype="<f4").reshape(-1, 384).astype(np.float64)
        max_difference = max(max_difference, float(np.max(np.abs(a - b))))
        cosines = np.sum(a * b, axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
        min_cosine = min(min_cosine, float(cosines.min()))
    tolerance = policy["numeric_tolerance"]
    if max_difference > tolerance["repeat_max_absolute_difference"] or min_cosine < tolerance["repeat_min_cosine"]:
        raise ValueError("Clean/resumed vectors exceed declared numerical tolerance")
    per_locale = {}
    projection = {"point_seconds": 0.0, "observed_shard_rate_bounds_seconds": [0.0, 0.0]}
    counts = {row["locale"]: row["products"] for row in preparation["sample"]["catalog"]}
    for locale in ("es", "jp", "us"):
        shards = [entry for entry in clean["shards"] if entry["metrics"]["locale"] == locale]
        rows = sum(entry["rows"] for entry in shards)
        seconds = sum(entry["metrics"]["encode_seconds"] for entry in shards)
        rates = [entry["metrics"]["encode_seconds"] / entry["rows"] for entry in shards]
        per_locale[locale] = {"titles": rows, "encode_seconds": seconds, "titles_per_second": rows / seconds,
                              "truncated_titles": sum(entry["metrics"]["truncated_rows"] for entry in shards),
                              "untruncated_tokens_min": min(entry["metrics"]["untruncated_tokens_min"] for entry in shards),
                              "untruncated_tokens_max": max(entry["metrics"]["untruncated_tokens_max"] for entry in shards),
                              "untruncated_tokens_mean": fmean(entry["metrics"]["untruncated_tokens_mean"] for entry in shards),
                              "raw_vector_bytes": sum(entry["bytes"] for entry in shards)}
        projection["point_seconds"] += counts[locale] * seconds / rows
        projection["observed_shard_rate_bounds_seconds"][0] += counts[locale] * min(rates)
        projection["observed_shard_rate_bounds_seconds"][1] += counts[locale] * max(rates)
    projection["raw_full_catalog_vector_bytes"] = sum(counts.values()) * policy["embedding_dimensions"] * 4
    projection["note"] = policy["projection"]
    clean_session = json.loads((directory / "clean/session.json").read_text())
    resumed_session = json.loads((directory / "resumed/session.json").read_text())
    report = {"config": policy, "official_test_read": False, "relevance_evaluated": False,
              "sample": preparation["sample"], "model_source": model_source, "model_files": preparation["model_files"],
              "preparation": {key: preparation[key] for key in ("download_or_verify_seconds", "uncached_model_file_bytes_before_download")},
              "by_locale": per_locale, "clean_session": clean_session, "resume_session": resumed_session,
              "worker_processes": {"clean": clean_process, "interrupted": interruption, "resumed": resumed_process},
              "recovery_checks": {"committed_shards_preserved": 2, "committed_titles_preserved": 512,
                                  "completed_shards_after_restart": 12, "all_3072_vectors_compared": True,
                                  "max_absolute_difference": max_difference, "minimum_cosine": min_cosine,
                                  "all_payload_hashes_equal": [entry["sha256"] for entry in clean["shards"]] == [entry["sha256"] for entry in resumed["shards"]]},
              "full_catalog_compute_projection": projection,
              "source_sha256": source_hashes(), "frozen_source_sha256": preparation["frozen_source_sha256"],
              "artifacts": [{"name": name, "bytes": (directory / name).stat().st_size, "sha256": file_digest(directory / name)}
                            for name in ("sample.jsonl", "sample-manifest.json", "clean/checkpoint.json", "clean/session.json", "resumed/checkpoint.json", "resumed/session.json")],
              "limits": "Small equal-locale sample; compute projection is locale-weighted and excludes startup/download/indexing. No full-catalog embedding, ANN overhead, retrieval gain, fine-tuning or serving capacity measured. Restart was SIGKILL at a committed-shard boundary, not a power-loss or mid-kernel recovery guarantee."}
    atomic_json(report_path, report)
    atomic_json(directory / "manifest.json", report)
    print(json.dumps({"by_locale": per_locale, "clean_session": clean_session, "projection": projection,
                      "recovery": report["recovery_checks"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "worker"), default="run", nargs="?")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/embedding-pilot-v1")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/embedding-pilot-v1.json")
    parser.add_argument("--run-name", choices=("clean", "resumed"), default="clean")
    parser.add_argument("--pause-after-shards", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "worker":
        worker(args.output_dir, args.run_name, args.pause_after_shards)
    else:
        run(args.output_dir, args.report)
