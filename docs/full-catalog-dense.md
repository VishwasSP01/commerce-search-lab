# Exhaustive dense retrieval on the real catalog

The fixed multilingual E5-small model **does not demonstrate an overall retrieval gain over title BM25** on validation. Keep BM25 as the baseline. Dense gains in English, loses substantially in Spanish, and is close to BM25 in Japanese. These are useful measured outcomes, not a reason to hide or retune the experiment.

## Results

Every query searches all products in its own locale. The three corpora total **1,814,924 locale-product titles**. The same **3,379 validation queries** and human labels are used for both methods; the official test set remains reserved.

| Known-Exact Recall@100 | Title BM25 | Exact dense | Dense minus BM25 | Paired 95% interval, percentage points |
| --- | ---: | ---: | ---: | ---: |
| English, 2,088 queries | 46.98% | 48.47% | +1.50 pp | [+0.32, +2.69] |
| Spanish, 563 queries | 58.08% | 47.94% | −10.14 pp | [−12.48, −7.75] |
| Japanese, 728 queries | 53.77% | 53.53% | −0.24 pp | [−2.27, +1.81] |
| **Overall, query-weighted** | **50.29%** | **49.48%** | **−0.81 pp** | **[−1.77, +0.11]** |

The overall dense mean has a marginal 95% interval of **[48.26%, 50.66%]**. The paired difference interval includes zero: this experiment establishes neither an overall gain nor a definitive overall loss. The language slices are development diagnostics, not multiplicity-adjusted confirmatory claims. Equal-locale mean recall is **49.98%** for dense; the headline weights queries equally.

Dense improves recall for **917 queries**, regresses **1,091**, and ties **1,371**. It finds **15,774 of 34,232 known Exact query-product pairs**; that pooled fraction is not the headline query-mean recall. Dense returns 100 products for every query; there are no empty/short lists. BM25 has 26 empty lists and 180 lists shorter than 100.

| Judgment coverage among actual returned top ten | Title BM25 | Exact dense |
| --- | ---: | ---: |
| English | 26.61% | 25.17% |
| Spanish | 42.46% | 28.86% |
| Japanese | 43.61% | 37.12% |
| **Overall, query-weighted** | **32.91%** | **28.36%** |

Dense's coverage difference is **−4.55 pp**, paired 95% interval **[−5.32, −3.74] pp**. It returns 9,583 judged products across 33,790 top-ten slots. **Coverage is not precision:** the remaining products have unknown relevance. Lower coverage cannot establish that dense's unknown results are bad. We retain unknown products at their actual ranks and never relabel them as Irrelevant.

## What was searched

The [completed product export](full-catalog-embeddings.md) is reused unchanged. The model is `intfloat/multilingual-e5-small`, pinned at `614241f622f53c4eeff9890bdc4f31cfecc418b3`. Product input is NFKC title text with `passage: `; queries use NFKC text with **`query: ` in every language**. Both use the frozen encoder, max length 128, batch size 32, float32, eager attention, masked mean pooling, L2 normalization and four CPU threads. Query encoding uses MPS on this machine, with unsupported-operation fallback disabled. No product is re-encoded, and no weights, model settings or query parameters are tuned.

All 3,379 query vectors are finite and unit-normalized within 1e-5. **No queries are truncated**; the longest untruncated prefixed input has 42 tokens. Query vectors occupy 5,190,144 bytes. Every product shard's checksum, norms, locale/ID alignment, checkpoint binding, strict key order and final sequence digests are verified against the prior completed export.

Retrieval runs exhaustive **CPU float32 inner products**, using four PyTorch threads. Because both sides are unit-normalized, these are cosine similarities within normalization/rounding error. “Exact” means every locale product is scored, with no ANN candidate loss. It does not mean infinite precision or complete relevance labels.

The scanner holds one existing 4,096-product shard and at most 128 queries per score matrix, retaining a running top 100. Queries from other locales never enter that shard's search. It ranks by descending computed score, then ascending product ID. All candidates tied at a partition cutoff participate in ID tie-breaking, including shard merges; there is no tolerance-based tie grouping. Negative scores are valid. The retriever receives query identities/text/vectors, never judgment candidate lists.

