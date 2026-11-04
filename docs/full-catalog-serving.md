# Full-catalog lexical serving feasibility

The unchanged Java API now has a verified full-catalog Elasticsearch backend: **1,814,924 locale-product titles** and **3,379 validation queries**. Both fresh builds pass every declared comparison: **3,478 analyzer probes**, identical ordered Lucene top100 IDs/counts, normalized-score agreement within the fixed tolerance, and **20,274 API checks** at 100/default10/1 against native Elasticsearch across the two builds. All nine analyzer/result/parity/metric/API artifact hashes reproduce exactly. A same-volume restart preserves both distinct index UUIDs/counts and all **6,758** restored top100 API results, for **27,032 functional searches** in total. Both project services finish stopped with the volume preserved.

| Indexed locale | Products |
| --- | ---: |
| Spanish | 260,011 |
| Japanese | 339,059 |
| English | 1,215,854 |
| Total | **1,814,924** |

The corpus is the pinned ESCI metadata catalog, including unjudged products. Each query searches only its locale. Known-Exact Recall@100 remains **50.29%**, with the same query-weighted 95% interval **[49.07%, 51.50%]** as the offline BM25 baseline. Judgment coverage@10 remains **32.91%**; it is not precision. There are 324,567 returned query-product pairs and 26 zero-result queries. Full-catalog relevance, uncertainty and per-locale metrics are computed with the frozen metric implementation. The offline exact-RRF quality reference remains **55.27%**; this API serves title BM25.

## Method and boundaries

The [protocol and stopping rules](decisions/0013-full-catalog-serving.md) were committed before indexing. The loader streams the previously verified 303,680,512-byte locale/ID/Base64-title export. It preserves string IDs and checks sorted uniqueness, input/source hashes and every bulk response item. Requests contain at most 500 products and 1 MiB of UTF-8 JSON. There is one sequential indexing stream, with no blind retries.

The backend retains Elasticsearch 9.5.6, Lucene 10.5.2, Kuromoji and the pilot's locale-specific mappings, title-only BM25 k1=1.2/b=.75, literal OR query and score/ID sorting. The frozen Lucene reference is 10.6.0. Score comparison uses the predeclared ES `/2.2` factor and absolute/relative tolerances **1e-6/1e-5**. The HTTP response preserves native ES scores.

After loading, the index is refreshed, write-blocked, asynchronously merged to one segment and flushed. Counts are verified globally and per locale, with zero deleted documents and a green primary. This immutable comparison procedure does not establish an operating policy for a live update pipeline. Force merge has an explicit ten-minute polling deadline; the task is uncancellable and must be inspected after an ambiguous failure rather than resubmitted.

Analyzer comparison covers all query strings, the first 30 export titles per locale and nine fixed edges. It does not enumerate every product's token stream. Finite agreement across these Lucene versions is observed evidence, not a universal compatibility proof. Every retrieved ID is checked against the indexed locale catalog. The official test remains unopened.

## Resource observations

The envelope stays **two container CPUs, 3 GiB container memory, 1 GiB ES heap**, with the existing API launched at **256 MiB heap**. The Python coordinator has a 1 GiB observed peak-RSS stop threshold. No model weights, cloud services or additional spending are involved.

The first merged index occupies **308,408,317 bytes (294.12 MiB)**. First-run container peak is **2,547,154,944 bytes (2.372 GiB)**, sampled maximum JVM heap use is **743,385,936 bytes**, and Python process peak RSS is **769,081,344 bytes**. The container also holds the earlier sample/fixture indexes. The second build retains the first full index too: copies used for reproducibility are not additional unique products. Index UUIDs and inventory are recorded in the reports.

The owned service restarts before each build, so its recorded cgroup peak begins with that startup; it includes native memory, cache and other resident indexes. JVM heap use is sampled every five seconds and is not a continuously measured peak. Python peak RSS covers the coordinator, not Java, Docker Desktop or the whole machine.

