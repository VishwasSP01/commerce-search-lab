# Full-catalog title embedding export

**Complete: all 1,814,924 locale-product titles are exported and validated.** Recorded export-session wall time totals **37.5 minutes**, with **1.61 GiB kernel-reported maximum process RSS**. The saved bundle is **2.668 GiB**, including vectors, row-aligned identities and manifests. This is a measured full-catalog export, not a retrieval-quality result.

The [predeclared contract](decisions/0005-full-catalog-embeddings.md) and [configuration](../config/catalog-embeddings-v1.json) extend the [pilot](embedding-pilot.md) to every pinned ESCI locale-product title. This step produces reusable vectors and row identities; dense indexing, retrieval evaluation, fusion and training remain separate work. No query or judgment partition is opened.

The contract was committed in `0b91c4c` before encoding. The [aggregate report](../reports/catalog-embeddings-v1.json) records exact measurements and provenance; the [restart/replay verification](../reports/catalog-embeddings-v1-verification.json) records preserved artifacts. All bulk output remains outside Git.

## Measured full-catalog results

The existing Apple M5 Pro / 24 GiB machine used MPS with Python 3.12.16, PyTorch 2.9.1, Transformers 4.57.1 and the unchanged encoder/model revision from the pilot.

| Locale | Titles | Shards | Encode-stage time | Titles/encode second | Truncated titles | Longest untruncated token sequence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Spanish (es) | 260,011 | 64 | 5.028 min | 861.9 | 22 (0.00846%) | 216 |
| Japanese (jp) | 339,059 | 83 | 8.563 min | 659.9 | 715 (0.21088%) | 271 |
| English (us) | 1,215,854 | 297 | 22.209 min | 912.4 | 52 (0.00428%) | 223 |
| Total | **1,814,924** | **444** | **35.800 min** | **844.9** | **789** | **271** |

All locale-product keys are strictly ordered and counted exactly; repeated product IDs across locales remain distinct keys. There are no missing output rows. Every payload passes dimension/byte-count, finite-value and unit-norm validation. Observed float32 norm minima/maxima are approximately **0.9999998212 / 1.0000001192**.

The 3,072 pilot identities all agree with their catalog counterparts: maximum absolute difference **1.0430813e-7**, minimum cosine **0.9999999999998463**, comfortably within the registered tolerances. These checks validate encoding continuity and identity alignment; they do not establish relevance.

There are 789 titles over the fixed 128-token limit. Token counts include prefix and special tokens. The model's 512-token capability was not substituted during this run; truncation is an explicit limitation, and its retrieval impact remains unmeasured.

| Resource/time | Measured value |
| --- | ---: |
| Recorded wall time across the two export sessions | 2,250.622 s (37.510 min) |
| Summed encode-stage time | 2,147.994 s (35.800 min) |
| Maximum kernel-reported process RSS | 1,732,755,456 bytes (1.614 GiB) |
| Maximum observed MPS tensor allocation after a forward call | 482,395,136 bytes |
| Maximum observed MPS driver allocation after a forward call | 1,144,881,152 bytes (1.066 GiB) |
| Raw vector payloads | 2,787,723,264 bytes (2.596 GiB) |
| Row-aligned ID maps | 76,226,808 bytes (72.70 MiB) |
| All export files, logical bytes | 2,864,726,393 bytes (2.668 GiB) |
| Reported allocated file blocks | 2,866,159,616 bytes |

All encoded rows are attributed to the two recorded sessions: 8,192 in the controlled-stop invocation and 1,806,732 after restart. Wall time includes repeated startup/source validation and shard work, excludes downtime between invocations and the later verification-only replay, and is not an API latency or production-throughput claim. Model load, warmup and shard I/O/validation durations are recorded per session.

The pilot projected roughly 41–45 minutes of encode-stage work; this full run measured 35.8 minutes for that stage and 37.5 minutes of recorded wall time. The pilot range was a planning estimate, not a confidence interval. Workload ordering, padding, caches and host conditions differ; this observation does not isolate the cause of the difference or guarantee a repeat runtime.

## Encoding and data layout

The model revision, verified local safetensors, encoder source and dependency lock remain those of the pilot. Use NFKC, `passage: `, float32, eager attention, masked mean pooling, L2 normalization, batch size 32, max length 128 and four CPU threads. Actual runtime/backend is bound into the export contract. Inference is offline and unsupported-operator MPS fallback is disabled.

Stream records in locale order es/jp/us and ascending product ID, retaining at most one 4,096-row title chunk in application memory. Each locale has its own shorter final shard if necessary. This increases the checkpoint size from the pilot's 256 rows without changing inference batch size. Independent shard manifests avoid repeatedly serializing a global growing checkpoint list.

DuckDB performs the sort with one thread and a 4 GB memory setting. Its buffers are additional to the Python title chunk; the setting is not a cap on total process or GPU memory.

Each `shards/00000/` directory contains:

