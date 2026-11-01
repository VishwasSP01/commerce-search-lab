# Engineering charter

Status: initial proposal, 1 November 2026. Revise as measurements replace assumptions.

## Objective

Build a multilingual product-search system whose relevance, latency, resource use, ingestion correctness, and failure behavior can be independently assessed.

The primary portfolio narrative is search and relevance engineering with dependable backend serving. Fine-tuning and distributed ingestion are experiments within that narrative. No hiring percentile or employer endorsement is claimed.

## Proposed resource envelope

- Plan around 10–12 focused hours per week, subject to the owner's availability.
- Begin with a two-week feasibility phase; estimate later releases from its results.
- Additional spending is capped at EUR 0.
- Hardware inspected on 1 November 2026: Apple M5 Pro, 24 GiB unified memory, 15 reported logical CPUs, and approximately 805 GiB filesystem space available at inspection. Available space is a snapshot, not a project allocation. GPU/software compatibility and training throughput remain untested.
- Use the existing machine and locally runnable software. Required workflows must not depend on paid services, payment-card trials, or opportunistic free GPU availability.
- Benchmark a small sample before selecting model size, training workload, catalog size, and service concurrency. See the [zero-budget execution plan](zero-budget-plan.md).
- Separate offline training/embedding jobs from the full serving stack where needed to stay within memory limits; retain concurrent queries and updates for the relevant systems tests.

The zero-spending limit is a requirement. Weekly time and release timing remain recommendations. Do not derive a production-scale capacity claim from the local hardware.

## Questions the project must answer

1. Where does BM25 fail across the three dataset locales?
2. Does dense retrieval plus fusion improve quality, and for which query categories?
3. Does fine-tuning or learning to rank add value beyond the chosen hybrid baseline?
4. What latency, memory, indexing time, and compute cost accompany each improvement?
5. How do load, retries, stale events, and component failures affect correctness and freshness?

Serving queries in several languages does not establish cross-language retrieval quality. Treat cross-language retrieval as a separate experiment if introduced.

## Evaluation contract

### Data and splits

- Record upstream source, release or revision, retrieval date, access and usage terms, checksums, schema, and preprocessing version.
- Audit joins on the documented product and locale identifiers. Report missing records, duplicates, identifier ambiguity, and per-locale counts.
- Preserve the official train/test boundary. Derive validation queries from training data and keep query groups together.
- Audit normalized query overlap and document any deduplication or exclusion policy.
- Keep test labels out of training, hard-negative mining, tuning, and model selection. Report actual training examples used; total dataset labels are not the training count.
- Check metadata availability and identifier compatibility before adding Amazon Reviews products. Report overlap and language coverage rather than assuming compatibility.

### Separate evaluation tracks

1. **Candidate ranking:** use the documented ESCI ranking subset and fixed judged candidate lists. Explicitly record the ESCI-to-gain mapping and metric implementation. Report NDCG@10, per-locale scores, query counts, and uncertainty.
2. **Catalog retrieval:** search a declared corpus. Report judged-relevant hit coverage at the retrieval cutoff, judgment coverage among returned results, and the treatment of unjudged products. These are incomplete-label measurements, not exhaustive recall or complete relevance ground truth.
3. **Systems performance:** use a declared corpus, query distribution, update workload, and hardware configuration. Report resource use and limitations separately from relevance.

Do not present candidate-ranking gains as proof of full-catalog retrieval gains. If human judgments are added, specify the sampling policy, rubric, assessor process, and agreement or adjudication procedure.

### Experiment discipline

- Register the hypothesis, baseline, primary metric, comparison procedure, and stopping rule before evaluating a new approach.
- Tune on validation data. Use the held-out test set for release decisions; record exposure and disclose repeated comparisons.
- Compare methods on the same query sets and report absolute and relative changes.
- Use paired query-level confidence intervals. If significance tests are used, document assumptions and account for multiple comparisons where applicable.
- Show per-locale results and both the aggregate weighting rule and query-category analysis.
- Include losing experiments and representative errors. A negative result is publishable when its method is sound.

## Architecture decision rules

- Start with a batch-built BM25 index, an evaluation command, and a minimal search API.
- Add dense retrieval and fusion only after the baseline is reproducible.
- Choose fine-tuning or LTR as the first learned improvement based on observed errors and resource feasibility.
- Introduce Flink with an explicit stateful processing requirement, such as keyed version handling or coalescing updates. Benchmark a simpler alternative when feasible.
- Record each consequential choice in an architecture decision record: context, alternatives, decision, evidence, consequences, and revisit trigger.
- State component delivery semantics precisely. Idempotent indexing does not alone establish end-to-end exactly-once behavior.
- Recompute embeddings when searchable content changes; price or stock updates should not require re-embedding unchanged text.