The first build uses 3,630 validated bulk requests: **42.540 seconds** in the streaming bulk loop, then **5.056 seconds** waiting for merge completion. Its native HTTP retrieval batch takes **9.897 seconds** and its 10,137-check API functional batch **28.163 seconds**, including Java process setup/shutdown. These observations are sequential batches with cache/setup effects. They do not measure p95/p99, concurrent QPS, steady update throughput or freshness, and must not be divided into a production-capacity claim.

| Recorded observation | First build | Fresh repeat |
| --- | ---: | ---: |
| Streaming bulk loop, seconds | 42.540 | 41.589 |
| Merge wait, seconds | 5.056 | 5.060 |
| Native retrieval batch, seconds | 9.897 | 9.808 |
| API functional batch, seconds | 28.163 | 29.390 |
| Container peak, GiB | 2.372 | 2.369 |
| Sampled maximum JVM heap use, GiB | 0.692 | 0.811 |
| Python peak RSS, GiB | 0.716 | 0.719 |

## Reproduction and operation

Prerequisites: the earlier audited/prepared datasets, verified full title export and catalog-retrieval artifacts, Java 21, Docker running, the pinned Elasticsearch image/plugin and the existing verified API JAR. At least 10 GiB free disk is required. The script verifies runtime source/JAR/input bindings before writing. The shared CI workflow evolves; the old API report's workflow hash remains historical while its runtime and experiment bindings stay checked.

```sh
docker compose -f infra/elasticsearch/compose.yml start elasticsearch
uv run --locked --no-sync python scripts/run_catalog_serving.py
docker compose -f infra/elasticsearch/compose.yml start elasticsearch
uv run --locked --no-sync python scripts/run_catalog_serving.py \
  --index csl-pilot-catalog-v1-b \
  --output-dir data/processed/catalog-serving-v1-repeat \
  --report data/processed/catalog-serving-v1-repeat-report.json
uv run --locked --no-sync python scripts/verify_catalog_serving.py
```

Each build refuses existing index/evidence paths and stops the owned ES service afterward. Preserve published aggregate reports and previous detailed outputs before reproducing in a clone, then choose fresh `--index`, `--output-dir` and `--report` paths as needed. The repeat verifier expects the two default run locations above. Its initial restart attempt caught a readiness race: node/license availability preceded shard recovery, yielding a transient count 503. The verifier now waits for green index health before counts; a regression test covers this boundary, and the failed attempt is preserved in ignored local evidence. Bulk/merge failures preserve partial owned indexes and resource evidence for inspection; they are not automatically retried or deleted. Detailed ranked outputs, probes, logs and five-second resource samples remain under ignored `data/processed/`.

To search the existing full index manually, select it explicitly; the API's sample default remains unchanged:

```sh
docker compose -f infra/elasticsearch/compose.yml start elasticsearch
java -Xms64m -Xmx256m -jar services/search-api/target/commerce-search-api.jar \
  --commerce.index=csl-pilot-catalog-v1-a
```

In another terminal, use the [documented API requests and error contract](java-search-api.md). Both services remain bound to loopback. Stop Java with Ctrl-C, then `docker compose -f infra/elasticsearch/compose.yml stop elasticsearch`; preserve the volume and leave unrelated services alone. Full-catalog indexes are write-blocked and automatic refresh remains disabled; there is no update path.

Eight new Python tests cover export identity/order corruption, byte/document caps, partial write failures, existing-index protection, merge error/deadline handling, locale-count drift and restored-primary readiness. The tiny native Elasticsearch loader fixture verifies actual bulk import, asynchronous merge, write-blocking, counts and searchability in the existing CI job. CI uses no ESCI or pretrained-model downloads.

Evidence: [first full-catalog report](../reports/catalog-serving-v1.json), [repeat and persistence verification](../reports/catalog-serving-v1-reproduction.json), [configuration](../config/catalog-serving-v1.json). The next increment is a bounded API load campaign with a declared workload, offered/achieved load, errors, queueing and latency distributions. Neural serving, streaming updates and final-test release decisions remain separate.
