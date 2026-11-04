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

The bounded API load baseline is complete. Read docs/api-load-baseline.md, decision0014/config and both reports/api-load-v1*.json before further performance work. Pinned native k6v2.0.0 uses constant-arrival-rate, 64preallocated/maxVUs, 3sclient deadline, 30s points20/100/200/500/1000, a fixed hash-selected300-query cohort (100/locale), top10 exact native IDs/scores, and two freshJava processes with100/s30swarmup and reversed rate order. Same immutable full ES index and unchanged APIJAR/resources. All109,205 measured HTTP requests are correct, with no errors/drops; both1000/s windows have all-attempt p50/p95/p99=1/4/7ms. Highest qualified tested offered rate is1000/s in both runs, using zero incorrect200 and>=99% correct completions/scheduled-slot budget. No numeric latency SLO, exact saturation limit or production capacity claim. Saturation was not reached. Endpoint scheduling differs by at most1slot, reconciled explicitly.

Critical caveat: first warm-up starts2966/3001, drops35 (1.17%), p99~807ms/max982ms; second warm-up3000/3000, no drops,p99=10ms. Both freshJava but retainedES/OS caches/history. Causes are unisolated. Do not bury warm-up loss or claim cold-start performance from warm measured windows. Both300-query recovery checks pass. All12raw NDJSON files are checksummed and independently recomputed for counts/percentiles/qualification; resources sampled1s. Cgroup lifetime peak~1.90GiB includes retained indexes/cache/native memory. psCPU is lifetime average; no directTomcat queue instrumentation. K6 VUs are not HTTP concurrency, dropped work has no latency sample, TTFB combines queue/service. Raw query/expected-output/metric/log files stay ignored. Runtime/experiment bindings stay enforced; past sharedCI workflow hashes are historical as CI evolves. CI still4jobs; main adds native controlled k6 success/wrong-result fixture. Both project services are stopped, volume preserved, unrelated services untouched.

Next propose small versioned API/backend instrumentation to measure request boundaries and investigate startup/queueing, keeping this tested runtime as a reference. Do not add rates or tune resources inside the closed load experiment. No neural-serving, concurrent-update/freshness, long-duration or final-test claim exists. ExactRRF stays offline reference55.27%; BM25fullcatalog50.29%. No ANN point is nominated (depth1024strict94.74%<95% budget). Honor its stopping rule and preserve source/lock/index/vector contracts, locale/query/group identities, incomplete-judgment semantics and official-test holdout. Unknown relevance remains unknown; coverage is not precision.