- `ids.jsonl`: one locale/product-ID object per vector row, preserving string IDs.
- `shard-00000.f32`: little-endian, row-major float32 values, 384 columns per row. The payload filename is fixed within each independently numbered directory.
- `checkpoint.json`: row count, input hash, vector hash, ID-map hash, encoding statistics and the parent contract fingerprint.

`contract.json` binds the export to source/model files, settings, code, reference pilot and runtime. The final `export-manifest.json` lists every shard and records ordered vector/ID sequence hashes. Bulk data stays under ignored `data/processed/catalog-embeddings-v1/`; the compact public report contains measurements and final-manifest provenance.

## Validation and restart behavior

The unchanged pilot checkpoint helper validates payload shape, finite values and unit norm within 1e-5. The export additionally checks strict key ordering, exact per-locale counts, locale-separated shard boundaries and ID-map bytes against regenerated identities. A committed shard is skipped only after validating its contract, input identity, payload and ID map. Corruption or incompatible settings fail explicitly. Uncommitted orphan files may be replaced; committed output is never silently regenerated.

Every pilot identity is compared with its full-export vector, with predeclared maximum absolute difference 1e-5 and minimum cosine 0.99999. Batch grouping and padding lengths differ between hash-sampled pilot order and product-ID catalog order, so byte equality is not required.

The full-export restart experiment uses a controlled stop after two committed shards, then resumes and verifies those files remain unchanged. This is distinct from the pilot's real SIGKILL experiment. Completed replay verifies the full export without re-encoding rows or replacing measured sessions. No disk-power-loss or multi-host exactly-once guarantee is claimed.

The measured restart preserved all eight prefix artifact hashes, including the first session's timing record. The resumed session reused two shards and generated the remaining 442. A completed replay verified all 444 shards, encoded zero, and preserved **all 1,336 data/manifest/session artifact hashes plus the public report**. Dot-prefixed writer locks are excluded from that artifact count. Compact before/after tree fingerprints are published; the full hash mapping stays under ignored `data/processed/`.

## Reproduction

Prerequisites: verified raw product metadata, local pinned model files, the original or reproduced pilot reference and the locked embedding environment. Existing pilot source and frozen BM25 source/lock fingerprints must still match. Model loading during this export never downloads files.

Run the commands from the repository root.

```sh
uv sync --locked --project experiments/embedding-pilot
uv run --locked --no-sync --project experiments/embedding-pilot \
  python experiments/embedding-pilot/export_catalog.py
```

The same command resumes a partial compatible export. To exercise the controlled stop, add `--stop-after-shards 2` on the first invocation (expected exit code 75), then run normally. Keep code/settings unchanged while a job is in progress; source fingerprints deliberately reject a changed contract.

Use fresh paths for an independent export:

```sh
uv run --locked --no-sync --project experiments/embedding-pilot \
  python experiments/embedding-pilot/export_catalog.py \
  --output-dir data/processed/catalog-embeddings-v1-repeat \
  --report data/processed/catalog-embeddings-v1-repeat-report.json
```

If reproducing the pilot on a new machine, first use its fresh output/report commands in the [pilot instructions](embedding-pilot.md), then pass those paths as `--pilot-dir` and `--pilot-report`. The export binds to that compatible reference without requiring its timings to match the original published observation.

Verification passes **13 Java, 58 main Python/integration and 6 encoder/streaming fixture tests**. New fixtures check locale ordering, string IDs, short final shards, no-op replay, identity-map corruption, contract changes, writer exclusion and locale/row-aware pilot reference matching. Existing offline CPU encoder tests and packaged Java integrations remain required in CI. No pretrained weights or ESCI are downloaded by CI.

## Measurement limits

Per-locale encode durations include preprocessing/prefixing inside each batch, tokenization, transfers, model forward pass, pooling, normalization, synchronization and float32 CPU output. Load/warmup, full-length token analysis, checkpoint/ID validation, sorting and file persistence are outside encode-stage time but included in recorded session wall time. Wall time excludes between-invocation downtime, interpreter bootstrap and final report serialization. All cleanly recorded sessions must account for every encoded row before interpreting their sum as measured full-work wall time; interrupted sessions can otherwise leave incomplete timing attribution.

Kernel maximum RSS measures the worker process. Observed MPS tensor/driver allocations are not strict GPU peaks, overlap unified-memory accounting and must not be summed with RSS. Logical file bytes and reported file blocks exclude directory metadata, model cache, original metadata and public reports. These measurements establish neither ANN memory/capacity nor API latency or production throughput.

All truncation remains visible at the fixed 128-token limit. A complete vector export does not establish useful retrieval: the next stage must use the existing locale-routed, incomplete-judgment evaluation contract and preserve the official test holdout.

The next proposed increment is a bounded dense retrieval baseline over these validated vectors, followed by a separate comparison/fusion experiment. No dense-search quality, ANN index, fine-tuning or serving result is included here.
