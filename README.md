# commerce-search-lab

A planned multilingual product-search system built around reproducible relevance experiments and measured serving behavior.

**Status:** the ESCI data audit and reproducible ranking splits are complete. No search implementation, training run, relevance result, or deployment exists yet.

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

Generated datasets remain under the ignored `data/processed/` directory. The [aggregate split report](reports/esci-split-report.json) records counts, checks, provenance, and artifact hashes. The next increment is the BM25 candidate-ranking baseline.

## Continuous integration

The [CI workflow](.github/workflows/ci.yml) runs on pull requests targeting `main` (including new commits pushed to those PRs) and on pushes to `main`. It installs the locked dependencies and runs the regression tests on Ubuntu with Python 3.12. Superseded runs are cancelled, and each job has a five-minute timeout.

The tests use small fabricated fixtures. CI does not download ESCI, generate benchmark datasets, or train models; the full-data audit and split reproduction remain separate commands above. No artifact uploads or Actions caches are configured. Standard GitHub-hosted runners in public repositories are [free under GitHub's current billing policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

To run the same commands locally:

```sh
uv sync --locked
uv run --locked --no-sync python -m unittest discover -s tests -v
```

## Sources to audit

- [Amazon ESCI dataset and baseline code](https://github.com/amazon-science/esci-data)
- [Amazon Reviews 2023 dataset documentation](https://amazon-reviews-2023.github.io/)

These datasets serve different purposes. Catalog expansion does not provide additional query-product relevance judgments or a live product-update feed.
