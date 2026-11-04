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

The bounded Java 21/Spring Boot 4.1.2 API is implemented and verified. Read `docs/java-search-api.md`, decision 0012 and `reports/search-api-v1.json`. The independent Maven module is `services/search-api`; leave the frozen root POM/locks/experiment sources unchanged. POST /v1/search validates query/locale/limit and returns native ES scores (no /2.2). Liveness and readiness are separate; timeout, backend unavailable, overload, partial/malformed results are explicit errors. 12 new module tests cover meaningful failure behavior. Two Java processes reproduce all 300 saved queries at 100/default10/1: 1,800 checks, zero mismatches, both top-100 hashes equal the original ES artifact. Controlled ES outage yields search/ready 503 and live 200, then readiness recovers. Both project services are stopped after verification; ES volume is preserved. Keep the backend local-only and leave unrelated containers untouched. CI extends the real-ES fixture job with API tests and packaged HTTP checks; there are still four jobs.

Next propose full-catalog lexical serving feasibility with a declared resource envelope, index/result checks and resource observations. Current HTTP evidence uses 30,000 sample products, not full-catalog serving or API QPS/percentiles. Load, telemetry, circuit breakers, hybrid serving, training and streaming remain separate increments. Backend default: existing csl-pilot-es-v1-c at loopback19200; API loopback18080. Refresh is disabled in the immutable sample profile; it has no update path.

Exact RRF remains the offline quality reference at 55.27% validation known-Exact Recall@100. No ANN setting is nominated: depth1024 reaches 94.74% strict agreement, below the unchanged 95% overall budget. Honor that experiment's stopping rule. Keep all source/lock/index/vector contracts, locale/query/group identities, incomplete-judgment semantics and the official test holdout. Unknown relevance remains unknown; judgment coverage is not precision. No production/final-test adoption or capacity claim.
