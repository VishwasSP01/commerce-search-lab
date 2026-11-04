# Open-arrival API load baseline

This campaign measures the unchanged Java title-BM25 API on the full **1,814,924-record** Elasticsearch index. The [protocol](decisions/0014-api-load-baseline.md) fixes workload, rate order, duration, resources and qualification before measurement. This is a short local warm-cache study, with results and limitations recorded together.

Across the two measured campaigns, **109,205 HTTP searches** return correct native results, with **zero HTTP/transport errors, incorrect HTTP200s or dropped iterations**. The highest qualifying tested offered rate is **1,000 requests/s** in both 30-second windows: all-attempt **p50=1ms, p95=4ms, p99=7ms** in each run. Saturation was **not reached** within the fixed tested range. No numeric latency SLO or exact capacity ceiling is claimed.

| Offered requests/s | Correct completions/s, ascending / descending | All-attempt p50 ms | p95 ms | p99 ms | Max ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20 | 20.00 / 20.03 | 7 / 4 | 13 / 11 | 20 / 17 | 25 / 20 |
| 100 | 100.00 / 100.00 | 2 / 2 | 6 / 6 | 10 / 10 | 25 / 17 |
| 200 | 200.03 / 200.03 | 2 / 2 | 5 / 5 | 8 / 8 | 22 / 17 |
| 500 | 500.00 / 500.03 | 1 / 1 | 4 / 4 | 7 / 7 | 47 / 14 |
| 1,000 | 1000.03 / 1000.00 | 1 / 1 | 4 / 4 | 7 / 7 | 17 / 59 |

Each latency cell is ascending / descending. All measured attempts are correct successes, so their success-only and all-attempt distributions coincide; the report still retains both. Every measured point exercises all300 queries. One-slot scheduler endpoint differences explain completion rates slightly above the nominal offered rate. The lower-rate tail is higher than the high-rate tail here; this campaign does not isolate the cause of that non-monotonic curve.

**Startup caveat:** the initial warm-up starts2,966 of3,001 scheduled requests, drops **35 (1.17%)**, and has all-attempt **p99≈807ms**, maximum982ms. All requests that start return correct HTTP200s. The second warm-up starts all3,000, with no drops and p99=10ms. Both use a fresh Java process, but ES/OS caches are retained between them. These observations expose startup/history sensitivity without identifying its cause. Warm-up is excluded from the measured table by the committed protocol; its loss remains visible here and in the aggregate/raw evidence. A cold-start availability or latency claim would require a separate experiment.

Both post-load checks return correct results for all300 queries. Independent verification recomputes all12 raw-file summaries (ten measured points and two warm-ups), counters, percentiles, loss decisions and resource summaries, preserving workload/source/tool bindings. The two timing runs are observations rather than an assertion of reproducible latency.


## Workload and accounting

The workload contains **300 real validation queries**, selected deterministically by SHA256 with **100 per locale** and a global hash order. Every request asks for top10 and every successful response must exactly match saved native Elasticsearch product IDs/scores. The mix is deliberately balanced for multilingual stress; it does not model real traffic or validation-frequency weighting. No labels, relevance tuning, re-indexing or runtime changes occur.

Each fresh Java process gets a **30-second, 100-arrival/s warm-up**. Measured rates are **20,100,200,500,1000 arrivals/s**, **30 seconds** each, with five seconds idle between points. The second fresh Java process reverses rate order after the same warm-up. ES and OS caches are not reset between campaigns. Connections are reused within a point; each point launches a fresh k6 process, so initial client connection establishment remains in the measurements.

Pinned native **k6 2.0.0** uses the open `constant-arrival-rate` executor, **64 preallocated/max VUs**, one request per iteration, three-second client timeout and five-second drain grace. Arrival starts are independent of response speed until the bounded generator exhausts VUs. `dropped_iterations` records unsent work; dropped requests have no latency sample and are counted as loss rather than assigned an invented latency.

Every point reconciles scheduled work with started/completed/HTTP/elapsed samples. Planned slots are rate×duration; observed starts+drops may differ by one at a scheduler endpoint. The qualification denominator uses observed scheduled slots. Correct response throughput is completions divided by the declared arrival-window duration, including completions during grace; it is not an instantaneous completion-rate maximum.

The predeclared qualification rule requires **zero incorrect HTTP200s** and **at least99% correct completions per scheduled slot**, including all HTTP/transport failures and dropped work in loss. This is an experiment availability budget. No latency SLO is selected using these same results. The highest qualifying **tested offered rate in both runs** is not an exact saturation limit or a production capacity guarantee. When no tested point fails, saturation remains unobserved.

