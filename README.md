# commerce-search-lab

A planned multilingual product-search system built around reproducible relevance experiments and measured serving behavior.

**Status:** project planning only. No dataset audit, search implementation, training run, relevance result, or deployment exists yet.

The project will investigate whether multilingual retrieval and ranking improvements justify their serving cost, and how product updates affect freshness, correctness, and search latency.

Start with the [engineering charter](docs/engineering-charter.md). It defines the research questions, evidence requirements, release gates, and provisional resource envelope.

**Budget constraint:** EUR 0 additional spending. Use the existing machine and locally runnable software. See the [zero-budget execution plan](docs/zero-budget-plan.md).

## First deliverable

A data-feasibility report and reproducible BM25 evaluation on a bounded sample. Confirm dataset versions, joins, locale coverage, official splits, label interpretation, and evaluation limitations before building the distributed pipeline or scaling experiments.

## Sources to audit

- [Amazon ESCI dataset and baseline code](https://github.com/amazon-science/esci-data)
- [Amazon Reviews 2023 dataset documentation](https://amazon-reviews-2023.github.io/)

These datasets serve different purposes. Catalog expansion does not provide additional query-product relevance judgments or a live product-update feed.
