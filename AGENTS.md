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

The ESCI audit, splits, first BM25 baseline, validation error analysis and one predeclared title-plus-bullet ablation are complete. Read `docs/bm25-title-bullet.md` and its aggregate/reproduction reports. The bullet variant failed its adoption rule; retain the frozen title-only baseline and do not silently tune the weight. The next proposed increment is to define an incomplete-judgment evaluation protocol before full-catalog or neural-retrieval quality claims. Keep spelling/entity/neural changes and later API/streaming work separate. Preserve the official test holdout. CI must run Java/Python tests and require packaged baseline, diagnostic bridge and weighted-field integration checks.
