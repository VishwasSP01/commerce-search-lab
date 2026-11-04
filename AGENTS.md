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

API/backend instrumentation is complete in version0.2.0. Read docs/api-request-timings.md, decision0015/config and reports/api-timings-v1*.json. The exact old0.1.0 JAR is preserved under data/reference/search-api-0.1.0 with SHA e488f68dacd7baa8c9648ba3a8fb30b29b404f9fa003d0d6b27d731d8c6cf2d0 and source ref f6a2acffcc4f7c3c885ac23914f6dde67d1eaccf. Old current-source/JAR guards intentionally reject0.2.0: use the old ref/worktree and reference artifact to reproduce closed experiments; do not rewrite prior reports or weaken guards. RootLucene POM/locks and closed experiment scripts/reports stay unchanged. Shared CI workflow hashes remain historical as CI evolves.

0.2.0 adds bounded Micrometer/Prometheus meters, JVM/HTTP/Tomcat MBean metrics, default-off local timing events, server-generated response UUID, fixed ES client X-Opaque-Id plus outgoing unsampled W3C traceparent. No incoming trace continuation/OTel SDK span export or collector/dashboard deployment claim. Capture filter entry before identity allocation; backend operation, request-build, full-response HTTP wait, JSON decode, result validation and ES took have explicit scopes. Clear thread context on all paths and keep query/body/result/IDs out of metric labels. ES took includes search queue/execution; paired client-minus-filter residuals are not isolated Tomcat queueing. Do not add nested timers or subtract marginal percentiles.

Final four100/s30s bursts use order0.2/0.1/0.1/0.2 with fresh JVM/ES restarts and retained OS caches. All11,979 started searches are correct;23 scheduled arrivals are dropped. Both observed bursts join5,990 responses exactly, no missing IDs/unmatched server events.900 functional checks at100/default10/1 match saved results. Independent audit recomputes raw metrics/joins and Prometheus request/stage/took counts and seconds; inflight drains. Slowest paired examples client678/713ms, filter~677/709ms, HTTPwait~676/682ms, ES took618/671ms. Other requests have large unmeasured residuals;896 negative residuals are retained. This narrows observed layers, not historical root cause or causal overhead. Two implementation corrections (opaque-ID policy/filter boundary) and interrupted preliminary candidates are preserved and excluded from final data; rates/order/resources were unchanged.

17 API tests and95 main Python tests pass locally; CI has4jobs with tiny native metrics/correlation fixture. Both project services are stopped; volume and unrelated services preserved. Next propose a bounded, versioned search-path warm-up/readiness experiment against health-only readiness, with registered targets before measurement. Do not tune the closed load study or claim a performance fix. Closed0.1 warm study remains109,205 correct measured searches,1000/s windows p50/p95/p99=1/4/7ms, but first warm-up35drops/p99~807ms and saturation unobserved. ES3GiB/twoCPUs/1GiBheap andAPI256MiBheap remain. ExactRRF stays offline reference55.27%, BM25fullcatalog50.29%, ANN unadopted (94.74%<95%). Preserve locale/query/group identities, incomplete-label semantics and official test holdout. Unknown relevance stays unknown; coverage is not precision.
