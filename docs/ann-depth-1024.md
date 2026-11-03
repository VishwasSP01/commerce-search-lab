# Depth 1024: better neighbor agreement, still below the unchanged budget

One separately predeclared **efSearch=1024** point on the same frozen HNSW graphs improves strict agreement from **90.91% to 94.74%**. It still misses the **95% overall agreement** requirement. All locale and hybrid-loss budgets pass, but the unchanged nomination function requires every check: **no ANN candidate is nominated**. Exact RRF remains the offline quality reference at **55.27% known-Exact Recall@100**.

This closes the additional-point experiment. No further depth, graph construction or threshold change is introduced here. The next core milestone is an Elasticsearch serving/parity feasibility pilot; additional ANN tuning can be a separate future question if serving constraints require it.

## Quality

All **3,379 validation queries** reuse the same saved vectors, locale routing and graphs covering **1,814,924 locale-product titles**. The graph parameters remain M=16, efConstruction=100, with the original construction seed/order and byte-identical index files. The only new search setting is 1024. The original 128/256/512 report is preserved.

| Metric | Frozen matched Flat | Depth 512 control | Depth 1024 |
| --- | ---: | ---: | ---: |
| Strict neighbor agreement@100 with Flat | Reference | 90.913% | **94.736%** |
| Dense known-Exact Recall@100 | 49.48% | 47.53% | **48.26%** |
| Hybrid known-Exact Recall@100 | 55.27% | 55.07% | **55.13%** |
| Hybrid judgment coverage@10 | 33.62% | 33.10% | **33.34%** |

Depth 1024's overall agreement interval is **[94.4838%, 94.9926%]**. The nomination rule uses the observed mean; it does not round 94.7363% up to the 95% threshold. Strict agreement measures set intersection with exact neighbors, not human relevance. Equivalence credit from the previous diagnosis does not enter this experiment or its decision. Coverage is not precision, and unknown judgments stay unknown.

| Locale | Agreement at 512 | Agreement at 1024 | Hybrid recall at 512 | Hybrid recall at 1024 |
| --- | ---: | ---: | ---: | ---: |
| English | 91.11% | **94.72%** | 52.31% | 52.35% |
| Spanish | 91.73% | **95.54%** | 60.57% | 60.58% |
| Japanese | 89.72% | **94.16%** | 58.70% | 58.88% |

Every locale exceeds the unchanged **90%** locale agreement floor. The overall **95%** check fails. Relative to matched Flat hybrid, depth 1024's query-mean recall change is **−0.1423 percentage points**, paired 95% interval **[−0.2770, −0.0227] pp**. That loss passes the declared overall lower-bound budget of −0.5 pp and every locale mean-loss budget. It remains a measured loss against exact retrieval, not parity.

Compared with depth 512, hybrid recall changes by **+0.0630 pp**, paired interval **[−0.0325, +0.1689] pp**. The higher mean does not demonstrate a hybrid gain under this interval. There are 43 improved, 43 regressed and 3,293 tied queries versus 512. Against matched Flat hybrid there are 35 improved, 46 regressed and 3,298 tied queries. All intervals use the existing paired normalized-query-group percentile bootstrap, 2,000 resamples and seed 20261101. This is one additional validation point chosen after a published diagnosis, with no adjusted confirmatory or final-test claim.

## Which exact neighbors changed

| Locale | Missing at 512 | Recovered at 1024 | Still missing | Newly missing | Missing at 1024 |
| --- | ---: | ---: | ---: | ---: | ---: |
| English | 18,563 | 7,553 | 11,010 | 17 | 11,027 |
| Spanish | 4,658 | 2,148 | 2,510 | 1 | 2,511 |
| Japanese | 7,484 | 3,247 | 4,237 | 11 | 4,248 |
| **Overall** | **30,705** | **12,948** | **17,757** | **29** | **17,786** |

The sets are not assumed to be nested. The accounting requires old missing = recovered + still missing, and new missing = still missing + newly missing. These are exact-neighbor memberships, not distinct catalog products or known-Exact labels. The previous float64 replacement diagnosis applies to depth 512; this increment does not automatically label all residual 1024 misses as strict score losses or infer a graph mechanism.

