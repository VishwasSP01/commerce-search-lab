# Local Elasticsearch lexical parity pilot

The bounded pilot passes: **30,000 independently sampled product titles**, **300 validation queries** and **399 analyzer probes** agree with the frozen Lucene reference under the [predeclared protocol](decisions/0011-elasticsearch-parity-pilot.md). Two fresh Elasticsearch indexes and two fresh reference indexes reproduce all nine input/analyzer/retrieval/parity artifact hashes. This establishes observed lexical compatibility on this sample; the full-catalog quality reference remains exact RRF at 55.27% known-Exact Recall@100.

| Observed comparison | Spanish | Japanese | English | Total |
| --- | ---: | ---: | ---: | ---: |
| Metadata products | 10,000 | 10,000 | 10,000 | 30,000 |
| Validation queries | 100 | 100 | 100 | 300 |
| Queries with different ordered top-100 IDs/counts | 0 | 0 | 0 | **0** |
| Queries with normalized scores outside tolerance | 0 | 0 | 0 | **0** |
| Zero-result queries in both backends | 1 | 8 | 1 | 10 |

There are 21,214 returned query-product pairs, with no padding of short or empty lists. All 399 analyzed strings match on terms, positions, position lengths and UTF-16 offsets. These strings comprise the 300 queries, 30 sampled titles per locale, and nine fixed edge probes; token streams for the other titles were not individually compared.

## Compatibility choices

Elasticsearch **9.5.6** bundles Lucene **10.5.2**; the unchanged offline reference uses **10.6.0**. Both use NFKC-normalized titles, locale-specific fields, BM25 **k1=1.2, b=.75**, a literal OR query, locale filtering and product-ID tie ordering. One primary shard and a single merged segment avoid distributed score aggregation in this pilot.

Elasticsearch's pinned legacy BM25 implementation multiplies by `k1+1`, so the fixed comparison divides native Elasticsearch scores by **2.2**. Raw scores remain in the result artifacts. The largest normalized absolute error is **4.0 × 10⁻⁶**; every pair satisfies `math.isclose` with predeclared absolute tolerance **1e-6** and relative tolerance **1e-5**. This is a known formula difference, not a scale fitted to the sample. It would matter for downstream raw-score features, even when rankings match.

A tiny synthetic fixture caught an incorrect initial assumption about Japanese compound handling. Inspection of the packaged frozen analyzer confirmed that it discards original compound tokens in SEARCH mode. The Elasticsearch setup was corrected **before** the real-data pilot; the frozen baseline and previous experiments were not changed. The fixture also covers empty/stop-only queries, repeated terms, literal punctuation, width normalization and repeated product IDs in different locales.

Finite agreement across different Lucene versions does not prove universal analyzer or scorer equivalence. Multi-shard search, additional fields, updates, dense retrieval and hybrid serving need their own checks. No sample relevance score is presented as a replacement for the independently measured 1.8M-product results. The official test remains reserved.

## Resource and lifecycle observations

The local free Basic node runs with **two container CPUs, 3 GiB memory limit and fixed 1 GiB JVM heap**, bound only to `127.0.0.1:19200`. ML and GeoIP downloads are disabled. The image digest and Kuromoji plugin checksum are pinned in the [configuration](../config/elasticsearch-pilot-v1.json) and [Dockerfile](../infra/elasticsearch/Dockerfile).

The first index occupies **6,296,104 bytes (6.00 MiB)** after refresh/merge. Its observed heap use is **269,399,040 bytes (256.92 MiB)**, with 1 GiB committed/max. Cgroup memory after that run is **1,510,395,904 bytes (1.407 GiB)**, including JVM, native memory and cache. This container also held the tiny fixture indexes; the repeat adds another 30,000-product index. Cgroup peaks are container-lifetime observations, not isolated per-run or whole-machine RSS. Do not extrapolate these sample sizes to a full-catalog capacity claim.

