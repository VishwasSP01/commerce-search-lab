package lab.commerce.api;

import java.io.IOException;
import java.net.Proxy;
import java.net.ProxySelector;
import java.net.SocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import static lab.commerce.api.SearchContract.*;

@Component
final class ElasticsearchBackend implements AutoCloseable {
    private final BackendSettings settings;
    private final JsonMapper mapper;
    private final HttpClient client;
    private final Semaphore permits = new Semaphore(16);

    ElasticsearchBackend(BackendSettings settings, JsonMapper mapper) {
        this.settings = settings;
        this.mapper = mapper;
        this.client = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(settings.connectTimeoutMs()))
            .followRedirects(HttpClient.Redirect.NEVER).version(HttpClient.Version.HTTP_1_1)
            .proxy(new ProxySelector() {
                public List<Proxy> select(URI uri) { return List.of(Proxy.NO_PROXY); }
                public void connectFailed(URI uri, SocketAddress address, IOException error) {}
            }).build();
    }

    Result search(Query query) {
        var match = Map.of("query", query.query(), "operator", "or", "auto_generate_synonyms_phrase_query", false,
            "zero_terms_query", "none", "fuzziness", 0, "lenient", false);
        var body = Map.of("size", query.limit(), "track_total_hits", true, "track_scores", true, "_source", false,
            "fields", List.of("locale", "product_id"), "timeout", "1s",
            "sort", List.of(Map.of("_score", "desc"), Map.of("product_id", "asc")),
            "query", Map.of("bool", Map.of("filter", List.of(Map.of("term", Map.of("locale", query.locale()))),
                "must", List.of(Map.of("match", Map.of("title_" + query.locale(), match))))));
        JsonNode response = exchange("POST", "/" + settings.index() + "/_search", body);
        try {
            if (response.path("timed_out").isBoolean() && response.path("timed_out").booleanValue()) throw ApiFailure.timeout();
            var shards = response.path("_shards");
            if (!response.path("timed_out").isBoolean() || response.has("error")
                || !numberEquals(shards.get("failed"), 0) || !numberEquals(shards.get("successful"), 1)
                || !numberEquals(shards.get("total"), 1)
                || response.has("terminated_early") && !isFalse(response.get("terminated_early"))) {
                throw new ApiFailure(502, "BACKEND_INCOMPLETE", "Search backend did not return a complete result.");
            }
            JsonNode rows = response.path("hits").path("hits");
            if (!rows.isArray() || rows.size() > query.limit()) throw ApiFailure.badResponse();
            var hits = new ArrayList<Hit>();
            var seen = new HashSet<String>();
            for (JsonNode row : rows) {
                var fields = row.path("fields");
                JsonNode products = fields.path("product_id"), locales = fields.path("locale"), score = row.path("_score");
                if (!products.isArray() || products.size() != 1 || !products.get(0).isString()
                    || products.get(0).asString().isEmpty() || !locales.isArray() || locales.size() != 1
                    || !locales.get(0).isString() || !query.locale().equals(locales.get(0).asString())
                    || !row.path("_id").isString() || !row.get("_id").asString().equals(query.locale() + ":" + products.get(0).asString())
                    || !score.isNumber() || !Double.isFinite(score.doubleValue()) || score.doubleValue() < 0) throw ApiFailure.badResponse();
                Hit hit = new Hit(products.get(0).asString(), score.doubleValue());
                if (!seen.add(hit.productId())) throw ApiFailure.badResponse();
                if (!hits.isEmpty()) {
                    Hit prior = hits.getLast();
                    if (hit.score() > prior.score() || hit.score() == prior.score() && hit.productId().compareTo(prior.productId()) < 0) throw ApiFailure.badResponse();
                }
                hits.add(hit);
            }
            return new Result(query.locale(), query.limit(), hits.size(), "bm25", List.copyOf(hits));
        } catch (ApiFailure failure) { throw failure; }
        catch (RuntimeException failure) { throw ApiFailure.badResponse(); }
    }

    void ready() {
        JsonNode health = exchange("GET", "/_cluster/health/" + settings.index() + "?wait_for_status=green&timeout=500ms", null);
        if (!health.path("status").isString() || !"green".equals(health.path("status").asString())
            || !isFalse(health.get("timed_out")) || !numberEquals(health.get("active_primary_shards"), 1)) throw ApiFailure.unavailable();
    }

    private static boolean numberEquals(JsonNode node, int value) {
        return node != null && node.isIntegralNumber() && node.canConvertToInt() && node.intValue() == value;
    }

    private static boolean isFalse(JsonNode node) { return node != null && node.isBoolean() && !node.booleanValue(); }

    private JsonNode exchange(String method, String path, Object body) {
        if (!permits.tryAcquire()) throw new ApiFailure(503, "OVERLOADED", "Search backend request limit reached.");
        try {
            String base = settings.backendUrl().toString().replaceAll("/$", "");
            var request = HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofMillis(settings.requestTimeoutMs()))
                .header("Content-Type", "application/json")
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofByteArray(mapper.writeValueAsBytes(body))).build();
            var pending = client.sendAsync(request, info -> new LimitedBody());
            HttpResponse<byte[]> response;
            try { response = pending.get(settings.requestTimeoutMs(), TimeUnit.MILLISECONDS); }
            catch (TimeoutException failure) { pending.cancel(true); throw ApiFailure.timeout(); }
            catch (InterruptedException failure) { pending.cancel(true); Thread.currentThread().interrupt(); throw ApiFailure.unavailable(); }
            catch (ExecutionException failure) {
                if (failure.getCause() instanceof java.net.http.HttpTimeoutException) throw ApiFailure.timeout();
                if (failure.getCause() instanceof ResponseTooLarge) throw ApiFailure.badResponse();
                throw ApiFailure.unavailable();
            }
            if (response.statusCode() == 404 || response.statusCode() == 429 || response.statusCode() >= 500) throw ApiFailure.unavailable();
            if (response.statusCode() != 200) throw ApiFailure.badResponse();
            try {
                JsonNode result = mapper.readTree(response.body());
                if (result == null || !result.isObject()) throw ApiFailure.badResponse();
                return result;
            } catch (ApiFailure failure) { throw failure; }
            catch (RuntimeException failure) { throw ApiFailure.badResponse(); }
        } finally { permits.release(); }
    }

    private static final class ResponseTooLarge extends IOException {}
    private static final class LimitedBody implements HttpResponse.BodySubscriber<byte[]> {
        private final HttpResponse.BodySubscriber<byte[]> delegate = HttpResponse.BodySubscribers.ofByteArray();
        private Flow.Subscription subscription;
        private long bytes;
        public CompletionStage<byte[]> getBody() { return delegate.getBody(); }
        public void onSubscribe(Flow.Subscription subscription) { this.subscription = subscription; delegate.onSubscribe(subscription); }
        public void onNext(List<ByteBuffer> chunks) {
            for (ByteBuffer chunk : chunks) bytes += chunk.remaining();
            if (bytes > 1024 * 1024) { subscription.cancel(); delegate.onError(new ResponseTooLarge()); }
            else delegate.onNext(chunks);
        }
        public void onError(Throwable failure) { delegate.onError(failure); }
        public void onComplete() { delegate.onComplete(); }
    }

    @Override public void close() { client.close(); }
}
