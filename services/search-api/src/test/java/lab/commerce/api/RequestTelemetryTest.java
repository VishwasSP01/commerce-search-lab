package lab.commerce.api;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Map;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import static org.junit.jupiter.api.Assertions.*;

class RequestTelemetryTest {
    @Test void concurrentEventsHaveDistinctIdsAndClearContextOnErrors() throws Exception {
        var registry = new SimpleMeterRegistry();
        var telemetry = new RequestTelemetry(registry, JsonMapper.builder().build(), false);
        var events = new ArrayList<java.util.concurrent.Future<Map<String, Object>>>();
        try (var workers = Executors.newFixedThreadPool(4)) {
            for (int i = 0; i < 40; i++) {
                final boolean invalid = i % 2 == 0;
                events.add(workers.submit(() -> {
                    var state = telemetry.begin(); telemetry.locale("jp");
                    try {
                        telemetry.stage("search", "http_wait", () -> { if (invalid) throw ApiFailure.timeout(); return "ok"; });
                    } catch (ApiFailure failure) { telemetry.failure(failure.code); }
                    Map<String, Object> event = telemetry.finish(state, invalid ? 504 : 200);
                    assertNull(telemetry.requestId());
                    return event;
                }));
            }
            var ids = new HashSet<>();
            for (var future : events) {
                var event = future.get(); assertTrue(ids.add(event.get("request_id"))); assertEquals("jp", event.get("locale"));
                assertNull(event.get("es_took_ms")); assertTrue((long) event.get("filter_ns") >= 0);
            }
            assertEquals(40, ids.size());
        }
        assertEquals(20, registry.get("commerce.search.request").tag("outcome", "timeout").timer().count());
        for (var meter : registry.getMeters()) {
            for (var tag : meter.getId().getTags()) assertFalse(java.util.Set.of("request_id", "query", "product_id", "url").contains(tag.getKey()));
        }
    }

    @Test void missingAndZeroTookRemainDifferentAndLoggingIsOptIn() {
        var registry = new SimpleMeterRegistry(); var mapper = JsonMapper.builder().build();
        var logger = (ch.qos.logback.classic.Logger) org.slf4j.LoggerFactory.getLogger("commerce.request.timing");
        var appender = new ch.qos.logback.core.read.ListAppender<ch.qos.logback.classic.spi.ILoggingEvent>();
        appender.start(); logger.addAppender(appender);
        try {
            var off = new RequestTelemetry(registry, mapper, false); var state = off.begin();
            assertNull(off.finish(state, 400).get("es_took_ms")); assertTrue(appender.list.isEmpty());
            var on = new RequestTelemetry(new SimpleMeterRegistry(), mapper, true); state = on.begin(); on.took(0L);
            assertEquals(0L, on.finish(state, 200).get("es_took_ms"));
            assertEquals(1, appender.list.size());
            var event = mapper.readTree(appender.list.getFirst().getFormattedMessage().substring("REQUEST_TIMING ".length()));
            assertEquals(0, event.path("es_took_ms").intValue()); assertTrue(event.has("request_id"));
            assertFalse(event.has("query")); assertFalse(event.has("hits"));
        } finally { logger.detachAppender(appender); appender.stop(); }
    }
}
