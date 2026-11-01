# Evaluation splits: ESCI ranking v1

Completed locally on 1 November 2026. This step prepares datasets; it does not train a model or measure retrieval quality.

## Purpose

Create repeatable training, validation, and test partitions for the judged-candidate ranking task. Keep complete query groups together and address the overlap found in the [data audit](esci-data-audit.md).

The [policy file](../config/esci-split-policy.json) fixes the scope, normalization, selection method, seed, and validation fraction. The [aggregate report](../reports/esci-split-report.json) records effective counts, verification results, provenance, and artifact hashes.

## Policy and rationale

1. Start with rows where `small_version = 1`, the official ranking subset.
2. Preserve every original ranking test row, including its label, query text, product ID, and other original columns. The derived Parquet encoding and row order may differ from the source; the complete original row contents are compared in both directions using `EXCEPT ALL`.
3. Use **all official test queries from the larger file** as a protected identity registry: 30,969 locale-query keys. Only query identities are used for splitting; relevance labels do not select partitions.
4. Match identities within locale using either query ID or query text normalized by Unicode NFKC, case folding, and Unicode whitespace collapse. Exclude complete training groups that match the protected registry. If an excluded ID has normalized-equivalent training variants, exclude those too.
5. Group the remaining training queries by locale and normalized text. Different IDs for normalized-equivalent queries must stay together.
6. Within each locale, sort groups by SHA-256 of the compact UTF-8 JSON array `[seed, locale, normalized_query]`. Break any hash tie by normalized text. The seed is `commerce-search-lab/esci-ranking/v1`.
7. Select the first 10% of groups for validation, rounding down. Clamp the selection to leave at least one group in each of training and validation; reject a locale with fewer than two eligible groups. Put the remaining groups into training.

The 10% choice is a fixed initial development policy, not a parameter selected from model performance. Allocation is stratified by locale; it is not balanced by labels, query source, or candidate count. It targets a fraction of normalized groups, so query/judgment percentages can differ.

Original downloads are untouched. Every original ranking row appears exactly once across training, validation, test, and the separately retained exclusion artifact.

## Resulting query counts

| Locale | Training | Validation | Official test | Excluded from training |
| --- | ---: | ---: | ---: | ---: |
| English (`us`) | 18,800 | 2,088 | 8,956 | 0 |
| Spanish (`es`) | 5,069 | 563 | 2,417 | 0 |
| Japanese (`jp`) | 6,554 | 728 | 3,123 | 2 |
| Total | 30,423 | 3,379 | 14,496 | 2 |

## Resulting judgment counts

| Locale | Training | Validation | Official test | Excluded from training |
| --- | ---: | ---: | ---: | ---: |
| English (`us`) | 378,079 | 41,574 | 181,701 | 0 |
| Spanish (`es`) | 137,773 | 15,118 | 65,883 | 0 |
| Japanese (`jp`) | 187,820 | 21,242 | 88,789 | 32 |
| Total | 703,672 | 77,934 | 336,373 | 32 |

Both excluded Japanese query IDs match held-out normalized text. No partial candidate list was removed. The 1,118,011 original judgments are fully accounted for.

Japanese normalized-group counts differ slightly from query counts: training has 6,552 groups, validation 728, and test 3,122. Multiple equivalent queries may stay inside a partition; none cross the three usable partitions under this normalization policy.

## Artifacts and reproduction

From the repository root:

```sh
uv sync --locked
uv run --locked python scripts/download_esci.py
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/prepare_esci_splits.py
```

Generated data stays outside Git under `data/processed/esci-ranking-v1/`:

| File | Purpose |
| --- | --- |
| `train.parquet` | The only judgment artifact to use for model fitting in this protocol |
| `validation.parquet` | Development comparisons and tuning |
| `test.parquet` | Preserved official ranking test rows; final evaluation |
| `excluded.parquet` | Overlapping training rows, retained for traceability; never used for fitting or tuning |
| `query-assignments.csv` | Query IDs, locale, original split, normalized-group hash, assigned partition, and exclusion reason |
| `manifest.json` | Aggregate report, policy, provenance and generated-file hashes |

The judgment artifacts retain the source `split` column, whose values remain `train`/`test`. An added **`evaluation_split`** column distinguishes `train`, `validation`, `test`, and `excluded`. Future consumers must use the named artifacts or this new column; filtering only the original `split` column would mix training and validation.

The physical `__index_level_0__` column is retained to verify original row contents. It must not become a ranking feature. The assignment CSV contains no relevance labels or raw query text. Group hashes identify normalization groups; they are not a privacy guarantee.

Outputs are built in a temporary directory and published only after verification succeeds. Re-running independently regenerates all artifacts and checks them against existing outputs. A changed policy, code fingerprint, environment, or artifact is reported as a mismatch; use a new `--output-dir` for a revised version instead of silently overwriting a dataset. `--report` can select a separate aggregate-report path for that comparison.

## Verification

The full-data build re-reads the written Parquet files and verifies:

- Every original ranking row is present once across the four artifacts; no rows, labels, or other original values were changed.
- Official test rows are unchanged, with no missing or added rows.
- Complete query IDs remain in one partition for each original split.
- Training, validation, and test have zero pairwise overlap by locale-query ID and locale-normalized text.
- Training and validation have zero overlap with the entire protected official test registry, not only the ranking subset.
- Excluded training groups do not also appear in training or validation.
- Every required language occurs in training, validation, and test.

The report records each violation count; every count is zero in this build.

Eighteen tests pass in total: seven audit/download tests and eleven new split tests. The new tests include input-order independence, normalized-group cohesion, larger-test protection, ID collisions, locale boundaries, invalid input, complete candidate retention, label-independent assignment, rejection of tampered test labels, and repeatable artifact generation.

## Limits and future use

This is **query-held-out evaluation**. Products may appear in multiple partitions because the task searches an existing catalog; cold-product generalization is not measured.

Normalization does not detect semantic paraphrases, translated duplicates across locales, or pretrained-model contamination. Zero overlap here means zero matches under the declared identity checks, not proof of every possible kind of leakage.

Only ranking-subset training artifacts have been prepared. The larger dataset's raw train split still contains its audited overlap, and may also contain queries assigned here to validation. Any future use of that larger training set must apply both this validation holdout and the official test holdout. Do not feed raw larger-split training data directly into fine-tuning.

Use validation for model and parameter decisions. Avoid repeated test scoring during development, choosing favorable seeds, or using test labels in feature generation or hard-negative mining. Full-catalog retrieval remains a separate evaluation track with incomplete relevance judgments.

## Next increment

Specify and implement a minimal BM25 candidate-ranking baseline, including text analysis per locale, a declared ESCI gain mapping, and NDCG@10. Develop it against training and validation artifacts; reserve test scoring for the agreed final evaluation procedure.
