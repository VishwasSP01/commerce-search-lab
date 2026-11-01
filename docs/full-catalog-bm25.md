# Full-catalog title BM25: measured validation retrieval

**The baseline finds an average of 50.29% of each query's known Exact products within the first 100 results.** Mean judgment coverage among the returned top ten is **32.91%**. These are separate retrieval and label-coverage measurements; the second number is not precision.

The [protocol](decisions/0003-full-catalog-evaluation.md) and [configuration](../config/catalog-bm25-title-v1.json) were committed in `cb175d1` before measurement. The [aggregate report](../reports/catalog-bm25-title-v1.json) contains full precision, bootstrap intervals, fingerprints and artifact hashes. No improvement over another catalog retriever is claimed; this establishes the comparison baseline for later dense and fusion experiments. The official test set remains unread and unscored.

## What is searched

The index contains every **1,814,924** pinned ESCI locale-product record. Each validation query searches all products in its own locale:

| Locale | Catalog records | Validation queries |
| --- | ---: | ---: |
| English (us) | 1,215,854 | 2,088 |
| Spanish (es) | 260,011 | 563 |
| Japanese (jp) | 339,059 | 728 |

This is locale-routed multilingual retrieval, not cross-language search. The retrieval query contains no judged-candidate restriction. Its input protocol includes only locale, query ID and Base64 query text; judgments are used afterward by Python evaluation.

The ranker retains the frozen title-only analyzers, Unicode NFKC, literal OR query and BM25 **`k1=1.2`, `b=0.75`**, with default overlap discounting. A separate index adds an explicit locale filter field and sorted product-ID values. Results sort by descending score and ascending product ID, including ties at rank 100. Zero-match products are never appended to fill a list.

Before full retrieval, the unchanged candidate scorer evaluated the new index and reproduced the frozen **77,934 candidate score rows byte-for-byte**. Its original per-query metrics also match exactly. Added scope/sort metadata has therefore preserved scoring on every original judged candidate; the frozen source and original index remain unchanged.

## Metric definitions

**Known-Exact Recall@100** is `retrieved products labelled E / all products labelled E for this query`. It measures finding known Exact products, not exhaustive recall over every truly relevant catalog item. Queries without E judgments would have null recall, be excluded from the recall mean/interval and be counted explicitly. In this validation set, all 3,379 queries have at least one E judgment, so none are excluded. Empty results with known E judgments score zero.

**Judgment coverage at 10** is `judged products among the actual top ten / min(10, returned products)`. Any ESCI label, including I, counts as judged. Empty lists score zero by declared convention. Because short lists can otherwise look deceptively well covered, the report also publishes returned counts, top-ten slot fill and short/empty-list counts. This denominator follows the project's explicit protocol; do not assume every library uses the same empty/short-list convention for a metric named Judged@10.

Unjudged products keep their returned positions and unknown relevance. They are not relabelled Irrelevant or removed before calculating coverage. No headline full-catalog NDCG is reported under an implicit unjudged-as-irrelevant assumption. The previous **0.6967 candidate-ranking NDCG@10** belongs to a different evaluation track and is not numerically comparable with this recall result.

## Results

All 3,379 validation queries are included. Values below are percentages; intervals are 95% percentile bootstrap intervals.

| Locale | Known-Exact Recall@100 | Recall interval | Judgment coverage at 10 | Coverage interval |
| --- | ---: | --- | ---: | --- |
| English | 46.98% | [45.46%, 48.54%] | 26.61% | [25.45%, 27.76%] |
| Spanish | 58.08% | [55.24%, 60.74%] | 42.46% | [39.91%, 45.16%] |
| Japanese | 53.77% | [51.26%, 56.36%] | 43.61% | [41.14%, 46.20%] |
| Overall, query-weighted | **50.29%** | **[49.07%, 51.50%]** | **32.91%** | **[31.86%, 33.93%]** |

The equal-locale macro means are 52.94% recall and 37.56% coverage. English has more queries and therefore more influence on the primary query-weighted aggregate. Intervals resample locale-normalized query groups 2,000 times with seed 20261101, retaining all member IDs. All 3,379 groups are distinct here. These intervals do not account for unknown missing judgments or dataset-selection bias. There is no method comparison in this baseline run, so no paired p-value is reported.

