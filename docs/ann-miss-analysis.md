# ANN misses: higher-score omissions dominate the agreement loss

The frozen efSearch=512 result misses **30,705** members of the matched Flat top-100 lists across **3,379 validation queries**. Under the predeclared replacement matching, **30,650 (99.82%)** have a higher common float64 score than their paired ANN replacement. Only **55 (0.18%)** are byte-identical vector replacements. No additional nonidentical score-equivalent pairs or float64 order reversals are found at the fixed 1e-10 tolerance.

Crediting all equivalent replacements changes agreement from **90.913% to 90.929%**. The original strict agreement, 95%/90% budgets and no-nomination decision remain unchanged. Equal-score artifacts explain very little of this fixed graph's loss. Exact RRF remains the offline reference at **55.27% known-Exact Recall@100**.

## What was lost

| Locale | Missing exact neighbors | Identical-vector replacements | Strict higher-score pairs | Strict agreement | Diagnostic equivalence credit |
| --- | ---: | ---: | ---: | ---: | ---: |
| English | 18,563 | 47 | 18,516 | 91.110% | 91.132% |
| Spanish | 4,658 | 2 | 4,656 | 91.726% | 91.730% |
| Japanese | 7,484 | 6 | 7,478 | 89.720% | 89.728% |
| **Overall, query-weighted** | **30,705** | **55** | **30,650** | **90.913%** | **90.929%** |

Strict higher-score pairs occur in **2,781 queries**; identical-vector replacements occur in 16. The positive score-gap distribution for the remaining paired replacements has mean **0.005776**, median **0.004182**, p95 **0.015684** and maximum **0.098553**. These gaps use the fixed stored-vector inner-product objective, not a semantic quality scale or graph-traversal trace. Their interpretation depends on the declared deterministic pairing.

Of the 30,705 members absent at depth 512, **30,676** are also absent from the saved depth-128 lists and **30,690** from depth 256. The lists are not assumed to be nested. This descriptive context does not establish whether more search work or a different construction will resolve the misses.

## Human relevance and hybrid impact

| Locale | Dense known-Exact pairs lost / added | Hybrid known-Exact pairs lost / added | Hybrid recall-regression queries |
| --- | ---: | ---: | ---: |
| English | 265 / 82 | 56 / 41 | 41 |
| Spanish | 121 / 55 | 14 / 15 | 13 |
| Japanese | 210 / 52 | 33 / 25 | 27 |
| **Overall** | **596 / 189** | **103 / 81** | **81** |

Counts are **query-product pairs**, not distinct catalog products or query-mean recall. All 596 lost dense known-Exact pairs belong to the strict-score-loss category; none is explained by equivalent-vector swaps. Dense recall improves on 147 queries and regresses on 306. Hybrid recall improves on 71 and regresses on 81, reproducing the benchmark outcomes. Net pair counts and query-mean recall have different denominators; for example, Spanish hybrid adds one pair net while its query-mean recall still decreases.

In **8 of the 81 hybrid regressions** (6 English, 2 Spanish), ANN retains every known Exact product present in the Flat dense list. Losing a positive dense candidate is therefore not necessary for a hybrid regression: changed candidate mixtures and relative ranks can also change RRF's final top 100. This observation does not isolate a particular graph mechanism.

Japanese retains the lowest strict agreement and loses 210 known-Exact dense pairs. This comparison between approximations of the **same frozen embeddings** is not evidence that the Japanese encoder or tokenizer is the cause. Unknown judgments remain unknown. A higher inner product does not prove higher human relevance, and identical embeddings need not imply duplicate products or equal judgments.

## Representative cases

Nine cases are selected by the fixed rules: one largest strict-loss count, greatest negative hybrid recall delta and largest identical-vector count per locale, with query-ID ties. Detailed queries, original labels, product IDs, scores and at most two pairs per case remain under ignored data. These purpose-selected examples illustrate mechanisms; prevalence comes from the complete tables above.

