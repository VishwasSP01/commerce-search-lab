# First BM25 baseline: measured validation results

Completed locally on 1 November 2026. The [aggregate report](../reports/bm25-title-v1.json) records the exact configuration, inputs, source fingerprints, artifact hashes, confidence intervals and execution measurements. The [design decision](decisions/0001-bm25-baseline.md) fixed the experiment before its first successful measurement.

## What was built

A Java 21 / Lucene 10.6.0 title index containing **1,814,924 real locale-product records**, with English, Spanish and Japanese analysis. A Python driver verifies inputs, exports label-free Java inputs, runs candidate scoring and calculates ranking metrics.

BM25 requires no supervised training. Collection statistics come from the full catalog's locale-specific title fields. At evaluation time, scoring is restricted to each validation query's judged candidate list. All candidates remain in the ranking, including zero lexical matches; missing indexed candidates are errors.

This is an offline candidate-ranking baseline. A full-catalog search evaluation, API, embeddings, neural fine-tuning and streaming ingestion are later increments.

## Results

All **3,379 validation queries and 77,934 judgments** were evaluated. Gains are linear E=1, S=0.1, C=0.01, I=0, with a log2(rank+1) discount and cutoff 10. The reference orders the same candidates by product ID without lexical scores. Ties in BM25 use ascending product ID.

| Locale | Queries | ID-order reference | BM25 NDCG@10 | Absolute gain | BM25 95% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| English (`us`) | 2,088 | 0.5585 | 0.6945 | +0.1361 | [0.6858, 0.7039] |
| Spanish (`es`) | 563 | 0.5548 | 0.6826 | +0.1278 | [0.6621, 0.7015] |
| Japanese (`jp`) | 728 | 0.5753 | 0.7139 | +0.1386 | [0.6976, 0.7301] |
| Overall, query-weighted | 3,379 | 0.5615 | **0.6967** | **+0.1352** | **[0.6889, 0.7043]** |

The equal-weight mean of locale means is 0.6970 for BM25 and 0.5628 for the reference. Overall query weighting gives English more influence because it has more validation queries.

The paired 95% bootstrap interval for the overall improvement is **[+0.1261, +0.1445]**. Intervals use 2,000 resamples of locale-normalized query groups, with seed 20261101. There are 3,379 distinct normalized groups in this validation partition. No p-value is reported.

The declared hypothesis is supported against this ID-order control. The control is not a competitive search model. These development-set results must not be described as gains over an existing production ranker, dense retriever, published test baseline, or unseen final test set.

## What did not work for every query

| Locale | Improved over control | Tied | Worse than control | All candidate scores zero |
| --- | ---: | ---: | ---: | ---: |
| English | 1,389 | 78 | 621 | 46 |
| Spanish | 378 | 14 | 171 | 4 |
| Japanese | 503 | 40 | 185 | 29 |
| Total | 2,270 | 132 | **977** | **79** |

Win/tie/loss counts compare per-query NDCG with a tolerance of 1e-12. They are derived from the generated `per-query.csv` file. Zero-score queries fall back to the declared product-ID tie order; they are not dropped from the averages.

One Spanish query loses all tokens during analysis. No query has zero ideal DCG. These findings identify concrete cases for the next error-analysis step. They do not establish which change will improve them. Titles alone cannot use information that exists only in other product fields; adding fields would be a new, separately reported experiment.

## Local execution evidence

Machine: Apple M5 Pro, 24 GiB unified memory, 15 reported logical CPUs, as recorded in the project hardware audit. Runtime: Microsoft OpenJDK 21.0.11, Python 3.12.16, DuckDB 2.0.0; Maven 3.10.0 and Lucene 10.6.0 are pinned.

The first successful run built the index in **11.67 seconds**, then scored the complete candidate batch in **1.23 seconds**. The index occupied **102,531,114 bytes (97.78 MiB)**. The JVM heap limit was 4 GiB and DuckDB's memory setting 4 GB; neither is a measured peak resident-memory figure.

These timings include JVM startup, but exclude product/query export, input hashing, dependency build, bootstrap calculation and any HTTP/network serving path. They are single-machine batch observations, not QPS, p95/p99 latency, ingestion throughput or production-capacity claims. Warm filesystem caches and local resource contention may affect them.

## Reproduce

Prerequisites: a Java 21 JDK, Python 3.12, `uv`, and the pinned ESCI downloads/prepared splits from earlier steps. A separate Maven installation is unnecessary: the wrapper downloads a pinned distribution and checks its SHA-256. Java dependencies are downloaded on the first build.

```sh
uv sync --locked
./mvnw --batch-mode --no-transfer-progress verify
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
uv run --locked python scripts/run_bm25.py
```

The runner verifies the catalog and validation hashes, checks all locale/product and query/candidate counts, and builds the current Java source. It has no test-scoring partition option and never opens `test.parquet`. Source downloads and the prepared test set remain unchanged.

The default output is `data/processed/bm25-title-v1/`. It contains the local index, Base64 text exchange files, every candidate score, per-query metrics and a manifest. The compact aggregate report is intended for Git. A failed run does not publish its temporary output directory.

To repeat without replacing existing evidence:

```sh
uv run --locked python scripts/run_bm25.py \
  --output-dir data/processed/bm25-title-v1-repeat \
  --report data/processed/bm25-title-v1-repeat-report.json
```

A second independent full-catalog build was run with this command. Reproduction checks compare the exported inputs, all candidate scores, per-query metrics, aggregate scores and bootstrap intervals. Execution timing and internal Lucene files are not expected to be byte-identical across builds or machines.

## Verification

- Nine Java tests cover language analysis, Unicode/newline transport, locale-scoped IDs, literal query handling, zero matches, missing/duplicated candidates, length normalization, global statistics and overwrite protection.
- Twenty-seven Python tests cover the existing data protocol plus NDCG hand calculations, deterministic tie handling, complete candidate coverage, held-out-partition rejection, paired bootstrap behavior and an actual packaged-Java-to-Python integration test.
- CI builds Java first and requires the packaged jar integration test; a missing artifact fails CI instead of being silently skipped. Without a built jar, a Python-only local run skips that one integration test.
- CI uses small fabricated fixtures. Full ESCI indexing and evaluation run locally; raw data, indexes, build output and per-query artifacts remain outside Git.

The next increment is to review errors and define full-catalog retrieval evaluation using judged-result coverage and explicit unjudged-result limits. The official test holdout stays reserved.
