# BM25 validation error analysis

Completed on 1 November 2026. The ranker, parameters, gains, input scores and official test holdout are unchanged. This is a diagnosis of the [published title-only baseline](bm25-baseline.md), not a new relevance experiment.

See the [analysis policy](../config/bm25-error-analysis-v1.json), [aggregate evidence](../reports/bm25-error-analysis-v1.json), and [18 qualitative case notes](bm25-case-review.csv).

## Scope and method

Analyze all 3,379 validation queries and 77,934 judgments, including wins and ties as well as failures. Join the 75,682 distinct candidate products to their locale-specific metadata. Verify source/input hashes and baseline code fingerprints, then recompute the baseline NDCG before diagnosing it.

The Java diagnostic bridge calls the unchanged `Bm25Cli.analyzer()` and `Bm25Cli.tokens()` methods from the packaged ranker. It analyzes 222,029 distinct locale/text records across queries, titles, descriptions, bullet points, brands and colors. Query token counts must agree with the original scoring run. A positive BM25 score without any shared title token fails verification.

The analysis opens only the prepared validation judgments, pinned product metadata and verified baseline score artifacts. It does not open `test.parquet`, change any relevance judgment, or search the full catalog for new candidates.

## Zero lexical scores: 79 queries, two different problems

| Locale | All candidate scores zero | Empty query after analysis | No title token overlap |
| --- | ---: | ---: | ---: |
| English | 46 | 0 | 46 |
| Spanish | 4 | 1 | 3 |
| Japanese | 29 | 0 | 29 |
| Total | **79** | **1** | **78** |

All 79 have at least one Exact or Substitute judgment. In the 78 nonempty cases, no analyzed query token occurs in any judged candidate's title. No zero-score case showed token overlap but failed the baseline query's matching logic.

The empty Spanish query is a phrase that also appears in an Exact-labeled book title; all its words are removed by the default analyzer. This supports investigating a named-title fallback separately. One case does not justify globally disabling stopword removal.

Zero scores do not necessarily mean NDCG is zero: the baseline retains every candidate and uses its fixed product-ID tie order. These 79 cases are ties against that control. They also do not establish that the entire catalog has no lexical match, because only the judged candidates were scored.

## What the available metadata can and cannot suggest

For diagnostics only, E/S means Exact or Substitute and I means Irrelevant. Complements are kept separate; the original graded evaluation still uses E=1, S=0.1, C=0.01, I=0.

Across the zero-score cohort, **18 of 79 queries** have at least one shared query token in an E/S candidate's extra metadata. Twelve have all unique query tokens covered within one extra field. Sixty nonempty queries have no shared term in any tested extra field of any E/S candidate; the remaining query is the empty-analysis case.

| Extra field | Zero-score queries with E/S overlap | Zero-score queries with I overlap | Loss queries with new E/S terms | Loss queries with new I terms |
| --- | ---: | ---: | ---: | ---: |
| Description | 9 | 4 | 583 | 182 |
| Bullet points | 8 | 4 | **715** | **238** |
| Brand | 5 | 2 | 68 | 12 |
| Color | 0 | 0 | 39 | 5 |

Each cell counts distinct queries. Columns and fields overlap; do not add them to estimate recovered queries. A new term means a query token present in that candidate's extra field but absent from its title. Any-token overlap is deliberately a weak lexical diagnostic, not evidence that the product satisfies the full intent.

The 18-query union is a metadata opportunity, not 18 measured recoveries. Extra fields may also promote irrelevant products or complements. The 60 remaining nonempty gaps motivate investigation of spelling, aliases, script variants or missing entity information; they are not automatically classified into those causes.

## Worse NDCG is not the same as an irrelevant first result

The original outcomes are reproduced exactly: **2,270 wins, 132 ties and 977 losses**, relative to product-ID order and with tolerance 1e-12.

| Top-ranked label among loss queries | Count |
| --- | ---: |
| Exact | 352 |
| Substitute | 371 |
| Complement | 114 |
| Irrelevant | 140 |
| Total | **977** |

