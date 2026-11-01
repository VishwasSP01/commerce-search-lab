# Multilingual embedding pilot: compatibility and resource evidence

**Decision: proceed to a separately reviewed full-catalog embedding export.** The existing Mac successfully encoded 3,072 real product titles with a pinned multilingual E5 model on its MPS GPU. Process termination and restart preserved completed work, and every resumed vector matched a clean run byte-for-byte. This establishes bounded feasibility, not a retrieval-quality improvement or a measured full-catalog run.

The [settings](../config/embedding-pilot-v1.json), [model source manifest](../config/multilingual-e5-small-source.json) and [decision](decisions/0004-embedding-pilot.md) were committed in `4578109` before encoding. The [main report](../reports/embedding-pilot-v1.json) records measurements, versions, hashes and limitations. Bulk samples, weights, vectors and checkpoints remain ignored under `data/` and `models/`.

Subsequent work completed the [full-catalog export](full-catalog-embeddings.md). The estimates and scope below describe this earlier pilot; use the full-export report for actual catalog measurements.

## Fixed workload and model

- Select **1,024 titles per locale**, 3,072 total, by ascending SHA-256 of `seed + TAB + locale + TAB + product_id`, then ID. The seed and ordering are fixed. Selection uses the verified product metadata only; no query or judgment partition is opened.
- Model: `intfloat/multilingual-e5-small`, revision **`614241f622f53c4eeff9890bdc4f31cfecc418b3`**. Its source manifest pins seven files by size and upstream SHA-256 or Git-blob hash. The model card declares an MIT license. Required files total **493,292,184 bytes (470.44 MiB)**.
- Apply NFKC and `passage: ` to titles. The same encoder supports the required `query: ` prefix; CPU fixtures test that path, but this pilot does not evaluate real search queries.
- Use **384 dimensions**, float32, eager attention, mask-aware mean pooling and L2 normalization in eval/inference mode. Batch size is **32**, maximum length **128**, and CPU thread count **4**. The model supports up to 512 tokens; 128 is the explicit pilot title limit.
- Load verified local safetensors with remote code disabled. Inference runs offline. Prefer MPS when available, otherwise record CPU explicitly; unsupported-operator MPS-to-CPU fallback is disabled.

The separate `experiments/embedding-pilot/` environment pins PyTorch 2.9.1, Transformers 4.57.1, NumPy 2.3.4 and the remaining dependencies in its own lockfile. The frozen BM25 environment and source fingerprints remain unchanged. Linux uses a CPU-only PyTorch index; no CUDA dependency stack is installed for CI.

