# Title-plus-bullet BM25: validation experiment

**Decision: retain title-only BM25.** The one predeclared variant, `title score + 0.25 × bullet score`, did not meet its adoption rule. Overall validation NDCG@10 decreased slightly from **0.696695 to 0.696164**. The paired 95% delta interval spans zero, so this is neither evidence of an improvement nor a statistically established regression.

The [configuration](../config/bm25-title-bullet-v1.json) and [decision](decisions/0002-title-bullet-ablation.md) were committed before measurement in `4033650`. The weight was not changed after observing results. See the [aggregate report](../reports/bm25-title-bullet-v1.json) for full precision, provenance, artifact hashes and selected extreme cases.

## Controlled comparison

Both systems use the same 1,814,924 locale-product catalog keys, 3,379 validation queries, 77,934 judged candidates, analyzers, normalization, BM25 parameters, gains and tie-breaking. The title scorer, its source fingerprints, query input, candidate scores and per-query metrics reproduce the frozen baseline exactly. The official test artifact is never opened.

The new auxiliary index holds bullet text and reuses the unchanged Java ranker. Its internal `title_<locale>` field names contain bullets in this index. Null bullets become empty text; no product is removed. Each index has independent full-catalog field statistics. Scores are combined with Python float64 arithmetic from serialized Lucene float32 values. This is **weighted BM25 over independent fields, not BM25F**, and the two-index arrangement is an offline experiment, not a serving architecture.

| Locale | Title only | Title + 0.25 × bullets | Delta | Paired 95% delta interval |
| --- | ---: | ---: | ---: | --- |
| English (us) | 0.694509 | 0.694080 | −0.000429 | [−0.004288, +0.003426] |
| Spanish (es) | 0.682581 | 0.683798 | +0.001217 | [−0.005706, +0.008225] |
| Japanese (jp) | 0.713879 | 0.711705 | −0.002175 | [−0.007016, +0.002543] |
| Overall, query-weighted | 0.696695 | 0.696164 | −0.000531 | [−0.003199, +0.002264] |

The equal-locale macro mean changes from 0.696990 to 0.696527. Across query IDs, 1,250 improve, 1,281 regress and 848 tie (tolerance 1e-12). Intervals use 2,000 paired percentile bootstrap samples of locale-normalized query groups, seed 20261101; there are 3,379 distinct groups. They describe sampling uncertainty on this development set, not all sources of experimental uncertainty. No p-value is claimed.

The predeclared adoption rule required an entirely positive overall paired interval and no negative locale mean delta. Both conditions fail. Do not infer that every bullet weighting, BM25F implementation or learned field model would fail; none of those alternatives was tested.

## Diagnostic effects and case inspection

These cohorts were identified in the earlier validation error analysis and remain development diagnostics. Their improvements cannot override the all-query result.

| Frozen baseline cohort | Queries | Title only | Variant | Delta | Improved / regressed / tied |
| --- | ---: | ---: | ---: | ---: | --- |
| All-zero title scores | 79 | 0.629762 | 0.627350 | −0.002413 | 4 / 5 / 70 |
| Losses against product-ID order | 977 | 0.547867 | 0.554354 | +0.006487 | 415 / 365 / 197 |

Bullet matches reduce all-zero query scores from 79 to 69. This does **not** mean ten ranking improvements: only four queries in that cohort gain NDCG, five lose and seventy tie. One query can gain a positive score while keeping the same ranking or NDCG. The baseline-loss cohort improves with a paired delta interval of [+0.001115, +0.011931]; the zero-score cohort interval is [−0.017761, +0.010964]. Neither is an independent held-out success estimate.

The report deterministically lists the ten largest gains and ten largest regressions by NDCG delta, then locale and query ID. A focused inspection of the top two on each side found:

- **es / 77334:** patio pallet furniture gains from 0.561894 to 1.0. Bullet text supplies outdoor-use context for Exact candidates. The title ranking already starts with Exact products; NDCG measures the entire top ten, not just the first result.
- **us / 16164:** the brand/design query gains from 0.105740 to 0.592472, yet the first two products remain labelled Irrelevant. A large metric gain does not imply that the query is solved.
- **us / 56706:** a branded lounging-pants query falls from 1.0 to 0.335477 as Substitute products overtake the Exact result. A small nominal auxiliary weight does not guarantee title dominance when field score scales differ.
- **us / 36117:** the DVD query falls from 0.666164 to 0.087614. Films labelled Exact have empty bullets; DVD players labelled Complement have extensive matching bullets and rise above them. Additional vocabulary can reinforce the wrong product type.

