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

The retrieval foundation, RRF, ANN benchmark/diagnosis and separate depth-1024 point are complete. Read `docs/ann-depth-1024.md` and its protocol/reports. Exact RRF remains the offline quality reference at 55.27% validation known-Exact Recall@100; no ANN point is nominated. Original grid 128/256/512 and sources remain frozen. The new 1024 point on identical graphs improves strict agreement 90.91% to 94.74%, still below the unchanged 95% overall budget; locale >=90% and hybrid-loss checks pass. Hybrid recall is 55.13% versus 512's 55.07%, paired delta [+0.063 pp, interval -0.033 to +0.169 pp], not a demonstrated gain. It recovers 12,948 old missing neighbors, leaves 17,757 and introduces 29 new misses. Contemporary native warm p95 is 2.453ms versus 512's 1.195ms; old Flat latency is historical, not remeasured. Reverse-order replay reproduces rankings, metrics and decision, with unchanged index/input/contract hashes and byte-identical 512 controls. Honor this experiment's stopping rule: no more points, new construction or relaxed budget inside it. Next propose a bounded local Elasticsearch serving/parity feasibility pilot, starting with a declared backend/version/resource envelope and frozen BM25 parity targets; Java API remains a subsequent small step. Do not silently adopt failed ANN settings or claim Faiss results establish Elasticsearch behavior. Int8, training, API and streaming remain separate. Preserve all source/lock/index/vector contracts, locale/query/group identities, incomplete-judgment semantics and the official test holdout. Agreement, known-Exact recall and coverage are distinct; unknown relevance stays unknown. Native singleton timing excludes encoding/API/concurrency; RSS is one-locale, not whole-host residency. CI has Java/main Python, offline embedding and native ANN/diagnostic/depth jobs. No production/final-test adoption or capacity claim.