| Recorded batch duration, seconds | First | Fresh repeat |
| --- | ---: | ---: |
| Reference build and retrieval | 1.712 | 1.528 |
| Elasticsearch load, refresh and merge | 1.543 | 0.952 |
| Elasticsearch HTTP retrieval batch | 0.673 | 0.421 |

The repeat values are recorded in the [repeat evidence](../reports/elasticsearch-pilot-v1-reproduction.json). These are sequential bounded batches, with setup/JVM/cache effects and concurrent local processes; they are **not** search API latency percentiles, QPS or a controlled backend speed comparison.

The verification command gracefully stops and restarts only this Compose service on its existing project volume. Both index UUIDs and 30,000-document counts survive, and all 300 queries on each index reproduce their original result-file hashes. Graceful stop takes **2.565 seconds**; restart to ready/green takes **12.279 seconds**, including the Compose command and readiness polling. It finishes by stopping the service and preserving the volume. This checks graceful local persistence, not crash recovery or delivery of streaming updates. Docker Desktop and unrelated services remain running.

## Reproduce locally

Prerequisites: Docker Engine/Desktop running, Java 21, `uv`, the audited product parquet and prepared validation partition, and the frozen reference JAR. No model weights or cloud account are required. The backend image/plugin download requires network access and roughly 750 MB before Docker storage overhead; allow room for build layers. This profile has security disabled and must remain loopback-only.

```sh
uv sync --locked
./mvnw --batch-mode --no-transfer-progress verify
docker compose -f infra/elasticsearch/compose.yml up -d --build
REQUIRE_BM25_JAR=1 uv run --locked --no-sync python -m unittest discover -s tests -v
uv run --locked --no-sync python scripts/elasticsearch_fixture.py
uv run --locked --no-sync python scripts/run_elasticsearch_pilot.py
uv run --locked --no-sync python scripts/run_elasticsearch_pilot.py \
  --index csl-pilot-es-v1-b \
  --output-dir data/processed/elasticsearch-pilot-v1-repeat \
  --report data/processed/elasticsearch-pilot-v1-repeat-report.json
uv run --locked --no-sync python scripts/verify_elasticsearch_pilot.py
```

The commands intentionally refuse existing indexes or evidence paths. In a clone, the published aggregate JSON reports already exist: preserve them, and move them out of the default report paths before reproducing the two runs and verification. Detailed generated inputs, reference indexes and ranked lists stay under ignored `data/processed/`. To retain an existing run, choose fresh `--index`, `--output-dir` and `--report` paths; the fixed verification command expects the two default run locations above. Never remove someone else's container, index or volume to make a rerun work.

To resume or stop just this backend:

```sh
docker compose -f infra/elasticsearch/compose.yml start elasticsearch
docker compose -f infra/elasticsearch/compose.yml stop elasticsearch
```

For readiness, `curl --fail http://127.0.0.1:19200/` exposes the backend version; `curl --fail 'http://127.0.0.1:19200/_cluster/health?wait_for_status=green&timeout=30s'` checks shard health. The scripts additionally enforce Basic license, plugin version, ownership/resource envelope, exact document counts and complete responses. If a bulk call fails, inspect its item failures and the concrete task-owned index; do not blindly retry ambiguous writes. Refresh is disabled during this immutable batch pilot, so newly written documents require an explicit refresh. This profile does not yet implement an update path.

CI adds a real-Elasticsearch synthetic fixture job alongside the Java/Python, embedding and ANN jobs. It builds the pinned image on Ubuntu, checks the small multilingual corpus, and removes only its own temporary stack. No ESCI download or benchmark rerun occurs in CI.

## Decision and next increment

Retain this local Elasticsearch profile as the lexical backend candidate for a **small Java 21 search API**. The next increment should declare the API contract and failure behavior, then verify it against this bounded corpus. Full-catalog indexing, API load targets, circuit breakers, neural serving and update ingestion remain separate work.

Evidence: [first aggregate report](../reports/elasticsearch-pilot-v1.json), [fresh repeat and graceful-restart verification](../reports/elasticsearch-pilot-v1-reproduction.json), and [verification command](../scripts/verify_elasticsearch_pilot.py).