These are one coding assistant's qualitative inspections of original labels and metadata, not independent human adjudication or estimates of failure prevalence. No labels or queries were changed. Full source text remains in local datasets rather than being copied into Git.

## Coverage and resource costs

| Locale | Catalog records | Null bullets | Blank non-null bullets | Unique validation products | Null bullets among those products |
| --- | ---: | ---: | ---: | ---: | ---: |
| es | 260,011 | 36,977 | 0 | 14,687 | 1,933 |
| jp | 339,059 | 88,363 | 4 | 20,536 | 4,322 |
| us | 1,215,854 | 178,776 | 0 | 40,459 | 4,917 |

All unique validation products have zero blank non-null bullets under DuckDB's `trim(text) = ''` check. Textual presence does not guarantee analyzed tokens or query matches. The auxiliary index still contains every catalog key, including null/blank metadata.

The first run on the existing Mac used the same 4 GiB Java heap cap and 4 GB DuckDB setting. These are configured limits, not measured peak memory.

| Measurement | First run |
| --- | ---: |
| Bullet export plus validation read | 7.829 s |
| Frozen-title rescoring batch | 1.645 s |
| Auxiliary index build | 41.535 s |
| Bullet candidate-scoring batch | 1.435 s |
| Score-file combination | 0.153 s |
| Reused title index | 102,531,114 bytes (97.78 MiB) |
| Auxiliary bullet index | 312,024,366 bytes (297.57 MiB) |
| Total two-index storage | 414,555,480 bytes (395.35 MiB; 4.04× title-only) |

The title build is reused. Java batch times include JVM startup; stage timings exclude hashing, Maven, metric evaluation and bootstrap. Combining scores includes file I/O. These single-machine batches do not establish API latency, concurrent capacity, p95/p99, QPS or production-scale behavior. Independent indexes also duplicate key storage, so these bytes do not predict a future unified-field implementation.

## Reproduction and verification

With the pinned raw data, prepared validation artifact and original local baseline index available:

```sh
./mvnw --batch-mode --no-transfer-progress verify
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
uv run --locked --no-sync python scripts/run_title_bullet.py
```

If the original baseline artifacts are absent, reproduce them first using the [baseline instructions](bm25-baseline.md). The experiment refuses existing output directories, verifies raw-data and baseline-source hashes, reconstructs the exact query input, reproduces all baseline per-query metrics, reruns the title scorer and requires its score artifact hash to match. It checks complete candidate coverage in both fields before combining. Generated inputs, indexes, scores, per-query metrics and manifests stay under ignored `data/`.

Repeat with fresh paths:

```sh
uv run --locked --no-sync python scripts/run_title_bullet.py \
  --output-dir data/processed/bm25-title-bullet-v1-repeat \
  --report data/processed/bm25-title-bullet-v1-repeat-report.json
```

The new tests exercise actual multilingual bullet scoring through the packaged Java jar, empty auxiliary metadata, zero-score candidate retention, identity-based score joining, invalid/duplicate/missing scores, analyzer mismatch, paired-comparator identity and the adoption gate. Existing tests continue to protect the frozen analyzers and global-statistics behavior. CI requires the packaged integration tests and uses only small fabricated fixtures.

An independent second export, auxiliary-index build and scoring run reproduced all seven exchanged artifact hashes, every aggregate and per-query metric, every bootstrap interval, the extreme-case selection and the failed adoption decision exactly. See the [repeat verification record](../reports/bm25-title-bullet-v1-reproduction.json). Execution times are excluded from equality checks, as are internal Lucene file identities. Local verification passes nine Java and forty-one Python/integration tests.

## Limits and next boundary

This is judged-candidate reranking, with global catalog statistics; it does not measure exhaustive full-catalog retrieval relevance. Validation guided the hypothesis, so these results are development evidence. The official test remains reserved. Retain title-only, record the unsuccessful variant, and define an explicit incomplete-judgment evaluation protocol before making full-catalog or neural-retrieval quality claims.
