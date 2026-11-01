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

The audit, splits, candidate-ranking BM25, error analysis, bullet ablation, full-catalog title BM25 and multilingual embedding pilot are complete. Read `docs/embedding-pilot.md` and `docs/full-catalog-bm25.md` and their protocols/reports. Retain title-only as the measured retrieval baseline; the bullet variant was rejected and the pilot measures feasibility, not relevance. The next proposed increment is a checkpointed full-catalog title embedding export, then a separate dense/fusion comparison. Preserve encoding/model/sample/runtime contracts and measured artifacts on restart; do not infer sustained capacity or total host/GPU peak from short pilot timings or MPS snapshots. Preserve known-Exact/incomplete-judgment caveats, locale scope and deterministic ties. Keep training, spelling/entity, API and streaming separate. Preserve the official test holdout. CI must require all packaged integrations and offline CPU encoder fixtures. Keep the frozen root uv.lock/source fingerprints intact; embedding dependencies have a separate project/lock. A later relevance-eval bridge must preserve linear ESCI gains and exact query identity/coverage.
