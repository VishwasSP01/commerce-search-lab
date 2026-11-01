# Hybrid retrieval: fixed RRF beats both single methods on validation

One predeclared fusion of title BM25 and multilingual dense results improves **known-Exact Recall@100 from 50.29% to 55.27%**, with a paired 95% improvement interval of **[+4.32, +5.59] percentage points** against BM25. It also beats dense alone. Every locale improves on the primary metric, so the predeclared rule nominates RRF as the next **offline development reference**. The official test remains unopened; this is not a serving rollout or a complete-relevance claim.

## Measured retrieval

All methods use the same **3,379 validation queries** and human labels. The underlying retrieval searches each query's own locale among **1,814,924 total locale-product titles**. Fusion combines the saved top 100 from each source, with no new search, encoding or model training.

| Known-Exact Recall@100 | BM25 | Exact dense | RRF | RRF minus BM25, paired 95% interval |
| --- | ---: | ---: | ---: | ---: |
| English, 2,088 queries | 46.98% | 48.47% | **52.54%** | +5.56 pp [+4.71, +6.41] |
| Spanish, 563 queries | 58.08% | 47.94% | **60.60%** | +2.53 pp [+1.17, +3.94] |
| Japanese, 728 queries | 53.77% | 53.53% | **58.98%** | +5.21 pp [+3.90, +6.59] |
| **Overall, query-weighted** | **50.29%** | **49.48%** | **55.27%** | **+4.98 pp [+4.32, +5.59]** |

RRF's marginal 95% interval is **[54.09%, 56.49%]**. Its paired improvement over dense is **+5.80 pp**, interval **[+5.17, +6.41] pp**. Equal-locale mean recall is **57.38%**; the headline weights queries equally. These are bootstrap development intervals without multiplicity adjustment, not final-test significance or p-values.

Against BM25, recall improves for **961 queries**, regresses for **441**, and ties for **1,977**. RRF retrieves **17,773** known Exact query-product pairs versus BM25's **16,307** and dense's **15,774**, out of 34,232 labeled Exact pairs. That pooled fraction is not the query-mean headline. Relative to BM25 it adds **2,564** known Exact pairs and loses **1,098**. A query can have both additions and losses; reporting only additions would conceal failures.

| Judgment coverage among actual returned top ten | BM25 | Dense | RRF |
| --- | ---: | ---: | ---: |
| English | 26.61% | 25.17% | 28.27% |
| Spanish | 42.46% | 28.86% | **40.36%** |
| Japanese | 43.61% | 37.12% | 43.78% |
| **Overall, query-weighted** | **32.91%** | **28.36%** | **33.63%** |

Overall coverage increases by **+0.71 pp**, paired interval **[+0.23, +1.24] pp**, but **Spanish coverage declines by 2.10 pp**. The primary-metric decision does not hide this secondary regression. Coverage is not precision: products without judgments retain unknown relevance and their actual ranks. RRF has 11,362 judged products across 33,790 top-ten slots, returns 100 products per query and has no empty/short lists.

## The 78 vocabulary-gap queries

Membership comes from the unchanged, checksummed original BM25 diagnostic: 3 Spanish, 29 Japanese and 46 English queries with `zero_kind=no_title_token_overlap`. That originally meant no query/title-token overlap among judged candidates, not no matches in the entire catalog. Its separate empty-analysis case is excluded. The new catalog measurement happens to show **zero known-Exact hits for BM25 throughout this fixed cohort**.

RRF retrieves **57 of the cohort's 799 known Exact pairs**, moving **19 of 78 queries** from zero known-Exact hits to at least one: 1 Spanish, 6 Japanese and 12 English. **59 queries still have none.** Mean known-Exact recall changes from 0 to **8.33%**; this does not mean 19 queries are completely fixed. Dense retrieves 58 pairs and has 8.44% mean recall; fusion drops one available English Exact product at the cutoff. No new human labels or score-based rescue definitions are introduced.

Across all validation queries, RRF recovers at least one known Exact for **191 of BM25's 570 zero-hit queries**. This broader diagnostic is distinct from the fixed 78-query cohort.

## Candidate opportunity versus ranking

The union of the two source top-100 lists contains **19,796 known Exact pairs**, with mean known-Exact coverage **60.52%**, across an average of 169.09 products per query. This is an **up-to-200-product candidate opportunity**, not a served Recall@100 result. RRF retains 17,773 of those pairs and drops **2,023** at the final cutoff. Complementary source candidates help, while fusion still makes ranking mistakes. Do not present the union's larger cutoff as a fair third top-100 method.

## How the fusion works