## Release gates

| Release | Deliverable | Evidence required to finish |
| --- | --- | --- |
| 0: feasibility | Data audit and bounded BM25 experiment | Verified joins and splits; exact sample manifest; repeatable metric command; runtime and resource estimate; revised scope |
| 1: foundation | BM25 evaluation and Java search API | Versioned artifacts; per-locale baseline; documented API behavior and error handling; measured latency on declared hardware |
| 2: neural search | Dense retrieval and fusion; one learned improvement | Controlled comparisons; uncertainty; error analysis; latency and resource tradeoffs; reproducible model configuration |
| 3: reliable updates | Kafka/Flink update path and serving instrumentation | Duplicate, stale-event, replay and restart behavior; correctness checks; freshness distribution under concurrent query/update load |
| 4: operating evidence | Load, recovery and observability report | Workload manifests; saturation point; p50/p95/p99; errors and timeouts; recovery duration; resource/cost record; runbook |

Do not require a positive relevance gain to close an experiment. Require credible evidence and a clear decision about whether to retain the change.

## Failure and load protocol

- Define per-product ordering or version semantics, including how duplicate, stale, conflicting, and malformed events are handled.
- Measure freshness from an explicit event timestamp to search visibility. Distinguish source time, ingestion time, and processing time; document clock assumptions.
- Test duplicates, out-of-order events, consumer restart, checkpoint recovery, Elasticsearch unavailability, and malformed records.
- Specify retry limits, dead-letter handling, replay, and how permanent failures become visible.
- Test embedding-service timeouts and degraded retrieval. Record how fallback changes quality and latency.
- Run concurrent queries and updates. Report offered and achieved load, concurrency, duration, warm-up, cache state, timeout rate, and failures alongside successful-request latency.
- Choose a load generator and configuration that make coordinated omission visible or avoid it; include overload behavior and the saturation point.
- Establish measured baselines before selecting numeric latency, freshness, and throughput targets. Publish the targets before the final load campaign.
- A small deployment is evidence for its measured configuration, not proof of large-company production capacity.

## Extensions after the core gates

- **Learned sparse retrieval:** add only with an explicit hypothesis and compatible multilingual evaluation.
- **LLM query understanding:** compare with rules and a no-parser baseline; evaluate intent correctness, harmful filters, relevance, latency, cache behavior, fallback, and cost. Schema validity is only one check. Build an independently labeled query-understanding test set if existing labels cannot assess it.
- **Click simulation:** label it as simulation. Test sensitivity to position bias and simulator assumptions; do not claim real user or conversion lift.
- **Helm and Terraform:** Helm may be exercised on a local cluster after resource feasibility is established. Cloud-specific Terraform can be authored and statically validated within the zero-budget constraint; do not claim an actual cloud deployment or tested cloud behavior without executing it. Avoid spending dependencies in the required workflow.
- **Catalog expansion:** justify the chosen size using a systems question. Report real unique usable records, deduplication, locale distribution, embedding/index storage, build time, and judgment limitations.

## Required final artifacts

- A concise README with measured results, reproduction instructions, and known limits.
- A system diagram, consequential architecture decisions, and versioned experiment configurations.
- Data/model manifests and scripts to reproduce the published tables without redistributing restricted artifacts.
- An evaluation report with failure examples and negative findings.
- Load and recovery reports, useful dashboards, and an operational runbook.
- CI checks for meaningful correctness risks: evaluation integrity, ordering/version rules, recovery, and API contracts. Add tests as these behaviors are implemented.
- Resume claims that match recorded measurements, hardware, datasets, training counts, and simulated workloads exactly.

## Review checkpoints

Ask the project reviewer to assess the data/evaluation protocol before model development, the architecture before distributed ingestion, and the evidence before publication. Review comments are inputs to decisions; document how consequential concerns were resolved.

## Immediate next work

The machine inventory, ESCI audit, splits, candidate-ranking baseline, error analysis and title-plus-bullet ablation are complete; the bullet variant was not adopted. The [full-catalog BM25 baseline](full-catalog-bm25.md) now measures known-Exact recall and judgment coverage under a predeclared incomplete-label protocol. The next proposed increment is a bounded multilingual embedding compatibility/resource pilot, followed by dense/fusion comparison if feasible. Serving, training and streaming remain separate increments; the official test set stays reserved. Keep experiments bounded: a publishable comparison and clear decision are sufficient without a positive gain.