| Diagnostic | English | Spanish | Japanese | Total |
| --- | ---: | ---: | ---: | ---: |
| Query-product pairs labelled Exact | 18,064 | 6,384 | 9,784 | 34,232 |
| Those pairs retrieved at 100 | 7,830 | 3,507 | 4,970 | 16,307 |
| Queries with at least one known Exact hit | 1,656 | 514 | 639 | 2,809 |
| Empty result lists | 15 | 3 | 8 | 26 |
| Fewer than 10 results, including empty | 37 | 10 | 32 | 79 |
| Fewer than 100 results, including empty | 56 | 39 | 85 | 180 |

The primary recall is a **mean of per-query fractions**, not `16,307 / 34,232`, which would weight queries by their number of Exact judgments. Across the top ten there are 33,242 returned slots, of which 10,910 have judgments. The primary coverage likewise averages query-level fractions rather than pooling slots. Mean top-ten slot fill is 98.38%; low coverage is not explained by mostly empty lists. One query has empty analyzer output; the remaining 25 empty lists have no lexical match in their locale catalog.

## Interpretation and limits

Title BM25 leaves substantial room to retrieve more known Exact products. English has the lowest observed mean recall, but corpus sizes and query/judgment distributions differ; the table does not isolate a causal language effect. Future comparisons must use exactly the same corpus scope, queries, cutoffs, label policy and tie handling.

Most top-ten results have no judgment. They may be useful or irrelevant; this dataset does not tell us which. A dense method might find valid products outside the original judgment pool, so known-positive recall and coverage are useful but insufficient for a complete relevance claim. Keep them separate. Additional relevance assessment would require a separately specified pooling, sampling and human-assessor process.

These are validation-development results after prior error analysis. They establish neither held-out generalization nor user/conversion lift. Candidate-ranking improvements, full-catalog retrieval and serving performance remain distinct claims. The unsuccessful bullet variant remains rejected; this run uses titles only.

## Local resource evidence

The existing Apple M5 Pro / 24 GiB machine ran the full experiment without paid services. The first index build took **12.731 s**, the frozen candidate-parity batch **1.167 s**, and the full retrieval batch **3.579 s**. The new index occupies **115,630,862 bytes (110.27 MiB)**, including explicit locale and sorting fields.

Java used a 4 GiB heap cap and DuckDB a 4 GB memory setting. These are configured limits, not measured peak memory. Batch times include JVM startup but exclude hashing, Maven, Python evaluation and bootstrap; the verified title export was reused. Local filesystem caches and shared-machine contention can affect timings. These numbers do not establish API p95/p99, QPS, concurrent capacity or production scale.

An independent rebuild took 13.459 s and its retrieval batch 3.612 s. Both builds reproduce all six exchanged artifact hashes, every per-query metric, aggregate and confidence interval exactly. See the [repeat verification record](../reports/catalog-bm25-title-v1-reproduction.json); internal Lucene files and timing are not required to be byte-identical.

## Reproduction and verification

Prerequisites are the pinned raw metadata, prepared validation artifact and original local baseline exchange files. If absent, follow the [baseline reproduction](bm25-baseline.md) first. No separate Maven installation is required.

```sh
uv sync --locked
./mvnw --batch-mode --no-transfer-progress verify
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
uv run --locked --no-sync python scripts/run_catalog_bm25.py
```

The runner rejects an existing output directory, checks pinned inputs and frozen sources, verifies score parity, and validates every returned identity against its locale catalog. It checks complete query status including zero-result queries, rank continuity, duplicate products, finite scores, score/ID order, analysis parity and cutoffs. It only opens the validation artifact, never the official test file.

All bulk artifacts stay in ignored `data/processed/catalog-bm25-title-v1/`: the new index, label-free query input, candidate-parity scores, complete retrieval results, query status, per-query metrics and a manifest. Only compact reports and code are committed.

```sh
uv run --locked --no-sync python scripts/run_catalog_bm25.py \
  --output-dir data/processed/catalog-bm25-title-v1-repeat \
  --report data/processed/catalog-bm25-title-v1-repeat-report.json
```

Verification passes **13 Java and 48 Python/integration tests**. New checks cover locale routing, insertion-order-independent cutoff ties, frozen-score parity, empty/short lists, unjudged result eligibility, Unicode transport, known-Exact denominators, undefined recall, grouped bootstrap and locale-catalog membership including numeric-looking IDs. CI requires the packaged Java integrations and uses small fabricated fixtures, not ESCI downloads.

The next proposed increment is a bounded multilingual embedding compatibility/resource pilot, followed by a controlled dense and fusion comparison if the pilot fits the zero-budget resource envelope. No model is downloaded or embedded in this increment.
