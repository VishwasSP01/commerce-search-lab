# Decision 0003: full-catalog BM25 under incomplete judgments

Status: predeclared before measurement.

Candidate reranking answers how well we order a supplied judged set. The next question is whether title BM25 finds known Exact products when searching the catalog. Keep the existing candidate benchmark, but do not transfer its NDCG to full-catalog retrieval claims.

Search all pinned ESCI products **within the query's locale**, not just its judged candidates. The three locale corpora total 1,814,924 records. This is multilingual, locale-routed search, not cross-language retrieval. Keep the same title analysis, Unicode normalization, literal OR query and fixed `k1=1.2`, `b=0.75`. Return up to 100 lexical matches, without zero-match padding. Ties, including at rank 100, use ascending product ID.

Build a separate index with explicit locale and sorted product-ID fields to make scope and tie-breaking independent of insertion order. Keep the frozen Java baseline source and historical index untouched. Before measurement, use the unchanged baseline candidate scorer on the new index and require its score file to match the frozen artifact exactly. This checks that the added filter/sort metadata has not changed title statistics or scoring. Export retrieval input containing only locale, query ID and query text; labels and candidate IDs never enter the retrieval process.

## Metrics and aggregation

- **Known-Exact Recall@100 (primary):** the number of unique returned products labelled E for this query divided by all products labelled E for this query. The denominator is known positives, not every truly relevant catalog product. Queries with no E judgment have undefined recall: store null, exclude them from recall means/intervals and report their count. A query with E judgments and no hits scores zero.
- **Judgment coverage at 10 (secondary):** among the actual first `min(10, returned)` products, the fraction with any ESCI judgment, including I. Empty lists score zero by explicit convention. Also publish returned counts, top-ten slot-fill fractions, empty-list counts and short-list counts so a high coverage value cannot hide very short lists. This measures label coverage, not precision or relevance.
- Do not label unjudged products irrelevant or compress them out of the ranking. Do not publish a headline full-catalog NDCG estimate that assumes unjudged products are irrelevant. New human judgments would require a separate pooled sampling and assessor protocol.
- Publish query-weighted and per-locale means, plus an equal-locale macro mean. Compute 95% percentile intervals from 2,000 resamples of locale-normalized query groups with seed 20261101, retaining all member IDs. Recall excludes undefined observations; coverage includes every query. Publish denominator counts.

Use the fixed 3,379-query validation artifact only. Preserve the official test set. This is development evidence for later BM25/dense/fusion comparisons; incomplete judgments can favor methods resembling those that collected the original pool. Exact-recall and coverage alone cannot establish all-catalog relevance or business impact. There is one baseline here, so a paired significance comparison is not applicable yet. A later relevance-eval bridge must preserve ESCI gain values and exact query identities.

## Evidence and completion

Commit this rule before running the catalog benchmark. Verify pinned products, validation and frozen source/artifact hashes. Check every returned identity belongs to its locale catalog, query completeness, contiguous ranks, uniqueness, finite scores, deterministic score/ID order, cutoff and status consistency. Record label-free input, all returned products and zero-result query status locally; keep only compact aggregate results in Git.

Test actual Java retrieval for locale routing, unjudged-product eligibility, score/ID cutoff ties independent of document order, short/empty queries and Unicode transport. Test recall denominators and unknown-label behavior in Python. Run two independent builds and require score/metric reproduction. Report build/query-batch time, index bytes and configured resource limits without claiming API capacity or peak memory. Publish a bounded result and focused commits/PR; dense modeling and Elasticsearch serving remain separate increments.
