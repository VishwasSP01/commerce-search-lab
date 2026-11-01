# ESCI data audit

Completed locally on 1 November 2026. Coverage: all three pinned source files, not a sample. The machine-readable evidence is [reports/esci-data-audit.json](../reports/esci-data-audit.json).

## Purpose and completion criteria

Establish what data we actually have before implementing retrieval or training models. Completion requires pinned provenance and checksums, verified schema and identity rules, measured locale/subset counts, metadata join and duplicate checks, and an explicit train/test overlap audit.

This audit meets those criteria. It also finds split-overlap warnings that must be addressed by the next evaluation-preparation step. The raw data has not been modified and no model has been trained.

## Provenance and reproduction

- Upstream: [amazon-science/esci-data](https://github.com/amazon-science/esci-data).
- Pinned revision: [`7916cdf6ab75a462e77f20ab40428a10923998d5`](https://github.com/amazon-science/esci-data/tree/7916cdf6ab75a462e77f20ab40428a10923998d5).
- [Source manifest](../config/esci-source.json): immutable-revision download URLs, expected sizes and SHA-256 hashes. Parquet hashes and sizes come from upstream Git LFS pointers. The CSV was first verified against the upstream Git blob hash, then its SHA-256 was recorded as well.
- The upstream [README](https://github.com/amazon-science/esci-data/blob/7916cdf6ab75a462e77f20ab40428a10923998d5/README.md) states Apache-2.0; the repository includes a [LICENSE](https://github.com/amazon-science/esci-data/blob/7916cdf6ab75a462e77f20ab40428a10923998d5/LICENSE) file. Raw data stays outside this project's Git history.
- Download size: 1,161,827,075 bytes in total. Downloads stream to temporary files, verify against the manifest, then become available under `data/raw/esci/`.
- Execution environment: Python 3.12.16, DuckDB 2.0.0. Dependencies are pinned in `uv.lock`. The audit uses four DuckDB worker threads with a 4 GB DuckDB memory setting; that setting is not a hard cap on the complete Python process.

From the repository root, with `uv` available:

```sh
uv sync --locked
uv run --locked python scripts/download_esci.py
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/audit_esci.py
```

The audit independently rechecks input hashes before reading data. It reports findings without automatically cleaning or rejecting split overlaps. A successful process exit means the audit completed, not that every finding is acceptable for model training.

## Files and schema

| File | Role | Fields used |
| --- | --- | --- |
| `shopping_queries_dataset_examples.parquet` | Query-product relevance judgments | `example_id`, `query`, `query_id`, `product_id`, `product_locale`, `esci_label`, `small_version`, `large_version`, `split` |
| `shopping_queries_dataset_products.parquet` | Product catalog | `product_id`, `product_locale`, `product_title`, `product_description`, `product_bullet_point`, `product_brand`, `product_color` |
| `shopping_queries_dataset_sources.csv` | Query source categories | `query_id`, `source` |

The examples file also contains `__index_level_0__`, a physical index column. Do not use it as a query/product identity or a ranking feature.

Identifiers and version flags are BIGINT; text fields are VARCHAR in the observed schema. The report includes the complete observed schemas.

ESCI labels are E (Exact), S (Substitute), C (Complement), and I (Irrelevant). A judgment belongs to a query-product pair; it is not a product's universal relevance. The numerical gain mapping and use of non-exact labels in training remain evaluation/modeling decisions, not assumptions made by this audit.

`product_locale` values are `us` (English), `es` (Spanish), and `jp` (Japanese). Locale coverage alone is not evidence of cross-language retrieval ability. The source categories are query-provenance metadata, not click logs.

## Measured inventory

| Measurement | Observed count |
| --- | ---: |
| Full judgment rows | 2,621,288 |
| Unique query IDs | 130,652 |
| Product catalog rows / unique locale-product keys | 1,814,924 |
| Source rows / unique source query IDs | 130,652 |
| Ranking-subset judgment rows (`small_version = 1`) | 1,118,011 |
| Ranking-subset queries | 48,300 |
| Product keys referenced by full judgments | 1,814,883 |
| Catalog product keys without judgments | 41 |

All full-file judgment rows have `large_version = 1`. The upstream README narrative gives 2,621,738 judgments, while its summary table and these verified files give 2,621,288. Use the pinned-file measurements in project claims.

The catalog already offers approximately 1.81 million locale-specific product records for a possible indexing experiment. This is an inventory finding, not an implemented or benchmarked index.

## Ranking subset by locale and split

| Locale | Train queries | Train judgments | Test queries | Test judgments |
| --- | ---: | ---: | ---: | ---: |
| English (`us`) | 20,888 | 419,653 | 8,956 | 181,701 |
| Spanish (`es`) | 5,632 | 152,891 | 2,417 | 65,883 |
| Japanese (`jp`) | 7,284 | 209,094 | 3,123 | 88,789 |
| Total | 33,804 | 781,638 | 14,496 | 336,373 |

The distribution is uneven. Future reports must include per-locale results and define how aggregate metrics are weighted.

## Integrity and missing metadata

- Zero duplicate example IDs, locale-product keys, or locale-query-product keys.
- Zero conflicting labels for a locale-query-product key.
- Zero unmatched judgment-to-product rows when joining on **both `product_locale` and `product_id`**.
- Zero unmatched judgment-to-source rows when joining on `query_id`.
- Zero null identity rows, empty queries, or unexpected labels, locales, splits, and version-flag values under the implemented checks.
- **11,567 product ID strings occur in multiple locales.** Joining on `product_id` alone is unsafe even though locale-product keys are unique.

For product fields, missing means SQL NULL or an empty string after DuckDB `trim`; this does not assess whether the remaining content is useful or accurate.

| Product field | Missing rows | Share of catalog |
| --- | ---: | ---: |
| Title | 0 | 0.00% |
| Description | 877,805 | 48.37% |
| Bullet points | 304,120 | 16.76% |
| Brand | 142,756 | 7.87% |
| Color | 691,020 | 38.07% |

Titles are present throughout the catalog. Description availability must not be assumed when designing document text or model inputs.

## Train/test overlap findings

All overlaps below are computed **within locale**. Normalization is Unicode NFKC, case folding, and collapse of Unicode whitespace. It is an identity audit heuristic, not a semantic duplicate detector or retrieval analyzer. Counts are unique shared keys/strings, not numbers of overlapping judgment rows.

| Scope | Shared query IDs | Shared exact query strings | Shared normalized query strings |
| --- | ---: | ---: | ---: |
| Full / larger dataset | 1 | 1 | 4 |
| Ranking subset | 0 | 0 | 2 |

The full-dataset ID overlap occurs in `us`. The full normalized-string overlaps occur in `us` (1) and `jp` (3). Both ranking-subset normalized overlaps occur in `jp`.

The presence of different IDs therefore does not establish complete separation of query text. This audit does not inspect semantic paraphrase leakage, pretrained-model contamination, or any future feature/training pipeline.

**Next evaluation-preparation decision:** preserve the official test set, define and record training-group exclusions for overlaps with held-out query identities, and create validation groups from training data only. Report the original and effective counts. Keep normalized-equivalent queries together when deriving validation groups. No exclusion or validation split has been applied in this step.

## Candidate coverage limits

The observed ranking candidate-group size ranges from 8 to 188 judgments; the full dataset ranges from 3 to 198. Do not enforce a hard limit of 40 based on the upstream introduction. This audit does not establish why the observed group sizes differ from that description.

These are judged candidate lists, not exhaustive relevance judgments across the catalog. Evaluating their ordering and evaluating retrieval over 1.81 million records are different tasks. Unjudged products must not silently become verified irrelevant products.

## Verification and boundary of this step

Seven regression tests pass. They cover locale-sensitive joins, normalized overlap, query IDs crossing splits, duplicate metadata and conflicting judgments, missing schema, corrupted downloads, and size/Git-blob verification.

The complete dataset audit ran successfully with verified inputs. Raw data, local environments, and download receipts are ignored by Git; the compact aggregate report is intended for version control. Reproduction commands use the pinned dependency lock.

This step creates no search service, index, embedding model, training dataset, or relevance score. The next increment is a reproducible evaluation split and overlap policy, before the BM25 baseline.

## Implementation references

- [DuckDB 2.0 Python API](https://duckdb.org/docs/2.0/clients/python/overview.html)
- [DuckDB 2.0 Parquet reader](https://duckdb.org/docs/2.0/data/parquet/overview.html)
- [DuckDB 2.0 configuration](https://duckdb.org/docs/2.0/configuration/overview.html)