- **Japanese query 118621, `カラコン ブルー フチなし`:** 93 missing neighbors, all strict-score-loss pairs; hybrid recall decreases by 0.25. Known Exact `B07BVKSMPS` has recomputed score **0.872918**, versus **0.859056** for its paired unjudged replacement `B076JDFZ5C`. The metadata describes contact lenses for the missing item and a pouch for the replacement. The replacement remains unjudged; no new Irrelevant label is assigned.
- **Spanish query 62012, `libro te`:** two strict-score-loss pairs; hybrid recall decreases by one third. A labeled Exact tea-book product, `1520910622`, scores **0.867197** versus **0.855796** for the paired unjudged `8423696456`, whose title is “Técnica Contable.” The **0.011401** gap is not an equivalence under the declared tolerance.
- **English query 27074, `clothing printer machine`:** 96 missing neighbors, all strict-score-loss pairs; hybrid recall decreases by one seventh. A labeled Exact heat-press product, `B07S1MW1V3`, scores **0.865903**, versus **0.837475** for an unjudged printed-clothing replacement. These are recorded labels and metadata observations, not a new human assessment.
- **English query 80040, `pilot g2 10`:** all 24 missing members have byte-identical-vector replacements; the selected products have the same short title “Pilot.” Their common score is **0.846842**, the displayed judgments are unknown, and this query's hybrid recall is unchanged. This is a genuine score-equivalence example, but it explains a small portion of aggregate misses.
- **Spanish query 66668, `mcstuffins`:** 53 strict-score-loss pairs. The selected missing products have title `-` and unknown judgments, while hybrid recall is unchanged. Approximating the distance objective and finding useful products are separate questions; these examples do not establish that 53 relevant products were lost. The diagnostic does not filter or repair metadata after seeing results.

## Method and limitations

The [decision](decisions/0009-ann-miss-analysis.md) and [configuration](../config/ann-miss-analysis-v1.json) were committed before diagnostic outcomes. Primary comparison is **saved HNSW-512 versus matched Faiss Flat**, top 100, on every validation query. No new nearest-neighbor search, graph build, parameter change, encoding, training or judgment occurs; the official test is unopened.

The analysis verifies source/report bindings, every required saved-result hash, query identities and validation provenance. It reproduces saved dense and hybrid per-query metrics. Each locale's stored Flat index and row-aligned ID map is checked before loading. At most **200 vectors per query** are reconstructed from the original float32 storage. Both sides are scored using float64 `np.einsum(..., optimize=False)`, without renormalization. This is common scoring of the observed union, not infinite-precision cosine or exhaustive full-catalog float64 ground truth.

Missing members and ANN-only replacements are matched in three stages: byte-identical vectors first, then maximum-cardinality equivalence among remaining scores within 1e-10, then rank-paired leftovers in descending score/ascending ID order. The latter are strict positive score losses or visible numeric reversals. Every missing/replacement identity appears once. Matching equivalent pairs first avoids the error of pairing scores `[0.9, 0.8]` against `[0.8, 0.7]` as two losses when one 0.8-for-0.8 equivalence is available. Equivalence credit is a diagnostic under this procedure, not a substituted adoption metric. Label losses/additions are counted independently of matching credit.

All per-query missing counts, category totals, known-Exact changes and the original strict-agreement mean are conserved. The [aggregate report](../reports/ann-miss-analysis-v1.json) contains locale counts, gap statistics, case membership, original nomination, source/index bindings and artifact hashes. A [fresh-run repeat](../reports/ann-miss-analysis-v1-reproduction.json) reproduces all three detailed artifact hashes and every non-execution report field, while preserving the input results and indexes. Source paths and dependency locks remain unchanged.

All **108 tests** pass locally: 13 Java, 70 main Python/integration, 10 offline embedding/export/search and 15 native ANN/diagnostic tests. Eight added fixtures cover score/vector equivalence with distinct labels, the rank-pairing pitfall, numeric reversals, invalid inputs, native reconstruction, deterministic/conserved pairing, case rules, ranking effects and locale-safe metadata joins. A separate 384-dimensional random-query check against `math.fsum` gives maximum absolute score error **6.94e-17** over 100 random float32-vector scores; its seed and recipe are recorded in the repeat verification.

The first diagnostic command records **15.04 seconds** of main wall time and **2.796 GiB** maximum process RSS. It loads one Flat locale index at a time and retains aggregate pair records. These are diagnostic resource observations, not ANN retrieval latency, serving residency or whole-host memory. The raw product table is read only for the bounded case metadata, after checksum verification.

## Reproduce and next decision

Initial command:

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/analyze_misses.py
```

The published report and initial data paths now exist and are refused. With the frozen ANN/Flat results, indexes, query vectors and ESCI metadata available, use fresh paths:

```sh
uv run --locked --no-sync --project experiments/ann-benchmark python experiments/ann-benchmark/analyze_misses.py \
  --output-dir data/processed/ann-miss-analysis-v1-local \
  --report data/processed/ann-miss-analysis-v1-local-report.json
```

Retain exact RRF and the original no-ANN-nomination decision. The recommended **next separate experiment** is one fixed **efSearch=1024** point on the same frozen graphs, preserving M, construction, vectors, locale routing, evaluation and budgets. That isolates additional search work before introducing graph-construction variability or compression. Predeclare that new experiment separately; this diagnosis neither executes it nor guarantees it will pass. Graph structure, metadata quality and encoder behavior remain unproven explanations for the observed misses.