## Current native timing and memory

Depth 512 is remeasured alongside 1024 rather than reusing its older latency as a current comparator. Each method/locale uses a fresh process, one Faiss search thread, the original 16-query warmup and three full shuffled passes. Original seeded query orders are identical across methods. First run executes 512 then 1024 within each locale; fresh-process replay reverses that worker order.

| First-run pooled native retrieval | Depth 512 | Depth 1024 |
| --- | ---: | ---: |
| p50 | 0.902 ms | **1.931 ms** |
| p95 | 1.195 ms | **2.453 ms** |
| p99 | 1.372 ms | **2.760 ms** |

Each pooled method has **10,137 observations**, retaining the same query/language weighting. Depth 1024 takes approximately twice the native query time while improving neighbor agreement by 3.82 percentage points. These observations include native retrieval and deterministic candidate sorting, and exclude query encoding, HTTP/API handling, output serialization and concurrency. They are not end-to-end service latency or QPS.

The reverse-order replay retains separate timing observations; ranked IDs, scores, metrics, transitions and nomination reproduce exactly. The original Flat rankings/metrics are the matched quality control; its old latency is **historical and not remeasured**, so no new matched speedup against Flat is claimed.

Serialized HNSW files remain **2.840 GiB** and their ID maps/contracts/checksums are unchanged. Largest one-locale query-worker maximum RSS is approximately **2.10 GiB** for both depths (2.097 GiB at 512, 2.096 GiB at 1024 in the first run). The small difference is not evidence of a memory reduction from larger search depth. This is process memory for one loaded locale, not all-index or whole-host residency. First command main wall time is **71.68 seconds**, including validation, fresh workers, evaluation and artifact checks, excluding imports/final report serialization. No build or encoding cost is incurred.

## Evidence and reproduction

The [predeclared decision](decisions/0010-ann-depth-1024.md) and [configuration](../config/ann-depth-1024-v1.json) authorize one new point. The [aggregate report](../reports/ann-depth-1024-v1.json) retains every budget check, locale metric/interval, native timing, worker RSS, result transition and source/index binding. The [reverse-order replay record](../reports/ann-depth-1024-v1-reproduction.json) verifies identical detailed artifacts and decisions, with timing observations kept separately.

The runner verifies the original ANN and diagnostic report lineage, frozen source/lock files, original build contract, index/ID hashes, query identities and prepared validation data. Saved baseline metrics are reproduced before running. The new experiment record is separate from the unchanged original build contract used by the native workers to validate their inputs. No original grid/source file is edited. The depth-512 combined dense/hybrid/per-query artifacts must match the original benchmark byte-for-byte. Workers reject invalid native neighbors, enforce identical outputs across all three passes, and the new runner checks raw timing identities, exact query order, sample counts and finite positive durations. Index/input/contract hashes are verified afterward.

All **113 local tests** pass: 13 Java, 70 main Python/integration, 10 offline embedding/export/search and 20 native ANN/diagnostic/depth fixtures. Five additional tests exercise single-point authorization/search-only dispatch, immutable graph reuse across native 512/1024 workers, byte-identical control checks, nonnested transition conservation, original timing orders/invalid durations, and unchanged nomination boundaries. CI uses tiny fabricated fixtures without real data or pretrained model downloads.

Initial command:

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/run_depth_1024.py
```

The tracked report and initial output now exist and are refused. With prior frozen inputs available, use fresh paths; reverse the worker order for the repeat:

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/run_depth_1024.py \
  --output-dir data/processed/ann-depth-1024-v1-local \
  --report data/processed/ann-depth-1024-v1-local-report.json \
  --reverse-order
```

Bulk rankings, query vectors, indexes and raw timings stay ignored. The official test remains unopened. Nomination is an offline development decision, not production adoption; this one-point experiment is complete without a passing candidate. Keep exact RRF and proceed to the next separately scoped serving milestone.
