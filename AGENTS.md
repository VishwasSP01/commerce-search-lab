# Working agreement

## Scope and pace

- Work in small, reviewable increments. Complete the current step and explain its result before advancing to a separate milestone.
- For each increment, state its purpose, completion criteria, and relevant verification.
- Explain consequential choices and tradeoffs in plain language so the owner can understand and defend the work.
- Keep the current step narrow. Do not introduce later-stage infrastructure, model training, or deployment merely because it appears in the roadmap.
- Commit and push each completed increment using focused, descriptive messages. Do not leave finished milestones only in the working tree.
- Use the engineering charter and zero-budget plan as project context. The owner's latest instructions take precedence.

## Constraints

- EUR 0 additional spending. Required workflows must run without paid services or payment-card trials.
- Use real relevance data. Identify generated update events and simulated clicks explicitly.
- Never invent benchmark results, hiring percentiles, production capacity, or cloud deployment experience.
- Keep raw datasets, model weights, credentials, and bulky generated artifacts outside Git.

## Engineering quality

- Protect evaluation splits and document dataset provenance and transformations.
- Measure before setting capacity claims or adopting additional complexity.
- Record consequential architecture decisions, alternatives, and evidence.
- Verify meaningful behavior as it is implemented. Use tests appropriate to the change; do not add tests solely for documentation edits.
- Distinguish planned behavior, implemented behavior, and measured results in documentation and reports.

## Immediate milestone

The offline retrieval/ANN experiments and bounded Elasticsearch lexical parity pilot are complete. Read `docs/elasticsearch-pilot.md`, decision 0011, its config and both aggregate reports before serving work. Elasticsearch 9.5.6/Lucene 10.5.2 matches the frozen Lucene 10.6.0 reference on 30,000 independently sampled titles and 300 validation queries: zero ordered-ID/count or normalized-score mismatch queries and all 399 analyzer probes match. ES LegacyBM25 raw scores have a known 2.2 factor; normalize only for this comparison, not by fitting scores. Two fresh ES/reference indexes reproduce all nine artifacts. Graceful same-volume restart preserves both index UUIDs/counts and all query results; no crash/update recovery claim. The local Basic service is stopped, volume preserved; start only the project-owned Compose service and leave unrelated containers untouched. Envelope: loopback 19200, 3 GiB, 2 CPUs, 1 GiB heap. Refresh is disabled in this immutable batch profile; it has no update path. Finite sample parity does not establish universal equivalence, full-catalog serving or API capacity. Next propose a small Java 21 search API contract and bounded backend integration with explicit failures; full-catalog indexing, hybrid serving, streaming and load campaigns are separate steps.

Exact RRF remains the offline quality reference at 55.27% validation known-Exact Recall@100. No ANN point is nominated: depth 1024 reaches 94.74% strict agreement, below the unchanged 95% overall budget. Honor the closed experiment's stopping rule; do not silently adopt failed settings or claim Faiss results establish Elasticsearch behavior. Preserve all frozen baseline source/lock/index/vector contracts, locale/query/group identities, incomplete-judgment semantics and the official test holdout. Agreement, known-Exact recall and judgment coverage are distinct; unknown relevance stays unknown. CI now has Java/main Python, offline embedding, native ANN and real-Elasticsearch synthetic parity jobs. No production/final-test adoption or capacity claim. Int8, training and streaming remain separate.
