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

The audit, splits, candidate BM25, error analysis, bullet ablation, catalog BM25, embedding pilot, full-catalog export and exhaustive dense retrieval comparison are complete. Read `docs/full-catalog-dense.md` and its protocol/reports. Dense known-Exact Recall@100 is 49.48% versus BM25 50.29%, paired 95% delta interval [-1.77, +0.11] percentage points; Spanish regresses, English improves. No overall dense gain is demonstrated. Keep BM25 as the baseline. Both dense runs reproduce query vectors/results/metrics exactly; all 1,814,924 locale-product vectors remain saved. The next proposed increment is a separate, predeclared RRF comparison using the saved BM25/dense lists, including language regressions and actual known-Exact recovery in the fixed vocabulary-gap cohort; do not assume a fusion gain. ANN/quantization follow separately. Preserve encoding/model/source/runtime contracts and measured artifacts; do not modify frozen encoder/checkpoint/lock/retrieval sources silently. Preserve locale scope, known-Exact/incomplete-judgment caveats, exact query/group coverage and deterministic cutoff ties. Do not infer whole-host/GPU peaks or production capacity from RSS, MPS observations or batch timings. Keep training, spelling/entity, API and streaming separate. Preserve the official test holdout. CI must require packaged integrations and offline CPU encoder/export/search fixtures. Root uv.lock and prior source fingerprints remain frozen; reuse the separate embedding environment or isolate new dependencies. A later relevance-eval bridge must preserve linear ESCI gains and exact query identity/coverage.
