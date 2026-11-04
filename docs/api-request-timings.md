# Versioned request and backend timing instrumentation

API **0.2.0** adds Micrometer/Prometheus metrics and opt-in correlated timing events. The exact **0.1.0** JAR and source ref remain the reference for the closed load study. Query construction, scoring, response bodies/statuses, timeouts and resource limits are retained.

The **900 functional searches** (300 queries at100/default10/1) reproduce saved native IDs/scores with zero mismatches. All final diagnostic searches that start return correct HTTP200s. Both instrumented bursts have exact client/event joins, no missing returned IDs/unmatched server events, and independently verified Prometheus request/stage/took counts and seconds.

| Order | Version | Started / scheduled | Dropped | Client p99 ms | Max ms | Correlated requests |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.2.0 | 2,997 / 3,000 | 3 | 459.04 | 678 | 2997 |
| 2 | 0.1.0 | 2,993 / 3,000 | 7 | 500.56 | 703 | reference: no events |
| 3 | 0.1.0 | 2,996 / 3,001 | 5 | 496.20 | 689 | reference: no events |
| 4 | 0.2.0 | 2,993 / 3,001 | 8 | 511.64 | 713 | 2993 |

These are startup-like bursts with ES restarts and retained OS caches, not the old warm load windows. They retain scheduler losses and do not demonstrate a performance fix or causal instrumentation overhead.

Two actual slowest paired requests illustrate the boundaries (each row is one request; no percentile subtraction):

| Instrumented burst | Client ms | Filter ms | Full-response HTTP wait ms | ES took ms | Paired client−filter residual ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 678 | 676.845 | 675.505 | 618 | 1.155 |
| 4 | 713 | 708.953 | 682.485 | 671 | 4.047 |

Across **5,990 paired requests**, these examples locate substantial observed waiting on the ES HTTP path. ES took includes queueing and execution, so this does not identify pure ES CPU time. Other requests have substantial unmeasured client−filter residuals: their paired-residual p99s are about300ms and354ms. Those values are individually paired duration differences, not differences of marginal p99s, and are not isolated Tomcat queue time. There are896 negative residuals retained rather than clamped; the timing boundaries and clock resolution permit these differences.

The measurements narrow the observed layers without proving why the earlier807ms warm-up spike happened. A bounded readiness/search-path warm-up experiment is a follow-up hypothesis, not an implemented performance fix.


## What the measurements cover

| Observation | Boundary | Important limitation |
| --- | --- | --- |
| Client wall duration | HTTP call through full response, including client blocking/connect | Millisecond resolution; dropped arrivals have no duration |
| Filter duration | Captured filter entry, including generated identity, through synchronous MVC response serialization | Excludes work before filter entry and telemetry completion work after the chain |
| `search.operation` | Backend operation, including its child stages and bookkeeping | Nested total; do not add it to its child durations |
| `search.request_build` | Outgoing request/JSON construction | Earlier map/input parsing and later bookkeeping sit outside this child stage |
| `search.http_wait` | JDK send through completion of the full response body | Includes client/connection/network/ES queue/execution/response work |
| `search.json_decode` | Parse returned ES JSON | Separate from incoming request parsing |
| `search.result_validation` | Validate shard/status/hit identities/order and construct results | Includes error paths |
| ES `took` | ES coordinating-node receipt through ready-to-respond | Includes ES search queue and execution; excludes transit and JSON response serialization; integer milliseconds |

The analyzer joins client and server events **per request**. Client-minus-filter residuals retain negative values from clock resolution/finalization effects. They combine unmeasured client/connect/network/dispatch/pre-filter work; they are not a direct measurement of Tomcat queue time. Marginal percentiles are never subtracted to claim a stage duration.

Request UUIDs are returned as `X-Request-ID`; caller IDs are ignored. An outgoing unsampled W3C `traceparent` uses the UUID without hyphens as its trace ID. ES `X-Opaque-Id` is the fixed client identity `commerce-search-lab-api`, following Elastic's bounded-cardinality guidance. This is trace-context correlation support, with no incoming trace continuation, OpenTelemetry SDK/exporter or recorded distributed-span deployment in this step.

## Metrics and events

`GET /actuator/prometheus` exposes the scrape format on the same loopback-only listener. Only health/prometheus actuator endpoints are exposed. Custom meters are:

- `commerce_search_request_seconds_*`: filter duration/count by bounded locale/status/outcome.
- `commerce_backend_stage_seconds_*`: operation/stage/outcome durations/counts, including errors.
- `commerce_elasticsearch_took_seconds_*`: observed ES took, converted from milliseconds to seconds; observed zero differs from missing.
- `commerce_backend_inflight`: accepted backend calls across search/readiness, draining to zero.

Actuator also exposes JVM/process/HTTP meters. Tomcat MBeans enable busy/max-thread gauges; those do not directly expose connector queue occupancy. The actuator health endpoint is not a substitute for the existing index-aware `/health/ready` endpoint.

