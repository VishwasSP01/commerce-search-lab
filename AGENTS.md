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

The audit, splits, candidate-ranking BM25 baseline, error analysis, bullet ablation and full-catalog title BM25 evaluation are complete. Read `docs/full-catalog-bm25.md` and its protocol and aggregate/reproduction reports. Retain title-only; the bullet variant failed its adoption rule. Full-catalog quality claims must preserve known-Exact recall and incomplete-judgment caveats, locale scope, short/empty lists and deterministic cutoff ties. The next proposed increment is a bounded multilingual embedding compatibility/resource pilot, followed by dense/fusion comparison if feasible. Keep spelling/entity/training and later API/streaming work separate. Preserve the official test holdout. CI must require packaged baseline, diagnostic, weighted-field and catalog-retrieval integrations. A later relevance-eval bridge must preserve linear ESCI gains and exact query identity/coverage.
