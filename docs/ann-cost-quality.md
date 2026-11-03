# Float32 ANN: faster queries, but no setting meets the declared quality budget

The fixed HNSW experiment **nominates no ANN setting**. At the deepest tested search depth, warm native retrieval p95 is **1.08 ms** versus **25.27 ms** for a matched exhaustive control, but top-100 neighbor agreement is **90.91%**, below the predeclared 95% requirement. Hybrid known-Exact Recall@100 changes from **55.27% to 55.07%**. Retain the exact-dense RRF reference; do not quietly relax the budget or extend the parameter grid after seeing these results.

## Quality and the decision

All runs reuse the same saved **1,814,924 locale-product vectors** and **3,379 validation query vectors**. Each query searches only its own locale. The graph family is Faiss CPU 1.15.1 `IndexHNSWFlat`, inner product on normalized float32 vectors, M=16, efConstruction=100, with four construction threads and seed 20261102. Only search depth varies: **efSearch 128, 256, 512**. The official test remains unopened.

| Method | Agreement with matched exact top 100 | Dense known-Exact Recall@100 | Hybrid known-Exact Recall@100 |
| --- | ---: | ---: | ---: |
| Faiss Flat exhaustive control | Reference | 49.48% | 55.27% |
| HNSW, efSearch 128 | 76.88% | 44.18% | 54.69% |
| HNSW, efSearch 256 | 85.39% | 46.27% | 54.90% |
| HNSW, efSearch 512 | **90.91%** | **47.53%** | **55.07%** |

Agreement is the size of the intersection of ANN and exact top-100 product sets, divided by 100, averaged over queries. It measures approximation of nearest neighbors, **not human relevance**. Known-Exact recall measures retrieval of labeled Exact products under incomplete judgments. Their denominators and meanings differ; the smaller hybrid relevance loss does not imply 99% nearest-neighbor agreement.

Hybrid results replay the unchanged RRF rule: equal weights, constant 60, BM25 and dense windows 100, final 100. Relative to matched Flat hybrid, overall recall changes are:

| efSearch | Hybrid change, percentage points | Paired 95% interval |
| --- | ---: | ---: |
| 128 | −0.58 | [−0.84, −0.33] |
| 256 | −0.37 | [−0.59, −0.16] |
| 512 | −0.21 | [−0.37, −0.05] |

The budget requires >=95% agreement overall and >=90% in every locale, hybrid recall's paired lower change bound >=−0.5 percentage points overall, and no locale mean loss exceeding 0.5 points. Depth 512 passes the hybrid-loss budgets but fails agreement overall and in Japanese. Depths 128 and 256 also fail the overall hybrid interval budget. These are declared development budgets, not universal product requirements or production SLOs.

| Locale, depth 512 | Neighbor agreement | Exact hybrid recall | ANN hybrid recall |
| --- | ---: | ---: | ---: |
| English | 91.11% | 52.54% | 52.31% |
| Spanish | 91.73% | 60.60% | 60.57% |
| Japanese | **89.72%** | 58.98% | 58.70% |

Depth 512 improves hybrid recall for 71 queries versus matched Flat, regresses for 81, and ties for 3,227. Approximation can sometimes recover a labeled product that lies outside an exact top-100 list; this does not make it a more faithful nearest-neighbor algorithm. Its hybrid judgment coverage@10 is **33.10%**, versus **33.62%** for the Flat hybrid. Coverage is not precision; unjudged products remain unknown and retain their actual ranks.

Marginal and paired percentile intervals use the existing metric implementations, 2,000 resamples of locale-normalized query groups and seed 20261101. All 3,379 validation groups are distinct. The aggregate report includes every locale, grid point, interval and comparison to both the matched native and frozen Torch references. These are finite-grid, repeatedly inspected validation-development results, without final-test or adjusted confirmatory significance claims.

## A matched exact control and explicit ties

The native control uses `IndexFlatIP` containing the exact same stored vectors. It avoids comparing old batched Torch throughput with new singleton Faiss timings. Against the frozen Torch exact lists, Faiss Flat has **99.9967% set agreement**: 11 reference members are missing across 337,900 top-100 slots, with 11 replacements. Both dense and hybrid primary recall means remain unchanged, while small rank/judgment-coverage differences remain visible in the report. Cross-backend floating-point results are not claimed bit-identical.

Flat expands the native result request until all products equal to the cutoff score are included, then sorts by descending score and ascending product ID. ANN sorts its returned 100 candidates; unseen or excluded cutoff ties remain approximation loss. It does not claim globally deterministic tie membership. Integer neighbor IDs must be unique and in range, and map to sorted string product IDs inside the correct locale, including leading zeros. Missing/invalid native neighbors fail the run.

Source reports, code/lock hashes, query identities, vector checksums/norms, row-aligned IDs and full per-locale counts are verified. The pipeline reproduces the saved baseline per-query metrics before benchmarking. No encoding, training, new judgments, query expansion, int8 or serving integration occurs.

## Warm retrieval latency