## Evaluation and uncertainty

The [predeclared protocol](decisions/0006-exact-dense-retrieval.md) and [configuration](../config/catalog-dense-exact-v1.json) were committed before measurement. Known-Exact Recall@100 divides retrieved judged Exact products by all judged Exact products for each query; queries with none are undefined/excluded from that mean and counted separately. There are no such exclusions in this validation set. Judgment coverage divides judged products by the actual returned top-ten length; empty lists use zero. Short-list and slot-fill reporting prevent denominator ambiguity.

The same metric definitions as [catalog BM25](full-catalog-bm25.md) are reused. The pipeline verifies the identical query-input checksum, reproduces BM25's saved per-query metrics, and requires matching query IDs, normalized groups and known-Exact denominators before pairing. Marginal intervals and paired dense-minus-BM25 intervals use 2,000 percentile bootstrap resamples of locale-normalized query groups, seed 20261101. All 3,379 groups are distinct in this set. This is development evidence with incomplete judgments; there is no p-value, catalog NDCG, exhaustive human quality, cross-language retrieval or online-impact claim.

## Cost and verification

The first local run records **39.71 seconds** of main wall time, including input/model verification, model load, fresh query encoding, exhaustive scan, evaluation/bootstrap and artifact hashing. The scan with catalog verification takes **32.38 seconds**, of which **27.67 seconds** are matrix multiplication/top-k work. This is a batch observation on the existing Mac, not API latency or QPS.

Maximum kernel process RSS is **0.974 GiB**, including encoding/model load and retrieval. Observed MPS driver allocation reaches **1.073 GiB** during query encoding; it overlaps unified-memory accounting and is neither additive to RSS nor a true GPU peak. Raw product vectors remain **2.596 GiB**; this scanner loads bounded shards rather than materializing a full in-memory index. ANN overhead and quantized serving cost are still unmeasured. Main wall excludes interpreter/import startup and final report serialization. The source report records each timing and backend precisely.

The fixtures compare blockwise retrieval with exhaustive sorting and an independent float64 oracle, cover negative scores/cutoff ties and detect corrupt vectors, IDs, manifests, wrong locales and missing metric rows. All **13 Java, 61 main Python/integration and 10 offline encoder/export/search fixture tests pass**. The [aggregate report](../reports/catalog-dense-exact-v1.json) contains metrics, intervals, resource observations, model/source bindings and artifact hashes. The [independent-repeat record](../reports/catalog-dense-exact-v1-reproduction.json) verifies that both fresh runs reproduce all five artifact hashes (including query vectors and ranked results), every metric/interval and source provenance. Both runs encode queries afresh and reuse the unchanged product bundle; timing/memory observations may differ. This is same-machine reproducibility, not a cross-platform bitwise guarantee.

## Reproduce

The initial measurement used the previously prepared data, model cache and completed product export:

```sh
uv run --locked --no-sync --project experiments/embedding-pilot python experiments/embedding-pilot/run_dense.py
```

That default command now refuses the existing evidence. The published report is tracked, so a fresh clone also needs a different report path. For an independent repeat, including fresh query encoding, use fresh paths such as:

```sh
uv run --locked --no-sync --project experiments/embedding-pilot python experiments/embedding-pilot/run_dense.py \
  --output-dir data/processed/catalog-dense-exact-v1-repeat \
  --report data/processed/catalog-dense-exact-v1-repeat-report.json
```

The original export must exist with the published contract/manifest and bound sources. Bulk query vectors, ranked results, IDs, model weights and datasets stay ignored. No dependency or frozen encoder/export source changes are required.

## Decision

Retain title BM25; dense alone is not an overall replacement. English gains and per-query wins suggest possible complementary candidates, but they do not prove fusion will improve retrieval. The next separate increment is one predeclared RRF comparison on these saved result lists, reporting language regressions and actual known-Exact recovery in the fixed vocabulary-gap cohort. ANN and int8 cost experiments follow once the retrieval comparison is understood. The Spanish regression's cause remains unproven; do not explain it with an invented model or language-data story.
