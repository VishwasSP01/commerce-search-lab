# Java search API on the verified lexical sample

The Java 21/Spring Boot **4.1.2** service exposes the verified Elasticsearch retrieval through HTTP. On the unchanged 30,000-product sample, all **300 validation queries** match the saved native Elasticsearch results at top 100, default top 10 and top 1. Two fresh Java processes perform **1,800 sequential search checks**, with zero mismatches. Both top-100 output files have the original native-result SHA-256 `d1585fa3ffc4eb5e9df19936092f8b6719334319ec28e17685612b8df1035786`.

This is functional compatibility evidence on the sample. The full-catalog quality reference remains exact RRF at 55.27% validation known-Exact Recall@100. This API currently serves title BM25. Full-catalog HTTP serving, concurrent load and latency percentiles require subsequent measurements.

## HTTP contract

`POST /v1/search`, `Content-Type: application/json`:

```json
{"query":"red running shoes","locale":"us","limit":10}
```

Required fields are `query` and `locale`; `limit` defaults to 10 and must be an integer from 1 to 100. Locales are exactly `us`, `es`, `jp`. A query must contain nonblank text and at most 256 Unicode code points both before and after NFKC normalization. Reject unpaired surrogates. The JSON body is capped at 8 KiB, including chunked bodies; unknown keys, nulls, wrong types, duplicate keys and trailing JSON are errors. Stop-only queries that analyze to no terms are valid and return an empty hit list.

The response schema below uses an **illustrative** product/score, not an observed search result:

```json
{"locale":"us","limit":10,"returned":1,"retrieval":"bm25","hits":[{"productId":"001","score":2.2}]}
```

IDs remain strings. Scores are **native Elasticsearch BM25 scores**; the previous parity experiment's comparison-only `/2.2` transformation is not applied. Results preserve backend score-descending/product-ID-ascending order and locale isolation. The query remains literal OR search with fixed title analyzers and no query-syntax interpretation. There is no padding of empty/short result lists.

| Condition | HTTP status | Error code |
| --- | ---: | --- |
| Invalid JSON or fields | 400 | `INVALID_REQUEST` |
| Body exceeds 8 KiB | 413 | `REQUEST_TOO_LARGE` |
| Unsupported media | 415 | `HTTP_415` |
| Backend connection unavailable, missing index, 429 or server error | 503 | `BACKEND_UNAVAILABLE` |
| More than 16 simultaneous backend calls | 503 | `OVERLOADED` |
| Backend/full-response deadline or ES `timed_out` | 504 | `BACKEND_TIMEOUT` |
| Failed shards or incomplete result status | 502 | `BACKEND_INCOMPLETE` |
| Malformed/oversized response, wrong locale/ID/order or unexpected backend status | 502 | `BACKEND_INVALID_RESPONSE` |

Errors contain only `{code,message}` and omit raw backend responses and query text. Invalid successful backend results never become successful partial lists. The parser checks one successful primary shard, finite nonnegative scores, uniqueness, requested cutoff, identity and ordering. Catalog membership remains an offline prerequisite; the API does not load the 1.8M-product identity set into memory.

`GET /health/live` reports the Java process. `GET /health/ready` checks green single-primary health for the configured index. A failed dependency leaves liveness `200` and readiness `503`. Readiness does not prove corpus identity or assess all indexed documents. The verification command separately checks backend version/plugin/license, owned container limits, sample document count and saved result identity.

## Local operation

The service has an independent Maven POM under `services/search-api`; the root baseline POM/locks stay frozen. Dependencies are pinned through the Boot parent/BOM. Use Java 21 and the existing wrapper:

```sh
./mvnw -f services/search-api/pom.xml --batch-mode --no-transfer-progress verify
docker compose -f infra/elasticsearch/compose.yml start elasticsearch
java -Xms64m -Xmx256m -jar services/search-api/target/commerce-search-api.jar
```

The default backend index is the existing **`csl-pilot-es-v1-c`**, from the [parity pilot](elasticsearch-pilot.md). The runbook for that pilot explains how to create an equivalent sample in a fresh environment. For another concrete task-owned sample index, pass `--commerce.index=csl-pilot-your-index`; never point parity claims at a different corpus without rechecking. The backend URL defaults to `http://127.0.0.1:19200`, accepts only explicit loopback HTTP endpoints, and refuses credentials, paths and wildcard indexes. A reused JDK client disables redirects/proxies.

The API binds to **`127.0.0.1:18080`** by default. In another terminal:

```sh
curl --fail http://127.0.0.1:18080/health/ready
curl --fail http://127.0.0.1:18080/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"running shoes","locale":"us","limit":10}'
```

Backend guardrails: 300 ms connect timeout, two-second deadline including full response-body completion, one-second ES query timeout, 1 MiB backend body cap, and at most 16 in-flight backend calls. Expired requests are cancelled. The initial Tomcat settings allow 16 worker threads, 64 connections and an accept backlog of 32. These are declared starting bounds, not measured capacity targets or a complete end-to-end request deadline. Queueing and slow inbound request bodies remain relevant to a later load campaign. There are no application retries, circuit breaker, alternate retriever or authenticated/public deployment profile in this increment.

Stop the foreground Java process with Ctrl-C. It uses graceful shutdown with a five-second Spring phase budget. Stop only the project-owned backend afterward, preserving its volume:

```sh
docker compose -f infra/elasticsearch/compose.yml stop elasticsearch
```

## Verification and limitations

The **12 new Java tests** exercise the real HTTP boundary against controlled responses: validation, Unicode/width normalization, literal query construction, native scores and string IDs, locale/limit behavior, empty results, backend/index absence, partial/malformed/oversized results, slow headers/body cancellation, recovery, health semantics, bounded concurrency and refused connections. They use synthetic fixtures.

The real-sample verification command starts/stops only its own Java child processes. It requires an unused port 18080 and the verified sample artifacts. It performs all three limit checks twice, gracefully restarting Java between runs. It also stops/restarts the owned Elasticsearch service to verify dependency failure behavior, then leaves Java and Elasticsearch stopped with the volume preserved. Docker Desktop and unrelated services are left alone.

```sh
uv run --locked --no-sync python scripts/verify_search_api.py
```

The published [aggregate report](../reports/search-api-v1.json) records all result hashes, failure checks, JAR/source bindings and frozen pilot provenance. Detailed ranked files and process logs stay under ignored `data/processed/search-api-v1`. The command refuses existing evidence paths; preserve/move that directory and the published aggregate report before reproducing. Starting the backend is required before running it. Nothing is re-encoded, re-indexed or tuned, and the official test stays reserved.

CI extends the existing real-Elasticsearch job: package/test this Java module and run `scripts/verify_search_api.py --fixture` against the tiny synthetic index, including both Java processes and all three result limits. The other three jobs continue to check the frozen experiments. CI does not fetch ESCI or model weights.

The next increment is full-catalog lexical serving feasibility with a declared resource envelope and result checks. Load targets, telemetry, circuit breakers, neural serving and ingestion follow as separate measured increments. See the [API decision](decisions/0012-java-search-api.md).