Metric labels never include request/query/product IDs, query text or raw URLs. Custom histograms use bounded stage/outcome/locale/status sets and explicit duration bounds. Histograms are bucket approximations; exact diagnostic percentiles come from local raw events/metrics.

Per-request JSON events are **disabled by default**. Enable them only when needed:

```sh
java -Xms64m -Xmx256m -jar services/search-api/target/commerce-search-api.jar \
  --commerce.index=csl-pilot-catalog-v1-a \
  --commerce.telemetry.request-events.enabled=true
```

`REQUEST_TIMING` lines contain generated ID, locale, outcome, observed status, filter nanoseconds, fixed stage nanoseconds and nullable ES took. They contain no query text, body, returned product IDs or results. Unmeasured stages remain absent, and unknown took remains null. The thread-local context is cleared on success/error paths and concurrent requests retain distinct IDs. Logging is best effort and adds diagnostic work; metrics remain enabled when events are off.

For a running Prometheus scraper, useful expressions include:

```promql
sum(rate(commerce_search_request_seconds_count{outcome="success"}[5m]))
histogram_quantile(0.99,
  sum by (le) (rate(commerce_backend_stage_seconds_bucket{operation="search",stage="http_wait"}[5m])))
```

This repository currently exposes/validates the scrape endpoint and saves snapshots. It does not deploy a Prometheus server or Grafana dashboards as part of this increment.

## Versioning and reproduction

The independent Maven module now produces **0.2.0**:

```sh
./mvnw -f services/search-api/pom.xml --batch-mode --no-transfer-progress verify
```

The original0.1.0 JAR is preserved under ignored `data/reference/search-api-0.1.0/commerce-search-api.jar`, with its recorded SHA-256 `e488f68dacd7baa8c9648ba3a8fb30b29b404f9fa003d0d6b27d731d8c6cf2d0`. Its source ref is `f6a2acffcc4f7c3c885ac23914f6dde67d1eaccf`. A separate checkout/worktree at that ref is the correct source context for reproducing the old closed experiments. Their current-source/JAR guards intentionally reject a different runtime; do not edit old reports or weaken guards to accept0.2.0. The root Lucene POM/locks and closed experiment scripts/reports stay unchanged.

The [registered diagnostic](decisions/0015-api-request-timings.md) reuses the exact previous300-query workload, top10 and full-index UUID. Four fresh Java processes run100 arrivals/s for30s, order0.2/0.1/0.1/0.2, restarting only the owned ES service and waiting for green index health before each. OS caches are retained. Instrumented processes have Prometheus snapshots before/after; those endpoint calls and optional logging add work. The controls lack that endpoint. No causal instrumentation-overhead comparison follows from these history/scrape differences.

```sh
docker compose -f infra/elasticsearch/compose.yml start elasticsearch
uv run --locked --no-sync python scripts/verify_api_timings.py
uv run --locked --no-sync python scripts/recompute_api_timings.py
```

The command refuses existing evidence/report paths; preserve/move earlier outputs before reproducing. It needs the recorded reference JAR, pinned k6, current JAR, full index and previous workload/native-result artifacts. After diagnostics it checks all300 queries at100/default10/1 against saved native results with events disabled. Generated IDs/events/client logs/k6 raw points/Prometheus snapshots/resource samples and both reference candidate archives stay under ignored data. Aggregate reports include anonymized numeric examples and hashes.

The independent audit recomputes raw counts/percentiles and exact joins, reconciles client event multisets with raw k6 metrics, and checks Prometheus request/stage/took **counts and seconds** against server events. Backend in-flight must drain to zero. Responses without returned correlation headers and unmatched server events remain explicit; no identity is invented. Every actual observed response is still checked against native results.

Before final measurement, implementation review corrected two issues: unique per-request opaque IDs were replaced with fixed opaque/W3C headers, and the filter timestamp was moved ahead of UUID creation. The interrupted preliminary candidates, exact JARs/source refs and observations are preserved and excluded from final results. These were registered measurement/propagation corrections, with no load rate/order/resource/qualification tuning. A k6 structured-console decoding fixture also prevents escaped JSON from corrupting joins.

CI retains four jobs. The API suite now has17 tests (five new instrumentation cases), plus three Python correlation tests. The native backend job checks the packaged metrics/event join on a tiny real-ES fixture, with no production-rate load, real data or preserved reference JAR required. The original API/failure tests and native correctness checks remain active.

The diagnostic keeps ES3GiB/twoCPUs/1GiBheap and API256MiBheap. Both project services finish stopped, preserving the volume and unrelated services. No latency SLO, performance fix, higher-rate claim, cold-cache guarantee or historical root-cause proof is established here. A next experiment can test a bounded search-path warm-up/readiness policy against health-only readiness, using these boundaries to evaluate the hypothesis.
