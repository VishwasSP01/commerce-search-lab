# commerce-search-lab

A planned multilingual product-search system built around reproducible relevance experiments and measured serving behavior.

**Status:** the offline retrieval foundation, RRF fusion, ANN benchmark/diagnosis and separate depth-1024 experiment are complete. Exact-dense RRF remains the reference at **55.27%** validation known-Exact Recall@100. The new depth improves agreement to **94.74%**, still below its unchanged 95% overall budget; no ANN setting is nominated. Serving integration is next; int8 and training remain future work, and the official test stays reserved.

The project will investigate whether multilingual retrieval and ranking improvements justify their serving cost, and how product updates affect freshness, correctness, and search latency.

Start with the [engineering charter](docs/engineering-charter.md). It defines the research questions, evidence requirements, release gates, and provisional resource envelope.

**Budget constraint:** EUR 0 additional spending. Use the existing machine and locally runnable software. See the [zero-budget execution plan](docs/zero-budget-plan.md).

## First deliverable

A data-feasibility report and reproducible BM25 evaluation on a bounded sample. Confirm dataset versions, joins, locale coverage, official splits, label interpretation, and evaluation limitations before building the distributed pipeline or scaling experiments.

## Completed step: ESCI data audit

The [audit report](docs/esci-data-audit.md) records 2,621,288 judgments, 130,652 queries, and 1,814,924 locale-specific catalog records. Every judgment joins to product metadata. Split-overlap findings are documented and must be handled before model training.

The [machine-readable report](reports/esci-data-audit.json) contains aggregate evidence; raw data remains outside Git. With Python 3.12 and `uv` available, run from the repository root:

```sh
uv sync --locked
uv run --locked python scripts/download_esci.py
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/audit_esci.py
```

## Completed step: evaluation splits

The [split policy and results](docs/evaluation-splits.md) define training (30,423 queries), validation (3,379), and the preserved official test set (14,496). Two overlapping training queries are excluded. All three languages are represented, and the prepared partitions have no overlap under the declared query-ID and normalization checks.

```sh
uv run --locked python scripts/prepare_esci_splits.py
```

Generated datasets remain under the ignored `data/processed/` directory. The [aggregate split report](reports/esci-split-report.json) records counts, checks, provenance, and artifact hashes.

## Completed step: BM25 baseline

The Java 21 / Lucene ranker indexes **1,814,924 product titles** and evaluates all **3,379 validation queries** against their judged candidates. It uses English, Spanish and Japanese analyzers with fixed BM25 parameters.

| Validation NDCG@10 | English | Spanish | Japanese | Overall, query-weighted |
| --- | ---: | ---: | ---: | ---: |
| Product-ID-order control | 0.5585 | 0.5548 | 0.5753 | 0.5615 |
| Title BM25 | **0.6945** | **0.6826** | **0.7139** | **0.6967** |

Read the [results, limits and reproduction instructions](docs/bm25-baseline.md), [predeclared design](docs/decisions/0001-bm25-baseline.md), and [aggregate evidence](reports/bm25-title-v1.json). These are candidate-ranking development results, not exhaustive full-catalog relevance or final test scores. The official test set remains unscored.

With Java 21 and the earlier data preparation complete:

```sh
./mvnw --batch-mode --no-transfer-progress verify
uv run --locked python scripts/run_bm25.py
```

## Completed step: validation error analysis

The [error analysis](docs/bm25-error-analysis.md) reproduces the baseline outcomes and distinguishes 78 title-vocabulary gaps from one empty-analysis query. Of 977 losses against the control, 352 still rank an Exact product first and 371 rank a Substitute first. Metadata and 18 inspected cases suggest hypotheses, not proven fixes.

The [aggregate diagnostic report](reports/bm25-error-analysis-v1.json) and [qualitative case notes](docs/bm25-case-review.csv) support a separate, controlled title-plus-bullet experiment. The ranker and official test holdout remain unchanged.

```sh
uv run --locked --no-sync python scripts/analyze_bm25_errors.py
```

## Completed step: title-plus-bullet ablation

One weight was committed before measurement: `title BM25 + 0.25 × bullet BM25`. On the same validation candidates, overall NDCG@10 changes from **0.696695 to 0.696164** (paired 95% delta interval **[−0.003199, +0.002264]**). The adoption rule fails, so **title-only remains the baseline**. The official test stays unscored.