Measurements run on the existing **Apple M5 Pro, 24 GiB RAM, 15 CPU cores**. Each fresh worker loads one locale and one index, uses **one Faiss search thread**, warms 16 shuffled queries, then runs three full shuffled singleton-query passes. Query orders are identical across methods, seeded 20261102 plus pass number. Each pooled row contains **10,137 observations**, with the same language/query weighting. Every pass must reproduce identical ranked IDs and scores.

| Method | p50, ms | p95, ms | p99, ms |
| --- | ---: | ---: | ---: |
| Faiss Flat | 24.489 | 25.268 | 49.232 |
| HNSW 128 | 0.238 | 0.330 | 0.391 |
| HNSW 256 | 0.431 | 0.571 | 0.640 |
| HNSW 512 | **0.846** | **1.085** | **1.176** |

These are first-run, warm, serial **native retrieval plus tie handling/sorting** observations. They exclude query encoding, HTTP/API handling, output serialization/file writes and concurrent traffic. The depth-512 ratio of pooled median times is about 29×; it is not an end-to-end/API speedup. The report retains per-locale/per-pass timing, load time and raw per-query durations under ignored data. The exact control pays for complete cutoff ties; ANN's candidate set remains approximate. Local OS scheduling and cache/thermal state can affect timings, so the replay retains separate observations rather than replacing the first run.

## Build, storage and memory

| Locale | Products | Build wall time | Build-worker maximum RSS |
| --- | ---: | ---: | ---: |
| Spanish | 260,011 | 16.27 s | 0.867 GiB |
| Japanese | 339,059 | 20.19 s | 1.352 GiB |
| English | 1,215,854 | 87.23 s | 4.763 GiB |

Four-thread construction takes **118.55 seconds of native add work**, or **123.69 seconds** summed locale build wall time including verification and persistence. Four-thread insertion can produce different graphs on an independent rebuild; this experiment freezes the actual checksummed graph and does not claim independent-build bitwise identity.

The three HNSW files total **3,049,529,854 bytes (2.840 GiB)**. The three Flat controls total **2,787,723,399 bytes (2.596 GiB)**, approximately the original raw-vector payload plus file headers. ID maps and build manifests are separate. HNSW adds graph storage; it does not compress the vectors. Both index families are retained for the benchmark, so their combined disk footprint is not a proposed serving footprint.

The largest query-worker peak RSS is **1.934 GiB** for Flat and **2.093 GiB** for HNSW, both on English. These peaks include native index loading, Python IDs, query vectors, warmup, search and output handling. Workers load one locale/index at a time: this is not measured residency of all three indexes together, total host memory, or construction memory. Serialized bytes must not be substituted for process RSS. First command main wall time is **395.09 seconds**, including build, isolated workers, verification and evaluation; it excludes interpreter/import startup and final report serialization. Int8 cost and quality remain unmeasured.

## Verification and reproduction

All **100 local tests** pass: 13 Java, 70 main Python/integration, 10 offline embedding/export/search and 7 native ANN fixtures. The ANN fixtures cover full exact cutoff ties with >100 duplicate vectors, negative scores, corrupt/out-of-range neighbors, locale/string-ID alignment, persistence and reload, changed-file rejection, agreement identities and nomination budgets. A separate CPU CI job installs the isolated lock and uses only tiny fixtures, without real data or pretrained models.

The [predeclared protocol](decisions/0008-ann-cost-quality.md), [configuration](../config/catalog-ann-v1.json) and [aggregate report](../reports/catalog-ann-v1.json) bind the method and evidence. The [fresh-process replay verification](../reports/catalog-ann-v1-reproduction.json) confirms all **12 aggregate artifact hashes** and **12 worker ranking hashes** match, every metric/interval/decision is unchanged, and the index/source hashes remain intact. Every worker also checks all three passes internally: six identical timed ranked outputs per query/method across the two runs. Raw timing summaries were recomputed from saved individual samples; timing/session evidence trees are checksummed separately because their measurements vary. This reuses the frozen graphs, not an independent graph-construction trial.

Initial run, from the repository root after the prior data/vector milestones:

```sh
uv sync --locked --project experiments/ann-benchmark
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/benchmark.py
```

The initial output/public report now exist and are refused. A fresh clone also has the tracked report, so use fresh output/report paths to rebuild the experiment. To replay the existing local graph bundle in fresh processes:

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/benchmark.py \
  --output-dir data/processed/catalog-ann-v1-local \
  --report data/processed/catalog-ann-v1-local-report.json \
  --indices-dir data/processed/catalog-ann-v1/indices
```

Omit `--indices-dir` with fresh output/report paths for an independent graph construction; bit-identical results are not promised. `--resume` can reuse completed locale builds after an interrupted construction, under the same contract. An interrupted query worker needs fresh query-output paths, optionally reusing the saved indexes. Bulk indexes, ID maps, ranked lists and raw timings remain ignored.

## Next decision

Keep the exact RRF reference at 55.27%. The result demonstrates a speed/quality tradeoff under one graph construction and a fixed search-depth grid, not that ANN generally fails. Before piling compression onto this candidate, inspect the misses in a separate bounded analysis, then predeclare any construction/search ablation. Int8 remains a separate cost/quality experiment. Do not increase search depth, change the graph, or relax the budget inside this completed experiment.
