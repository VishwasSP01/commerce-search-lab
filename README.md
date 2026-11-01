# commerce-search-lab

A planned multilingual product-search system built around reproducible relevance experiments and measured serving behavior.

**Status:** the ESCI audit, ranking splits, and first Java/Lucene BM25 candidate-ranking baseline are complete. Validation results are measured; neural training and deployment are future work.

The project will investigate whether multilingual retrieval and ranking improvements justify their serving cost, and how product updates affect freshness, correctness, and search latency.

Start with the [engineering charter](docs/engineering-charter.md). It defines the research questions, evidence requirements, release gates, and provisional resource envelope.

**Budget constraint:** EUR 0 additional spending. Use the existing machine and locally runnable software. See the [zero-budget execution plan](docs/zero-budget-plan.md).

## First deliverable

A data-feasibility report and reproducible BM25 evaluation on a bounded sample. Confirm dataset versions, joins, locale coverage, official splits, label interpretation, and evaluation limitations before building the distributed pipeline or scaling experiments.

## Completed step: ESCI data audit

The [audit report](docs/esci-data-audit.md) records 2,621,288 judgments, 130,652 queries, and 1,814,924 locale-specific catalog records. Every judgment joins to product metadata. Split-overlap findings are documented and must be handled before model training.

The [machine-readable report](reports/esci-data-audit.json) contains aggregate evidence; raw data remains outside Git. With Python 3.12 and `uv` available, run from the repository root:

```sh
uv sync --locked
uv run --locked python scripts/download_esci.py
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/audit_esci.py
```

## Completed step: evaluation splits

The [split policy and results](docs/evaluation-splits.md) define training (30,423 queries), validation (3,379), and the preserved official test set (14,496). Two overlapping training queries are excluded. All three languages are represented, and the prepared partitions have no overlap under the declared query-ID and normalization checks.

```sh
uv run --locked python scripts/prepare_esci_splits.py
```

Generated datasets remain under the ignored `data/processed/` directory. The [aggregate split report](reports/esci-split-report.json) records counts, checks, provenance, and artifact hashes.

## Completed step: BM25 baseline

The Java 21 / Lucene ranker indexes **1,814,924 product titles** and evaluates all **3,379 validation queries** against their judged candidates. It uses English, Spanish and Japanese analyzers with fixed BM25 parameters.

| Validation NDCG@10 | English | Spanish | Japanese | Overall, query-weighted |
| --- | ---: | ---: | ---: | ---: |
| Product-ID-order control | 0.5585 | 0.5548 | 0.5753 | 0.5615 |
| Title BM25 | **0.6945** | **0.6826** | **0.7139** | **0.6967** |

Read the [results, limits and reproduction instructions](docs/bm25-baseline.md), [predeclared design](docs/decisions/0001-bm25-baseline.md), and [aggregate evidence](reports/bm25-title-v1.json). These are candidate-ranking development results, not exhaustive full-catalog relevance or final test scores. The official test set remains unscored.

With Java 21 and the earlier data preparation complete:

```sh
./mvnw --batch-mode --no-transfer-progress verify
uv run --locked python scripts/run_bm25.py
```

## Completed step: validation error analysis

The [error analysis](docs/bm25-error-analysis.md) reproduces the baseline outcomes and distinguishes 78 title-vocabulary gaps from one empty-analysis query. Of 977 losses against the control, 352 still rank an Exact product first and 371 rank a Substitute first. Metadata and 18 inspected cases suggest hypotheses, not proven fixes.

The [aggregate diagnostic report](reports/bm25-error-analysis-v1.json) and [qualitative case notes](docs/bm25-case-review.csv) support a separate, controlled title-plus-bullet experiment. The ranker and official test holdout remain unchanged.

```sh
uv run --locked --no-sync python scripts/analyze_bm25_errors.py
```

## Continuous integration

The [CI workflow](.github/workflows/ci.yml) runs on pull requests targeting `main` (including new commits pushed to those PRs) and on pushes to `main`. It installs locked Python dependencies, builds Java 21, and runs the Java, Python and packaged integration tests on Ubuntu. Superseded runs are cancelled, and each job has a five-minute timeout.

The tests use small fabricated fixtures. CI does not download ESCI, generate benchmark datasets, or train models; the full-data audit and split reproduction remain separate commands above. No artifact uploads or Actions caches are configured. Standard GitHub-hosted runners in public repositories are [free under GitHub's current billing policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

To run the same commands locally:

```sh
uv sync --locked
./mvnw --batch-mode --no-transfer-progress verify
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
```

## Sources to audit

- [Amazon ESCI dataset and baseline code](https://github.com/amazon-science/esci-data)
- [Amazon Reviews 2023 dataset documentation](https://amazon-reviews-2023.github.io/)

These datasets serve different purposes. Catalog expansion does not provide additional query-product relevance judgments or a live product-update feed.