The [experiment write-up](docs/bm25-title-bullet.md) documents improvements, regressions, missing metadata and added storage. The [aggregate report](reports/bm25-title-bullet-v1.json) records full results, and the [independent-repeat checks](reports/bm25-title-bullet-v1-reproduction.json) record exact score and metric reproduction. This is an unsuccessful validation-development variant, not a neural or full-catalog quality claim.

```sh
uv run --locked --no-sync python scripts/run_title_bullet.py
```

## Completed step: full-catalog retrieval baseline

Title BM25 now searches the entire product catalog within each query's locale, without judged-candidate filtering. The three locale corpora total **1,814,924 products**.

| Validation retrieval | English | Spanish | Japanese | Overall, query-weighted |
| --- | ---: | ---: | ---: | ---: |
| Known-Exact Recall@100 | 46.98% | 58.08% | 53.77% | **50.29%** |
| Judgment coverage among returned top ten | 26.61% | 42.46% | 43.61% | **32.91%** |

Recall measures retrieval of **known** Exact products; unknown relevance remains unknown. Coverage is not precision. The [results and protocol](docs/full-catalog-bm25.md) explain denominators, confidence intervals, empty/short lists, locale scope and limitations. The [aggregate report](reports/catalog-bm25-title-v1.json) and [independent-repeat verification](reports/catalog-bm25-title-v1-reproduction.json) record reproducible evidence. This is separate from candidate-ranking NDCG; the official test remains reserved.

```sh
uv run --locked --no-sync python scripts/run_catalog_bm25.py
```

## Completed step: multilingual embedding pilot

The pinned multilingual E5 model encoded **3,072 real titles** on the existing Mac's MPS GPU. A forced worker termination followed by restart preserved 512 completed titles, and all resumed vectors matched the clean run exactly. The sample produced 384-dimensional normalized float32 vectors; two Japanese titles were truncated at the declared 128-token limit.

The [pilot report](docs/embedding-pilot.md) records throughput, measured process memory, observed GPU allocations, checkpoint integrity and limitations. It estimated roughly **41–45 minutes of encode-stage work** and **2.596 GiB** of raw vectors before metadata/ANN overhead. The subsequent full-catalog export below replaces that planning estimate with measured results. No relevance gain is claimed.

```sh
uv sync --locked --project experiments/embedding-pilot
uv run --locked --no-sync --project experiments/embedding-pilot python experiments/embedding-pilot/pilot.py
```

## Completed step: full-catalog embeddings

All **1,814,924 locale-product titles** now have verified 384-dimensional float32 embeddings and row-aligned IDs. The export ran on the existing Mac's MPS GPU in **37.5 minutes of recorded session wall time**, with **1.61 GiB maximum process RSS**. The saved bundle is **2.668 GiB**, including **2.596 GiB** of raw vectors; ANN overhead remains unmeasured.

The run preserved 8,192 completed titles across a controlled stop/restart, matched all 3,072 pilot references within tolerance, and passed a full replay without changing any of its 1,336 artifact hashes or re-encoding a shard. **789 titles** exceeded the fixed 128-token limit. These are export/integrity measurements, not a relevance gain.

Read the [results and reproduction instructions](docs/full-catalog-embeddings.md), [aggregate report](reports/catalog-embeddings-v1.json), and [restart/replay evidence](reports/catalog-embeddings-v1-verification.json). The official test set remains untouched; all bulk data and weights stay ignored.

```sh
uv run --locked --no-sync --project experiments/embedding-pilot python experiments/embedding-pilot/export_catalog.py
```

## Completed step: exhaustive dense retrieval

The pinned multilingual E5-small model searches every product in each query's locale, reusing the saved title vectors. Two fresh runs, including query encoding, reproduce all five artifact hashes and every metric/interval. The official test remains reserved.

| Validation known-Exact Recall@100 | English | Spanish | Japanese | Overall, query-weighted |
| --- | ---: | ---: | ---: | ---: |
| Title BM25 | 46.98% | 58.08% | 53.77% | **50.29%** |
| Exhaustive dense | 48.47% | 47.94% | 53.53% | **49.48%** |

