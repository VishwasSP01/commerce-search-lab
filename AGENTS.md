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

The audit, splits, BM25 experiments, embeddings, exhaustive dense baseline, RRF comparison and float32 ANN cost/quality benchmark are complete. Read `docs/ann-cost-quality.md` and the protocol/reports. Exact RRF remains the offline development reference: BM25 50.29%, dense 49.48%, RRF 55.27% validation known-Exact Recall@100. The fixed M=16/efConstruction=100 HNSW grid (efSearch 128/256/512) nominates none: deepest neighbor agreement is 90.91% overall and 89.72% Japanese, missing the predeclared 95%/90% targets. Its hybrid recall is 55.07%, paired delta [-0.37,-0.05] percentage points versus matched Flat hybrid; within the separate loss budget, but not enough to pass agreement. Native warm serial p95 is 1.085ms versus Flat 25.268ms, excluding encoding/API/concurrency. HNSW files are 2.840 GiB versus Flat 2.596; maximum one-locale query-worker RSS is 2.093 versus 1.934 GiB, not all-index or host residency. Frozen graph searches are repeatable; independent multithread graph builds are not claimed bit-identical. Preserve old vectors, source fingerprints, locks and all measured controls. Next propose a bounded ANN miss analysis before predeclaring a graph/search ablation; do not add settings to this completed grid or silently relax its budget. Int8 stays separate. Distinguish neighbor agreement, known-Exact recall and judgment coverage; unjudged relevance stays unknown. Preserve locale scope, query/group identities, explicit cutoff-tie policies and the official test holdout. Keep training, API/Elasticsearch serving and streaming separate. CI has regression, offline embedding and native ANN jobs; isolate future dependencies. No production/QPS claim from native/cached-list timings.