Thus 723 loss queries still start with an Exact or Substitute. A Substitute receives less gain than an Exact, and NDCG also depends on the rest of the top ten. The loss count must not be presented as 977 irrelevant first results.

Among the 977 losses, 516 have at least one zero-scoring E/S candidate, while 22 have no positive-scoring E/S candidate at all. Seventy-one have a tied highest score. These flags overlap; they are not independent or exhaustive root-cause categories.

Query-length slices are also recorded in the aggregate report. They are descriptive post-hoc views, not evidence that query length causes failure or that a particular fix will generalize.

## Qualitative case review

Selection was fixed by the policy: three largest NDCG losses per locale, three zero-score cases per locale selected by a seeded hash, plus every empty-analysis case. The one empty case was already selected, giving 18 distinct cases.

The coding assistant inspected those cases and wrote the linked CSV notes. These are single-assessor hypotheses, not independently adjudicated human labels. The selection emphasizes severe losses and a repeatable zero-score sample; it does not support population-wide counts for semantic causes.

Observed patterns include:

- Main products losing to compatible accessories or other Complement-labeled items with strong word overlap.
- A Japanese query name matching accessory wording while Exact device titles use Latin-script wording.
- Name or series-like queries whose relevant titles omit that name.
- Compound or possible spelling variants with no exact token overlap.
- Negation or phrase scope lost in bag-of-words matching.
- Query/title/label tension, including an Exact-labeled monitor title with a different size from the query.

The last category is a review flag, not proof of bad annotation: variant metadata, historical changes or intent interpretation may differ. No judgments were corrected or removed. Ambiguous queries were not assigned a guessed intended spelling.

The public CSV contains query IDs and authored observations. Full query/product text, analyzer tokens and selected candidate evidence remain in the ignored local review package.

## Recommended next experiment

Prepare **one predeclared title-plus-bullet-points ablation**. Bullet points provide the broadest new query-token signal on E/S candidates among the tested fields, but their I-candidate overlap is substantial. A gain is plausible, not established.

Keep the title-only run frozen as the comparison. Fix one configuration before measurement; use the same catalog, validation queries, candidate lists, gains and tie policy. Report overall and per-locale NDCG, paired uncertainty, cohort regressions, zero-score counts, index size and batch cost. Evaluate every validation query, not only the selected failures. Record that these cohorts were discovered on development data.

Do not bundle spelling correction, named-title fallback, entity expansion or neural retrieval into that ablation. Each would answer a separate question. Preserve the official test holdout for final evaluation, and define a separate incomplete-judgment protocol before making full-catalog retrieval-quality claims.

No improvement is implemented or claimed by this analysis.

## Reproduction and verification

With the baseline's local data and score artifacts present:

```sh
uv sync --locked
./mvnw --batch-mode --no-transfer-progress verify
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
uv run --locked --no-sync python scripts/analyze_bm25_errors.py
```

The default local output is `data/processed/bm25-error-analysis-v1/`. It contains analyzer inputs/outputs, all per-query flags, selected case evidence and a manifest. Failed analyses do not publish their temporary output directory. Use a fresh directory for a repeat:

```sh
uv run --locked --no-sync python scripts/analyze_bm25_errors.py \
  --output-dir data/processed/bm25-error-analysis-v1-repeat \
  --report data/processed/bm25-error-analysis-v1-repeat-report.json
```

The test suite includes eight new checks covering empty analysis, misleading metadata overlap, E/S versus Complement semantics, losses with an Exact first result, analyzer drift, cohort invariants, deterministic sampling, and an actual Java diagnostic-bridge round trip. Together with the existing tests, that is 35 Python/integration tests and nine Java tests. CI uses fabricated fixtures and does not download ESCI.

A second complete analysis reproduced the aggregate report and all four local artifact hashes exactly. The 18 authored notes match the fixed sample manifest. Outcome totals, zero-score categories and loss top-label counts reconcile with their declared cohorts. Baseline scores and ranker source fingerprints remain unchanged.
