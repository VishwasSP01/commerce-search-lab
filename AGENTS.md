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

The retrieval foundation, RRF, float32 ANN benchmark and ANN miss diagnosis are complete. Read `docs/ann-miss-analysis.md` and its protocol/reports. Exact RRF remains the offline reference at 55.27% validation known-Exact Recall@100; no ANN setting is nominated. The frozen M=16/efConstruction=100 grid 128/256/512 stays unchanged. Of 30,705 missing depth-512 neighbors, 30,650 are strict higher-score paired omissions, 55 are byte-identical replacements, zero are other score-equivalent/numeric reversals at 1e-10. Diagnostic equivalence credit changes agreement 90.913% to 90.929%, not enough to alter the old 95%/90% budget. Dense loses/adds 596/189 known-Exact query-product pairs; hybrid loses/adds 103/81, and 8 hybrid regressions retain all dense known Exact members. Do not equate vector/score equivalence with relevance or infer graph/language causes. Both diagnostic runs reproduce all artifacts; no new nearest-neighbor search was issued. Next propose one separately predeclared efSearch=1024 point on the same frozen graphs, holding construction, vectors, routing and all budgets fixed; no gain is guaranteed. Do not edit the old grid, locks or source fingerprints. Int8, training, API/Elasticsearch serving and streaming stay separate. Preserve official test isolation, locale/query/group pairing, incomplete-judgment semantics, and the distinction between ANN agreement, known-Exact recall and judgment coverage. CI has regression, offline embedding and native ANN/diagnostic jobs. Native warm timing is not API capacity, and one-locale RSS is not whole-host residency. Existing baseline results and index/vector bundles remain saved.