For each product, add `1 / (60 + rank)` for each source list containing it. Ranks start at one; absence contributes zero. Weights are equal, each source window is 100, and the final output is 100. For example, a product ranked second by BM25 and first by dense scores `1/62 + 1/61`; one ranked first only by BM25 scores `1/61`. Raw BM25 and cosine scores never enter the fusion formula. The constant controls the contribution dropoff with rank; **60 was fixed before measurement**, not selected by a sweep.

The implementation accumulates and sorts exact rational scores, breaks equal scores by ascending product ID, then writes binary64 scores. Query locale scopes all product identities, including IDs repeated across locales. BM25's empty lists remain empty inputs, rather than being padded. Dense's full lists ensure 100 unique fusion results. The fusion function accepts only ordered product-ID lists; labels are used afterward for evaluation. Every returned product must belong to the source union.

This is a local Python implementation of the [documented RRF formula](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion), not evidence of Elasticsearch serving. The [predeclared decision](decisions/0007-rrf-hybrid-retrieval.md) and [configuration](../config/catalog-rrf-v1.json) fix constants, metrics, cohort, comparisons and the nomination rule.

## Evaluation and evidence

Known-Exact Recall@100 divides retrieved labeled Exact products by all labeled Exact products for each query. Queries without known Exact products would be undefined/excluded and counted; none are excluded here. Judgment coverage uses actual returned top-ten length, with the established empty-list-zero convention. Unknown relevance stays unknown; there is no full-catalog NDCG or exhaustive-relevance claim.

Before fusion, the pipeline verifies saved input hashes and source/report bindings, identical query inputs, validation provenance and the original diagnostic membership. It reproduces both methods' per-query metrics. Comparisons require identical query IDs, normalized groups and known-Exact denominators. The paired bootstrap resamples the **same query groups together for both methods**, computes the mean difference on each draw, and uses its 2.5th/97.5th percentiles. It uses 2,000 draws and seed 20261101; all 3,379 normalized groups are distinct. This preserves pairing by query difficulty. The repeatedly inspected validation set still limits generalization.

The rule requires a positive overall paired lower bound versus **both** methods and no locale mean recall regression versus BM25. It passes. RRF is nominated for subsequent offline cost/quality comparisons; BM25 and dense remain preserved comparators. This decision does not open the official test, change a serving system, or assert that every query benefits.

Both fresh fusion/evaluation runs reproduce **all four artifact hashes**, every metric/interval, diagnostic, source provenance and the decision. All input artifact hashes remain unchanged after both runs. The [aggregate report](../reports/catalog-rrf-v1.json) and [repeat verification](../reports/catalog-rrf-v1-reproduction.json) contain the evidence. Detailed ranked lists, cohort membership/recovery records and per-query results stay under ignored `data/processed/`.

All **13 Java + 70 main Python/integration + 10 offline encoder/export/search tests pass** locally. Nine new tests cover hand-calculated scores, exact cutoff ties, score-scale independence, empty lists, malformed ranks, locale identity, unknown labels, actual additions/losses, cohort drift, paired identities and the nomination guardrails. Frozen sources and locks remain unchanged.

## Cost and reproduction

The first run records **0.99 seconds for fusion alone** and **10.18 seconds of main wall time**, including input verification, metrics/bootstrap and diagnostics. Maximum process RSS is **0.279 GiB**. The repeat takes 10.20 seconds. These observations operate on cached result lists; **end-to-end hybrid retrieval latency, concurrent capacity and serving memory remain unmeasured**. Main wall excludes interpreter/import startup and final report construction/hashing/serialization. It must not be added to unrelated earlier batch timings to manufacture a latency claim.

Initial measurement:

```sh
uv run --locked --no-sync python scripts/run_rrf.py
```

The default command now refuses the existing output/report. A fresh clone also has the tracked public report, so reproduction needs fresh paths. With the saved BM25, dense and diagnostic bundles and prepared validation data available:

```sh
uv run --locked --no-sync python scripts/run_rrf.py \
  --output-dir data/processed/catalog-rrf-v1-local \
  --report data/processed/catalog-rrf-v1-local-report.json
```

The independent repeat used `catalog-rrf-v1-repeat` and `catalog-rrf-v1-repeat-report.json`. This replay repeats fusion/evaluation, not upstream retrieval or an embedding restart test. No new dependencies, model downloads, catalog scans or product/query re-encoding are required.

The next separate increment should measure ANN agreement with the exact dense reference, known-Exact retrieval and memory/runtime cost, then an int8 comparison under a declared protocol. Approximation and compression can affect the hybrid gain; neither is assumed free or automatically four times smaller in total serving memory. The final test remains reserved.
