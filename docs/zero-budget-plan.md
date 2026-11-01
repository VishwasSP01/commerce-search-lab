# Zero-budget execution plan

Requirement: EUR 0 additional spending. Existing hardware, electricity, and internet access are assumed available. This is a local research and engineering project, not an always-on hosted service.

## Quality standard

Preserve evaluation integrity, meaningful correctness tests, failure experiments, reproducibility, and honest reporting. Adjust experiment size and runtime to available resources. The project does not claim a hiring percentile or equivalence to a large production deployment.

## Available machine

Inspected on 1 November 2026 using local system tools:

- Apple M5 Pro.
- 24 GiB unified memory.
- 15 reported logical CPUs.
- Approximately 805 GiB filesystem space available at inspection.

This inventory does not establish GPU compatibility, model-training speed, index capacity, or sustainable load. Measure those before committing to final targets. Do not allocate all available memory or disk to the project.

## Execution strategy

| Area | Local approach | Evidence to collect |
| --- | --- | --- |
| Data | Begin with ESCI's own catalog and a deterministic sample; audit before expansion | Counts, checksums, joins, missing fields, locale coverage, split integrity |
| Retrieval | BM25 first, then a compact multilingual encoder and fusion | Per-locale quality, embedding throughput, index build time, storage and memory |
| Training | Run a short compatibility and resource pilot; size training data, batches and sequence lengths from measurements | Actual training pairs, training time, peak memory, convergence and held-out results |
| LTR | Add after reliable feature generation and a leakage audit | Incremental quality, feature ablations and scoring overhead |
| Ingestion | Run a bounded local Kafka/Flink/search stack with explicitly generated update events | Ordering, replay, restart behavior, freshness and backpressure |
| Serving | Run the Java API and local telemetry with declared resource limits | Tail latency, error rate, saturation and recovery under concurrent queries and updates |
| LLM extension | Try a small local model only after a compatibility and latency pilot; compare with a rule-based baseline | Extraction accuracy, harmful filtering, quality, latency and fallback behavior |
| Deployment | Reproducible local startup; local cluster only if it fits the measured resource envelope | Successful local deployment, teardown and repeatability; precise limits on unexecuted cloud configuration |

## Memory and workload rules

- Schedule model training and bulk embedding separately from the full ingestion/serving stack unless measurements show that concurrency is safe.
- Checkpoint expensive offline work and cache versioned embeddings to avoid recomputing unchanged inputs.
- Stream or batch metadata processing rather than assuming the entire dataset fits in memory.
- Expand corpus size in measured stages, recording unique usable products and memory/disk growth. Millions of products are a stretch experiment until demonstrated.
- Measure whether the load generator competes with the system under test on the same machine. Report this limitation and generator saturation.
- Local process/container failures establish behavior for those tests. They do not establish independent-host, network-partition, availability-zone, or regional resilience.
- Optional external free compute must not become a prerequisite for reproducing the core project.

## Feasibility decisions

The first two weeks should establish dataset validity, a BM25 baseline, the cost in time and memory of embedding a small sample, and whether a short training run works locally. Those measurements determine the next release's scope.

Do not promise a fine-tuning gain, a particular catalog size, QPS, or tail-latency threshold before these pilots. If a desired component does not fit, first reduce model size, workload size, or concurrency and document the tradeoff. Any removal of a headline capability should be explicitly reflected in the project scope and final claims.

## Deployment claims

Publish local deployment instructions and artifacts. A demo recording can show behavior without a hosted service. Cloud configuration that was not applied must be labeled unexecuted; static validation is not a cloud deployment test.