The paired overall change is **−0.81 percentage points**, 95% interval **[−1.77, +0.11] pp**: no demonstrated overall gain. Spanish regresses substantially. Judgment coverage@10 changes from 32.91% to 28.36%; coverage is not precision, and unjudged products retain unknown relevance. **Keep BM25 as the baseline.**

The first run takes **39.71 seconds** of main wall time, including query encoding, verification and evaluation; maximum process RSS is **0.974 GiB**. This is an exhaustive CPU batch reference, with MPS query encoding, not API capacity or ANN performance. Read the [results, interpretation and reproduction instructions](docs/full-catalog-dense.md), [predeclared protocol](docs/decisions/0006-exact-dense-retrieval.md), [aggregate evidence](reports/catalog-dense-exact-v1.json) and [independent-repeat verification](reports/catalog-dense-exact-v1-reproduction.json).

```sh
uv run --locked --no-sync --project experiments/embedding-pilot python experiments/embedding-pilot/run_dense.py
```

## Completed step: hybrid retrieval with RRF

One predeclared configuration combines the two saved top-100 lists using equal-weight reciprocal rank fusion, rank constant 60, final cutoff 100. No new retrieval, encoding, training or parameter sweep is involved.

| Validation known-Exact Recall@100 | English | Spanish | Japanese | Overall, query-weighted |
| --- | ---: | ---: | ---: | ---: |
| Title BM25 | 46.98% | 58.08% | 53.77% | **50.29%** |
| Exhaustive dense | 48.47% | 47.94% | 53.53% | **49.48%** |
| RRF | **52.54%** | **60.60%** | **58.98%** | **55.27%** |

RRF gains **+4.98 percentage points** over BM25, paired 95% interval **[+4.32, +5.59] pp**, and **+5.80 pp** over dense. It passes the predeclared offline nomination rule. **441 queries still regress versus BM25.** Judgment coverage@10 is 33.63% overall, while Spanish coverage decreases; coverage is not precision.

RRF recovers at least one known Exact product for **19 of the original 78 vocabulary-gap queries**; 59 still have none. Two fresh fusion runs reproduce all four artifact hashes and every metric/interval/decision, preserving the source lists. This is incomplete-label validation evidence, not a final-test, exhaustive-relevance or production claim.

Read the [results, regressions, recovery analysis and reproduction instructions](docs/full-catalog-rrf.md), [predeclared protocol](docs/decisions/0007-rrf-hybrid-retrieval.md), [aggregate report](reports/catalog-rrf-v1.json) and [repeat verification](reports/catalog-rrf-v1-reproduction.json).

```sh
uv run --locked --no-sync python scripts/run_rrf.py
```

## Completed step: ANN cost and quality

One Faiss float32 HNSW graph per locale indexes all **1,814,924 saved vectors**, with fixed M=16 and efConstruction=100. Three search depths are compared with a matched exhaustive Faiss control using the same saved validation query vectors and singleton CPU searches.

| Method | Exact-neighbor agreement@100 | Hybrid known-Exact Recall@100 | Warm native p95, ms |
| --- | ---: | ---: | ---: |
| Faiss Flat control | Reference | **55.27%** | 25.268 |
| HNSW, efSearch 128 | 76.88% | 54.69% | 0.330 |
| HNSW, efSearch 256 | 85.39% | 54.90% | 0.571 |
| HNSW, efSearch 512 | **90.91%** | **55.07%** | **1.085** |

**No ANN setting is nominated:** depth 512 misses the >=95% overall and >=90% per-locale neighbor-agreement budgets, despite passing the separate hybrid-recall loss budget. Neighbor agreement and human-label recall are different metrics. Keep the exact RRF reference. The graph files occupy **2.840 GiB**, versus **2.596 GiB** for Flat; the largest one-locale query-worker RSS is 2.093 GiB for HNSW versus 1.934 GiB for Flat. These warm serial native timings exclude encoding/API/concurrency and are not serving capacity.

The [results, limits and reproduction instructions](docs/ann-cost-quality.md), [predeclared protocol](docs/decisions/0008-ann-cost-quality.md), [aggregate report](reports/catalog-ann-v1.json) and [fresh-process replay evidence](reports/catalog-ann-v1-reproduction.json) preserve the losing configurations and matched-control comparison. Both runs reproduce every ranked/metric artifact and decision on the same frozen graphs, with distinct timing observations. The official test remains reserved; bulk indexes and timings stay ignored.

