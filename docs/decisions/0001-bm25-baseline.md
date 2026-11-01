# 0001: First BM25 experiment

Status: accepted before the first full-catalog run, 1 November 2026.

## Question and scope

Does a fixed, title-only BM25 ranker improve validation NDCG@10 over a deterministic product-ID-order reference? This is judged-candidate ranking, not full-catalog retrieval relevance or serving performance.

Use the existing 3,379 validation query IDs and all their 77,934 judgments. Do not read the official test artifact. BM25 needs no supervised training; indexing and scoring receive no relevance labels.

## Decision

- Use Java 21 and Apache Lucene 10.6.0 directly. A local index avoids introducing an Elasticsearch service, HTTP API or distributed infrastructure into this increment.
- Index all 1,814,924 pinned locale-product title records in one index, with separate `title_us`, `title_es`, and `title_jp` fields. Thus BM25 collection statistics come from the full catalog for the relevant locale, not each query's judged candidate list.
- Apply Unicode NFKC, then default Lucene EnglishAnalyzer, SpanishAnalyzer, or JapaneseAnalyzer to both titles and queries. JapaneseAnalyzer uses its default SEARCH mode. Documents from other locales do not contribute to that title field's statistics.
- Use Lucene BM25Similarity with fixed `k1=1.2`, `b=0.75`, and its default overlap discounting. No parameter search, extra fields, boosts, or query rewriting.
- Construct literal analyzed OR queries through QueryBuilder, retaining its default synonym/graph handling. Do not interpret user query strings as Lucene query syntax.
- Filter scoring to every supplied judged candidate; require all candidates to exist in the index. Preserve zero-score candidates, including when analysis produces no query terms.
- Sort by descending score, then ascending product ID. This rule is independent of labels and input row order. The reference ranks the same candidates by product ID only; it is a control, not a competitive retrieval baseline.

## Evaluation contract

Linear gains are E=1, S=0.1, C=0.01, I=0, matching the pinned upstream training gain mapping. Compute DCG as the sum of gain/log2(rank+1), truncate at 10, and divide by ideal DCG for the same complete judged candidate list. Define zero ideal DCG as NDCG=0 and report its count. Do not exponentiate the gains.

The primary aggregate is the mean over validation query IDs. Also report each locale and an equal-weight mean of locale means. Use a paired percentile bootstrap of normalized query groups for 95% intervals of BM25 and its difference from the reference: 2,000 resamples, seed 20261101. Matching normalized variants stay together in resampling. No significance p-value is claimed.

The model configuration and hypothesis are fixed in [config/bm25-title-v1.json](../../config/bm25-title-v1.json) before looking at results. Validation results are development evidence, not final held-out performance. Preserve losing cases and report lexical zero-match counts.

## Reproduction and consequences

Provide a pinned, checksum-verified Maven wrapper so a separate Maven installation is unnecessary. Pin Java dependencies and relevant build plugins. Keep TSV exchange files, Lucene indexes, scores and per-query evidence under ignored data directories. Base64-encoded UTF-8 text preserves tabs, newlines and Japanese text without adding a JSON library to the Java component.

The runner verifies product and validation hashes, validates row/candidate coverage, builds current Java sources, and publishes only after a successful complete run. Scoring rules have focused Java and Python tests; CI uses fabricated fixtures. Repeated runs use fresh output directories to preserve the earlier evidence.

Catalog text may include products judged for test queries; this is an existing-catalog, query-held-out experiment. Test query text/labels are not used by this baseline runner. Future larger-data model training must still respect the validation and official test holdouts.

Record index size, build time, and candidate-scoring batch time with hardware/runtime metadata. These local batch timings include JVM startup and are not API p95/p99, QPS, or distributed-capacity measurements. Title-only matching may miss information in descriptions and attributes; investigate errors before changing the baseline.

## Sources

- [Pinned ESCI training gain mapping](https://github.com/amazon-science/esci-data/blob/7916cdf6ab75a462e77f20ab40428a10923998d5/ranking/train.py)
- [Lucene 10.6.0 BM25Similarity source](https://github.com/apache/lucene/blob/releases/lucene/10.6.0/lucene/core/src/java/org/apache/lucene/search/similarities/BM25Similarity.java)
- [Lucene 10.6.0 JapaneseAnalyzer source](https://github.com/apache/lucene/blob/releases/lucene/10.6.0/lucene/analysis/kuromoji/src/java/org/apache/lucene/analysis/ja/JapaneseAnalyzer.java)
- [Apache Maven Wrapper documentation](https://maven.apache.org/tools/wrapper/)
