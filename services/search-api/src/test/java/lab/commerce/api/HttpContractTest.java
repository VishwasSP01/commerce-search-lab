package lab.commerce.api;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.beans.factory.annotation.Autowired;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import static org.junit.jupiter.api.Assertions.*;
import static org.awaitility.Awaitility.await;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class HttpContractTest {
    static final JsonMapper JSON = JsonMapper.builder().build();
    static final AtomicReference<String> MODE = new AtomicReference<>("good");
    static final AtomicReference<JsonNode> LAST = new AtomicReference<>();
    static final AtomicInteger CALLS = new AtomicInteger();
    static final AtomicReference<String> OPAQUE = new AtomicReference<>();
    static final AtomicReference<String> TRACEPARENT = new AtomicReference<>();
    static final HttpServer STUB;
    static final java.util.concurrent.ExecutorService WORKERS = Executors.newVirtualThreadPerTaskExecutor();
    static {
        try {
            STUB = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            STUB.setExecutor(WORKERS);
            STUB.createContext("/", exchange -> {
                try {
                    String mode = MODE.get();
                    CALLS.incrementAndGet();
                    byte[] input = exchange.getRequestBody().readAllBytes();
                    boolean ready = exchange.getRequestURI().getPath().contains("_cluster/health");
                    if (!ready) { LAST.set(JSON.readTree(input)); OPAQUE.set(exchange.getRequestHeaders().getFirst("X-Opaque-Id")); TRACEPARENT.set(exchange.getRequestHeaders().getFirst("traceparent")); }
                    int status = mode.equals("missing") ? 404 : mode.equals("unavailable") ? 503 : 200;
                    String response = ready ? "{\"status\":\"green\",\"timed_out\":false,\"active_primary_shards\":1}" : results(mode);
                    if (mode.equals("slowHeaders")) Thread.sleep(600);
                    if (mode.equals("malformed")) response = "{invalid";
                    if (mode.equals("large")) response = "x".repeat(1024 * 1024 + 1);
                    byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
                    exchange.getResponseHeaders().set("Content-Type", "application/json");
                    exchange.sendResponseHeaders(status, bytes.length);
                    if (mode.equals("slowBody")) { exchange.getResponseBody().write(bytes, 0, 1); exchange.getResponseBody().flush(); Thread.sleep(600); exchange.getResponseBody().write(bytes, 1, bytes.length - 1); }
                    else exchange.getResponseBody().write(bytes);
                } catch (InterruptedException error) { Thread.currentThread().interrupt(); }
                catch (IOException ignored) { /* Client cancellation is expected in deadline tests. */ }
                finally { exchange.close(); }
            });
            STUB.start();
        } catch (IOException failure) { throw new ExceptionInInitializerError(failure); }
    }

    static String results(String mode) {
        String locale = LAST.get().path("query").path("bool").path("filter").get(0).path("term").path("locale").asString();
        if (mode.equals("wrongLocale")) locale = "jp";
        var one = Map.of("_id", locale + ":001", "_score", 2.2, "fields", Map.of("locale", List.of(locale), "product_id", List.of("001")));
        var two = Map.of("_id", locale + ":002", "_score", 2.2, "fields", Map.of("locale", List.of(locale), "product_id", List.of("002")));
        List<?> hits = switch (mode) {
            case "empty" -> List.of(); case "duplicate" -> List.of(one, one); case "badOrder" -> List.of(two, one);
            default -> LAST.get().path("size").intValue() == 1 ? List.of(one) : List.of(one, two);
        };
        return JSON.writeValueAsString(Map.of("took", 7, "timed_out", mode.equals("timedOut"), "_shards",
            Map.of("failed", mode.equals("partial") ? 1 : 0, "successful", 1, "total", 1), "hits", Map.of("hits", hits)));
    }

    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("commerce.backend-url", () -> "http://127.0.0.1:" + STUB.getAddress().getPort());
        registry.add("commerce.index", () -> "csl-fixture-api");
        registry.add("commerce.connect-timeout-ms", () -> 50);
        registry.add("commerce.request-timeout-ms", () -> 250);
    }
    @LocalServerPort int port;
    @Autowired MeterRegistry registry;
    final HttpClient client = HttpClient.newHttpClient();
    @BeforeEach void reset() { MODE.set("good"); CALLS.set(0); LAST.set(null); }
    @AfterAll static void close() { STUB.stop(0); WORKERS.close(); }

    HttpResponse<String> post(String body) throws Exception { return send("POST", "/v1/search", "application/json", body); }
    HttpResponse<String> send(String method, String path, String type, String body) throws Exception {
        return client.send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path)).timeout(Duration.ofSeconds(5))
            .header("Content-Type", type).method(method, HttpRequest.BodyPublishers.ofString(body)).build(), HttpResponse.BodyHandlers.ofString());
    }
    void error(int status, String code, HttpResponse<String> response) {
        assertEquals(status, response.statusCode(), response.body());
        JsonNode body = JSON.readTree(response.body());
        assertEquals(code, body.path("code").asString(), response.body());
        assertFalse(response.body().contains("_shards"));
    }

    @Test void literalNormalizedQueryAndNativeScoresCrossRealHttpBoundary() throws Exception {
        var response = post("{\"query\":\"Ｓｈｏｅｓ + (red):\",\"locale\":\"us\"}");
        assertEquals(200, response.statusCode(), response.body());
        JsonNode body = JSON.readTree(response.body());
        assertEquals(10, body.path("limit").intValue()); assertEquals(2, body.path("returned").intValue());
        assertEquals("001", body.path("hits").get(0).path("productId").asString());
        assertEquals(2.2, body.path("hits").get(0).path("score").doubleValue());
        JsonNode match = LAST.get().path("query").path("bool").path("must").get(0).path("match").path("title_us");
        assertEquals("Shoes + (red):", match.path("query").asString());
        assertFalse(match.path("auto_generate_synonyms_phrase_query").booleanValue());
        assertEquals("or", match.path("operator").asString());
        assertEquals("asc", LAST.get().path("sort").get(1).path("product_id").asString());
        assertEquals(1, CALLS.get());
    }

    @Test void localeAndLimitAreExplicitAndEmptyAnalysisIsValid() throws Exception {
        assertEquals(200, post("{\"query\":\"空港\",\"locale\":\"jp\",\"limit\":1}").statusCode());
        assertTrue(LAST.get().path("query").path("bool").path("must").get(0).path("match").has("title_jp"));
        assertEquals(1, LAST.get().path("size").intValue());
        MODE.set("empty");
        var response = post("{\"query\":\"the and of\",\"locale\":\"us\",\"limit\":100}");
        assertEquals(200, response.statusCode()); assertEquals(0, JSON.readTree(response.body()).path("returned").intValue());
    }

    @Test void invalidJsonAndBoundsNeverReachBackend() throws Exception {
        for (String body : List.of("{}", "[]", "null", "{", "{\"query\":\"x\",\"locale\":\"us\"} {}",
            "{\"query\":\"x\",\"query\":\"y\",\"locale\":\"us\"}", "{\"query\":\"　\",\"locale\":\"us\"}",
            "{\"query\":1,\"locale\":\"us\"}", "{\"query\":\"x\",\"locale\":\"US\"}", "{\"query\":\"x\",\"locale\":\"us\",\"extra\":1}",
            "{\"query\":\"x\",\"locale\":\"us\",\"limit\":null}", "{\"query\":\"x\",\"locale\":\"us\",\"limit\":\"10\"}",
            "{\"query\":\"x\",\"locale\":\"us\",\"limit\":1.0}", "{\"query\":\"x\",\"locale\":\"us\",\"limit\":0}",
            "{\"query\":\"x\",\"locale\":\"us\",\"limit\":101}", JSON.writeValueAsString(Map.of("query", "a".repeat(257), "locale", "us")),
            JSON.writeValueAsString(Map.of("query", "㍿".repeat(100), "locale", "us")))) error(400, "INVALID_REQUEST", post(body));
        assertEquals(0, CALLS.get());
    }

    @Test void requestBytesAndMediaAreBounded() throws Exception {
        error(413, "REQUEST_TOO_LARGE", post(" ".repeat(8193)));
        error(415, "HTTP_415", send("POST", "/v1/search", "text/plain", "hello"));
        assertEquals(0, CALLS.get());
    }

    @Test void unavailableOrMissingIndexDoesNotBecomeEmptySuccess() throws Exception {
        for (String mode : List.of("unavailable", "missing")) { MODE.set(mode); error(503, "BACKEND_UNAVAILABLE", post("{\"query\":\"x\",\"locale\":\"us\"}")); }
        assertEquals(2, CALLS.get());
    }

    @Test void partialAndTimedOutResultsAreErrors() throws Exception {
        MODE.set("partial"); error(502, "BACKEND_INCOMPLETE", post("{\"query\":\"x\",\"locale\":\"us\"}"));
        MODE.set("timedOut"); error(504, "BACKEND_TIMEOUT", post("{\"query\":\"x\",\"locale\":\"us\"}"));
    }

    @Test void malformedWrongLocaleDuplicateOrUnorderedResultsFailClosed() throws Exception {
        for (String mode : List.of("malformed", "wrongLocale", "duplicate", "badOrder", "large")) {
            MODE.set(mode); error(502, "BACKEND_INVALID_RESPONSE", post("{\"query\":\"x\",\"locale\":\"us\"}"));
        }
    }

    @Test void deadlineIncludesBodyAndRecoversWithoutRetry() throws Exception {
        for (String mode : List.of("slowHeaders", "slowBody")) {
            MODE.set(mode); long started = System.nanoTime();
            error(504, "BACKEND_TIMEOUT", post("{\"query\":\"x\",\"locale\":\"us\"}"));
            assertTrue(Duration.ofNanos(System.nanoTime() - started).toMillis() < 2000);
        }
        assertEquals(2, CALLS.get()); MODE.set("good");
        assertEquals(200, post("{\"query\":\"x\",\"locale\":\"us\"}").statusCode());
    }

    @Test void livenessStaysUpWhileReadinessTracksDependency() throws Exception {
        assertEquals(200, send("GET", "/health/ready", "application/json", "").statusCode());
        MODE.set("unavailable");
        error(503, "BACKEND_UNAVAILABLE", send("GET", "/health/ready", "application/json", ""));
        assertEquals(200, send("GET", "/health/live", "application/json", "").statusCode());
    }

    @Test void backendConfigurationRejectsRemoteAndWildcardScope() {
        for (String url : List.of("http://example.com:19200", "http://user:pass@localhost:19200", "https://localhost:19200", "http://localhost:19200/path")) {
            assertThrows(IllegalArgumentException.class, () -> new BackendSettings(URI.create(url), "csl-pilot-api", 50, 250));
        }
        assertThrows(IllegalArgumentException.class, () -> new BackendSettings(URI.create("http://localhost:19200"), "*", 50, 250));
    }

    @Test void generatedCorrelationAndPrometheusRespectDataBoundaries() throws Exception {
        var response = client.send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/v1/search"))
            .header("Content-Type", "application/json").header("X-Request-ID", "caller-id-must-be-ignored")
            .POST(HttpRequest.BodyPublishers.ofString("{\"query\":\"private-query-marker\",\"locale\":\"us\"}")).build(), HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        String id = response.headers().firstValue("X-Request-ID").orElseThrow();
        assertDoesNotThrow(() -> java.util.UUID.fromString(id)); assertEquals("commerce-search-lab-api", OPAQUE.get());
        assertTrue(TRACEPARENT.get().matches("00-[0-9a-f]{32}-[0-9a-f]{16}-00"));
        assertEquals(id.replace("-", ""), TRACEPARENT.get().split("-")[1]);
        await().atMost(Duration.ofSeconds(2)).untilAsserted(() -> assertFalse(registry.find("commerce.search.request").timers().isEmpty()));
        String metrics = send("GET", "/actuator/prometheus", "application/json", "").body();
        assertTrue(metrics.contains("commerce_search_request_seconds_count"), metrics);
        assertTrue(metrics.contains("commerce_backend_stage_seconds_count"));
        assertTrue(metrics.contains("commerce_elasticsearch_took_seconds_count"));
        assertTrue(metrics.contains("tomcat_threads_busy_threads"));
        assertFalse(metrics.contains("private-query-marker")); assertFalse(metrics.contains(id));
        assertFalse(metrics.contains("productId"));
        assertEquals(404, send("GET", "/actuator/env", "application/json", "").statusCode());
    }

    @Test void requestOutcomeMetricsIncludeValidationAndBackendErrors() throws Exception {
        double invalid = registry.find("commerce.search.request").tag("outcome", "invalid_request").timers().stream().mapToDouble(t -> t.count()).sum();
        error(400, "INVALID_REQUEST", post("{}"));
        await().atMost(Duration.ofSeconds(2)).untilAsserted(() -> assertEquals(invalid + 1, registry.find("commerce.search.request").tag("outcome", "invalid_request").timers().stream().mapToDouble(t -> t.count()).sum()));
        MODE.set("slowBody");
        double prior = registry.find("commerce.backend.stage").tags("operation", "search", "stage", "http_wait", "outcome", "timeout").timers().stream().mapToDouble(t -> t.count()).sum();
        error(504, "BACKEND_TIMEOUT", post("{\"query\":\"x\",\"locale\":\"us\"}"));
        await().atMost(Duration.ofSeconds(2)).untilAsserted(() -> assertEquals(prior + 1, registry.find("commerce.backend.stage").tags("operation", "search", "stage", "http_wait", "outcome", "timeout").timers().stream().mapToDouble(t -> t.count()).sum()));
        assertEquals(0, registry.get("commerce.backend.inflight").gauge().value());
    }
}
