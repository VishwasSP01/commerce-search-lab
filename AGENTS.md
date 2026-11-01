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

The audit, splits, BM25 experiments, full-catalog embedding export, exhaustive dense baseline and fixed RRF comparison are complete. Read `docs/full-catalog-rrf.md` and its protocol/reports. Validation known-Exact Recall@100: BM25 50.29%, dense 49.48%, RRF 55.27%; paired RRF-minus-BM25 95% interval [+4.32, +5.59] percentage points. All locales improve on recall; Spanish judgment coverage decreases and 441 queries regress in recall. RRF passes the predeclared offline-reference nomination rule, not a final-test/serving release. In the fixed 78-query vocabulary-gap cohort it recovers a known Exact for 19, leaving 59 with none. Source lists and all 1,814,924 vectors remain saved; both fusion runs reproduce all output hashes/metrics. The next proposed increment is a bounded ANN cost/quality experiment against the exact dense reference, followed separately by int8. Preserve separate meanings of ANN agreement with exact top 100, known-Exact recall and judgment coverage; do not assume a fourfold total-memory saving or no hybrid-quality loss. Preserve all frozen sources/locks and encoding/model/runtime contracts; use separate dependency environments if needed. Preserve locale scope, unknown relevance, exact query/group pairing, cutoff ties, the official test holdout and the same-machine/batch limits. Keep training, API, Elasticsearch serving and streaming separate. CI requires packaged integrations and offline CPU fixtures. A later relevance-eval bridge must preserve linear ESCI gains and exact query identities. Do not tune on the official test or claim deployed capacity from cached-list fusion.
