package lab.commerce.api;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import static org.junit.jupiter.api.Assertions.*;

class BackendBoundsTest {
    @Test void boundedCallsRejectOverflowAndReleasePermitsAfterCompletion() throws Exception {
        var entered = new CountDownLatch(16);
        var release = new CountDownLatch(1);
        var workers = Executors.newVirtualThreadPerTaskExecutor();
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(workers);
        server.createContext("/", exchange -> {
            try {
                exchange.getRequestBody().readAllBytes(); entered.countDown(); release.await(5, TimeUnit.SECONDS);
                byte[] body = "{\"timed_out\":false,\"_shards\":{\"failed\":0,\"successful\":1,\"total\":1},\"hits\":{\"hits\":[]}}".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(200, body.length); exchange.getResponseBody().write(body);
            } catch (InterruptedException error) { Thread.currentThread().interrupt(); }
            finally { exchange.close(); }
        });
        server.start();
        try (var backend = new ElasticsearchBackend(new BackendSettings(URI.create("http://127.0.0.1:" + server.getAddress().getPort()), "csl-fixture-bound", 300, 5000), JsonMapper.builder().build())) {
            var pending = new ArrayList<java.util.concurrent.Future<SearchContract.Result>>();
            var query = new SearchContract.Query("shoes", "us", 10);
            for (int i = 0; i < 16; i++) pending.add(workers.submit(() -> backend.search(query)));
            assertTrue(entered.await(3, TimeUnit.SECONDS), "All bounded calls reached the controlled backend");
            var failure = assertThrows(ApiFailure.class, () -> backend.search(query));
            assertEquals(503, failure.status); assertEquals("OVERLOADED", failure.code);
            release.countDown();
            for (var call : pending) assertEquals(0, call.get(3, TimeUnit.SECONDS).returned());
            assertEquals(0, backend.search(query).returned());
        } finally { release.countDown(); server.stop(0); workers.close(); }
    }

    @Test void refusedConnectionIsUnavailableAndInvalidUnicodeIsRejected() throws Exception {
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        int port = server.getAddress().getPort(); server.start(); server.stop(0);
        try (var backend = new ElasticsearchBackend(new BackendSettings(URI.create("http://127.0.0.1:" + port), "csl-fixture-down", 50, 250), JsonMapper.builder().build())) {
            var failure = assertThrows(ApiFailure.class, () -> backend.search(new SearchContract.Query("x", "us", 10)));
            assertEquals(503, failure.status);
        }
        var mapper = JsonMapper.builder().build();
        assertThrows(ApiFailure.class, () -> SearchContract.parse(mapper.readTree("{\"query\":\"\\ud800\",\"locale\":\"us\"}")));
    }
}
