# commerce-search-lab

A planned multilingual product-search system built around reproducible relevance experiments and measured serving behavior.

**Status:** the full audit of the pinned ESCI files is complete. No search implementation, training run, relevance result, or deployment exists yet.

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

The next increment is the evaluation split and overlap policy. The BM25 baseline follows that step.

## Sources to audit

- [Amazon ESCI dataset and baseline code](https://github.com/amazon-science/esci-data)
- [Amazon Reviews 2023 dataset documentation](https://amazon-reviews-2023.github.io/)

These datasets serve different purposes. Catalog expansion does not provide additional query-product relevance judgments or a live product-update feed.