Client wall timing wraps the HTTP call and includes client blocking/connection/response completion, with millisecond clock resolution. Report p50/p95/p99/max over all attempts and separately correct successes. Raw k6 duration excludes blocking/connection, whereas TTFB/wait includes server queueing plus processing. Failure latency remains in all-attempt percentiles. Per-locale request counts and actual unique-query coverage are retained; overloaded/drop-heavy points may not complete the planned balanced cycles.

## Resource and queue observations

Resources retain **3GiB/two container CPUs/1GiB ES heap**, API **256MiB heap**, and existing16-worker/64-connection/backlog32 server limits. k6 and Java run on the same host as Docker Desktop. The volume also contains other retained full/sample/fixture indexes; this is not isolated single-index memory measurement.

One-second samples preserve ES process CPU, heap, cgroup memory/peak, search queue and rejection counters, plus task-owned Java/k6 `ps` CPU and RSS. `ps` CPU is a lifetime average; sampled maxima and counter deltas can miss brief events. The monitor and granular JSON writer add load, and the generator/server share resources. Host architecture/OS/logicalCPU count, tool and JAR hashes are recorded. No claim isolates the precise CPU bottleneck from these observations alone.

Observed container lifetime peak is **2,040,020,992 bytes (1.900 GiB)** across the campaign. The initial warm-up has a sampled ES search queue maximum of12; measured-rate samples show a maximum of1. Sampled ES search-rejection deltas are zero. One-second snapshots can miss short queue peaks or attribute cumulative rejection increments to an adjacent phase.

Tomcat queue occupancy is not directly instrumented. Client blocking, connection and TTFB timings, allocated VUs and dropped work provide evidence about the path; they do not separate server queueing from execution. A later instrumentation increment can measure those boundaries. Allocated VUs are not equivalent to simultaneous in-flight HTTP requests.

After each campaign, readiness must recover and all300 queries must return correct results at low sequential load. Preserve failed points and do not change rates/resources/parameters to improve a result. Final Java/ES processes are stopped, the data volume remains, and unrelated services are left alone.

## Reproduce

Prerequisites: the earlier full-catalog index and saved query/native-result artifacts, Java21, Docker running, and the unchanged verified API JAR. The runner checks runtime/source/input/indexUUID/count bindings. The historical workflow hashes from previous reports remain historical while CI evolves.

Install the checksummed native tool under ignored `data/tools/`:

```sh
uv run --locked --no-sync python scripts/install_k6.py
uv run --locked --no-sync python -m unittest discover -s tests -p test_api_load.py -v
uv run --locked --no-sync python scripts/k6_fixture.py
```

The official Mac ARM64 ZIP or Linux AMD64 tarball SHA-256 is pinned in [configuration](../config/api-load-v1.json). The installer verifies the archive before writing a single executable and records/verifies its binary digest. It changes no global installation or shell configuration. No cloud account, paid service, JavaScript package installation or remote script import is required. Usage reporting is disabled.

Run the declared campaign and independently recompute the report from raw samples:

```sh
uv run --locked --no-sync python scripts/run_api_load.py
uv run --locked --no-sync python scripts/verify_api_load.py
```

The runner starts only the existing project-owned backend and its own Java/k6 child processes. Port18080 must be unused. It refuses existing output/report paths; preserve/move the previous ignored `data/processed/api-load-v1` and published report before reproducing. Resource-monitor or process failures stop the run, and partial raw evidence is preserved. A runtime correctness failure stops further points; ordinary overload failures remain measured outcomes. A repetition is another observation, not a byte-identical timing requirement.

The [aggregate report](../reports/api-load-v1.json) records points, failures, percentiles, workload/tool/source hashes and recovery. The [independent verification](../reports/api-load-v1-verification.json) recomputes every count, percentile and qualification from the checksummed raw samples and summarizes resource observations. Raw NDJSON, query text, expected IDs/scores, k6 summaries, logs and one-second samples stay ignored. Aggregate reports do not redistribute the query dataset.

Four new unit tests check deterministic sampling, tail percentiles, accounting with failed/dropped work and incomplete-sample rejection. The native k6 fixture sends a few synthetic requests to a controlled loopback server, proving that correct results pass and wrong HTTP200s fail. CI runs those fixtures without Elasticsearch/ESCI; the existing native-ES/API checks continue separately. CI performance is not used as a benchmark result.

This is title-BM25, top10, fixed repeated warm queries and short durations over plain loopback HTTP, with no TLS/authentication/proxy, neural scoring or concurrent writes, on one declared local configuration. It establishes neither cold-cache behavior, long-duration stability, production SLOs nor performance for neural retrieval or concurrent updates. The next decision should use the observed curves and errors to choose a focused instrumentation or performance experiment, with any later numeric latency targets registered separately.