The implementation follows the [E5 pooling/prefix instructions](https://huggingface.co/intfloat/multilingual-e5-small), [PyTorch 2.9 MPS device guidance](https://docs.pytorch.org/docs/2.9/notes/mps.html) and [synchronization/memory APIs](https://docs.pytorch.org/docs/2.9/mps.html).

## Measurements

Hardware/runtime: Apple M5 Pro, 24 GiB unified memory; macOS 27.2 arm64, Python 3.12.16, PyTorch 2.9.1. MPS was built and available. No CPU-versus-GPU speedup comparison is claimed.

The final clean run produced 12 checkpoints of 256 titles. One batch per locale warms the encoder outside stage timing. The table includes all subsequent batches, with no rate-based exclusions.

| Locale | Titles | Encode stage | Titles/s | Mean / max untruncated tokens | Truncated titles |
| --- | ---: | ---: | ---: | --- | ---: |
| Spanish (es) | 1,024 | 1.339 s | 764.5 | 39.36 / 101 | 0 |
| Japanese (jp) | 1,024 | 1.958 s | 522.9 | 42.82 / 138 | 2 |
| English (us) | 1,024 | 1.251 s | 818.6 | 34.87 / 105 | 0 |

Token counts include prefix and special tokens, before truncation. Two Japanese titles exceed 128 tokens: **0.195% of that locale sample**, 0.065% overall. This is observed data loss under the chosen limit, not evidence that truncation is harmless. All produced vectors pass finite-value, shape and unit-norm checks within absolute tolerance 1e-5.

Timed encode stages include normalization/prefixing, tokenization, device transfers, forward pass, pooling, normalization, GPU synchronization and the CPU float32 array. Separate untruncated-length analysis, concatenation, checkpoint validation/fsync, startup, model loading and warmup are outside these stage times. The complete clean worker subprocess took **7.132 s**, including imports and those other stages. Model loading inside its main function took 0.547 s and warmup 0.222 s. These are short batch observations, not query-serving latency or sustained capacity.

| Resource | Final clean worker |
| --- | ---: |
| Kernel-reported maximum process RSS | 1,022,787,584 bytes (975.41 MiB; 0.953 GiB) |
| Largest observed MPS tensor allocation after a forward call | 489,322,496 bytes |
| Largest observed MPS driver allocation after a forward call | 1,126,924,288 bytes (1,074.72 MiB) |
| Raw vectors for 3,072 titles | 4,718,592 bytes (4.50 MiB) |

RSS covers the worker process, not the parent or the entire machine. MPS values are observations after forward calls, not true GPU peaks; short-lived allocations can be missed. They overlap unified-memory accounting and **must not be added to RSS** to claim total memory. No total-host peak, training-memory requirement or ANN-memory requirement is established.

## Full-catalog planning estimate

The corpus contains 260,011 Spanish, 339,059 Japanese and 1,215,854 English locale-product keys. Weight each locale's measured seconds/title by its actual catalog count. Do not extrapolate from an equally weighted mean of the three sample rates.

The final run projects **2,473.77 s, about 41.2 minutes**, for the encode stages alone. Applying observed fastest/slowest shard rates by locale gives **38.3–44.6 minutes**. These are descriptive bounds from four shards per locale, **not a confidence interval or guaranteed deadline**. They exclude download, startup, checkpoint overhead, index construction, prolonged thermal throttling and changed host contention.

Raw full-catalog float32 vectors require **2,787,723,264 bytes (2.596 GiB)** by arithmetic. IDs, manifests, temporary/duplicate output, model files and ANN index overhead are additional. A full-catalog encode and retrieval index have not yet been run.

An earlier clean observation with identical encoder code, sample, model and settings projected **45.1 minutes**, with observed-shard bounds of 39.0–53.2 minutes. Its rates were 520.5 / 481.6 / 808.6 titles/s for es/jp/us. That first observation included colder runtime caches; it preceded a worker bookkeeping fix that does not change encoding. Both observations, their hashes and cache/download context are preserved in the [timing-repeat record](../reports/embedding-pilot-v1-timing-repeat.json). All clean vector payload hashes match across those observations. The difference is a reason to measure the eventual full run rather than advertise the faster estimate as capacity.

The first model-file download/verification took 41.353 s for 493,292,184 initially uncached bytes. The final run reused those files and verified them in 0.305 s. Neither figure includes dependency installation or is part of the compute projection.

## Forced termination, restart and completed replay

Each output run is bound to settings, sampled text/identity, verified model files, encoding/checkpoint source and actual runtime/backend. One writer holds a local OS file lock. A shard is a little-endian, row-major float32 payload with an input hash, byte count and SHA-256. The payload is validated and replaced before atomic manifest replacement commits it. An uncommitted payload at the next position is replaced on restart; corrupted committed data or a changed contract fails instead of silently mixing vectors.

The real recovery experiment:

1. Encode all 3,072 titles cleanly.
2. Start another model worker, commit two shards / 512 titles, pause at that boundary, and send **SIGKILL** to that worker.
3. Start a new worker on the same run directory. It verifies and retains the two committed shards, then encodes only the remaining ten shards / 2,560 titles.
4. Compare all 3,072 vectors against the clean run. Maximum absolute difference is **0.0**, minimum cosine **0.9999999999999998**, and all payload hashes are equal. The numerical acceptance thresholds were fixed at 1e-5 maximum absolute difference and 0.99999 minimum cosine.

A completed-worker replay also preserves the checkpoint, measured session and all 12 payload files: **all 14 hashes remain unchanged**. See the [replay verification](../reports/embedding-pilot-v1-replay.json). The guard prevents an otherwise unnecessary completed replay from replacing the original timing record.

This demonstrates process termination at a committed-shard boundary. Unit fixtures cover orphan/incomplete writes, changed identity/settings, checksum corruption and competing writers. It is not a mid-kernel recovery, disk-power-loss, multi-host exactly-once or hardware-failure guarantee. Cross-device or cross-version byte identity is not promised.

## Reproduce

Prerequisites are the pinned product metadata and the existing `uv` installation. No paid service, GPU rental, API key or new hardware is required. The real model download is about 470 MiB and stays ignored.

```sh
uv sync --locked --project experiments/embedding-pilot
uv run --locked --no-sync --project experiments/embedding-pilot \
  python -m unittest discover -s experiments/embedding-pilot/tests -v
uv run --locked --no-sync --project experiments/embedding-pilot \
  python experiments/embedding-pilot/pilot.py
```

Use fresh output/report paths to repeat the complete experiment without replacing evidence:

```sh
uv run --locked --no-sync --project experiments/embedding-pilot \
  python experiments/embedding-pilot/pilot.py \
  --output-dir data/processed/embedding-pilot-v1-repeat \
  --report data/processed/embedding-pilot-v1-repeat-report.json
```

For an already prepared, partially completed worker directory, restart with `pilot.py worker --output-dir <prepared-directory> --run-name clean` (or `resumed`). This worker command requires the original sample and compatible contract. A fully completed worker returns without changing its measured session. A whole-experiment `run` refuses an existing output directory.

Local verification passes **13 Java, 54 main Python/integration and 3 encoder-fixture tests**. The unchanged regression CI checks now include checkpoint integrity. A separate Linux CPU job installs the locked pilot environment and generates a tiny random local safetensors model to check pooling, masks, prefixes, Unicode, padding, truncation, dimensions and normalization. It never downloads the public pretrained model or ESCI and does not train the fixture.

The official test set remains unopened. No dense retrieval, fusion, fine-tuning, relevance gain or serving-performance result is claimed. The next proposed increment is a checkpointed full-catalog title embedding export under a frozen encoding contract, followed by a separate dense/fusion evaluation using the existing incomplete-judgment protocol.
