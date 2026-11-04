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

Full-catalog lexical serving feasibility is complete. Read `docs/full-catalog-serving.md`, decision 0013/config and `reports/catalog-serving-v1*.json`. Two fresh ES indexes each contain 1,814,924 locale-product titles (ES260011/JP339059/US1215854). All 3,379 validation query rankings match frozen Lucene, normalized scores pass the fixed /2.2 comparison and all 3,478 probes match. Known-Exact Recall@100 remains BM25's 50.29%; coverage32.91% is not precision. The unchanged Java API reproduces 20,274 searches at100/default10/1 across both builds; 6,758 more top100 searches reproduce both indexes after graceful same-volume restart. All nine artifacts match fresh builds. Both UUIDs/counts/results persist. The verifier waits for green index health before counts; node/license readiness alone was insufficient, and the original failed readiness attempt is preserved locally.

Envelope remains ES3GiB/twoCPUs/1GiBheap, API256MiBheap, Python observed1GiB RSS stop threshold. Observed container peaks ~2.37GiB include old sample/fixture/other full-copy indexes; heap max is sampled, Python RSS excludes children, batches are not QPS/percentiles. Loader streams <=500docs/1MiB create batches, validates all responses, refuses existing indexes and does not retry ambiguous writes. Full indexes are write-blocked, one segment, refresh disabled; immutable full index names csl-pilot-catalog-v1-a/b. Preserve the volume, start only the owned Compose service, leave unrelated services untouched. API sample default stays unchanged; select the full index explicitly via --commerce.index. Do not change frozen runtime/experiment contracts. Shared CI workflow hashes from old reports are historical; its current loader step evolves while runtime hashes remain enforced. CI still has four jobs, with native immutable-loader/API fixtures.

Next propose one bounded API load campaign on the full index with an explicit workload, offered/achieved load, warm-up/cache rules, errors, queueing and latency distributions. No concurrent API capacity, percentile, freshness, streaming-recovery or production claim exists yet. Telemetry/circuit breakers, neural serving, int8, training and streaming remain separate increments. Exact RRF remains the offline reference at55.27%; no ANN setting is nominated (depth1024 agreement94.74% <95% budget). Honor its stopping rule and preserve locale/query/group identities, incomplete-judgment semantics and the official test holdout. Unknown relevance remains unknown.