```sh
uv sync --locked --project experiments/ann-benchmark
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/benchmark.py
```

## Completed step: ANN miss diagnosis

On all 3,379 validation queries, the saved depth-512 lists omit **30,705** exact top-100 members. Deterministic matching classifies **30,650 (99.82%)** as strict higher-score omissions and **55** as identical-vector replacements. Crediting equivalent replacements changes agreement only from **90.913% to 90.929%**; the original adoption decision remains unchanged.

The dense lists lose **596** known-Exact query-product pairs and add **189**; the hybrid lists lose **103** and add **81**. All known-Exact dense losses fall in the strict-score-loss category. Japanese has 7,478 strict-score-loss pairs and 210 lost known-Exact dense pairs. This is a diagnosis of the fixed approximation, not evidence of a language-model cause. Unknown relevance stays unknown.

Both fresh diagnostic runs reproduce all detailed artifact hashes, aggregates and case membership without another nearest-neighbor search or index change. Read the [diagnosis and representative cases](docs/ann-miss-analysis.md), [predeclared protocol](docs/decisions/0009-ann-miss-analysis.md), [aggregate report](reports/ann-miss-analysis-v1.json) and [repeat verification](reports/ann-miss-analysis-v1-reproduction.json). The next proposed experiment is one separately predeclared depth-1024 point on the same frozen graphs; no improvement is assumed.

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/analyze_misses.py
```

## Completed step: one fixed depth-1024 experiment

The same graphs and saved query vectors are tested at **efSearch=1024**, with a fresh depth-512 timing control. Strict neighbor agreement improves **90.91% → 94.74%** and hybrid known-Exact Recall@100 changes **55.07% → 55.13%**. The hybrid change versus 512 is **+0.063 pp**, paired 95% interval **[−0.033, +0.169] pp**, so the higher mean is not a demonstrated hybrid gain.

Depth 1024 recovers **12,948** previously missing exact neighbors, leaves **17,757** still missing and introduces **29** newly missing members. Its native warm p95 is **2.453 ms**, versus **1.195 ms** for the contemporaneous 512 control. These timings exclude encoding/API/concurrency; serialized indexes remain unchanged.

**The 95% overall agreement check still fails**, although every locale and relevance-loss budget passes. No new candidate is nominated and the stopping rule is honored: no further depth or construction change in this experiment. Exact RRF stays the quality reference. Two fresh-process runs reverse worker order while reproducing rankings, metrics and the decision; the original 512 artifacts remain byte-identical.

Read the [outcome, limits and reproduction instructions](docs/ann-depth-1024.md), [predeclared protocol](docs/decisions/0010-ann-depth-1024.md), [aggregate report](reports/ann-depth-1024-v1.json) and [reverse-order replay evidence](reports/ann-depth-1024-v1-reproduction.json).

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/run_depth_1024.py
```

## Continuous integration

The [CI workflow](.github/workflows/ci.yml) runs on pull requests targeting `main` (including new commits pushed to those PRs) and on pushes to `main`. It installs locked Python dependencies, builds Java 21, and runs the Java, Python and packaged integration tests on Ubuntu. Superseded runs are cancelled, and each job has a five-minute timeout.

The tests use small fabricated fixtures. A separate CPU-only embedding job installs its own locked environment and checks a tiny locally generated model, shard integrity and exhaustive dense retrieval, without downloading pretrained weights. A third CPU job checks native ANN persistence/reload, ties, locale identities and quality budgets in its own locked environment. CI does not download ESCI, generate benchmark datasets, or train models; real-data experiments remain separate commands above. No artifact uploads or Actions caches are configured. Standard GitHub-hosted runners in public repositories are [free under GitHub's current billing policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

To run the same commands locally:

```sh
uv sync --locked
./mvnw --batch-mode --no-transfer-progress verify
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
```

## Sources to audit

- [Amazon ESCI dataset and baseline code](https://github.com/amazon-science/esci-data)
- [Amazon Reviews 2023 dataset documentation](https://amazon-reviews-2023.github.io/)

These datasets serve different purposes. Catalog expansion does not provide additional query-product relevance judgments or a live product-update feed.
