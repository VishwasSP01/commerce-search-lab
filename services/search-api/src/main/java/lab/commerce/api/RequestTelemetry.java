package lab.commerce.api;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

@Component
final class RequestTelemetry {
    private static final org.slf4j.Logger LOG = org.slf4j.LoggerFactory.getLogger("commerce.request.timing");
    private final MeterRegistry registry;
    private final JsonMapper mapper;
    private final boolean events;
    private final ThreadLocal<State> current = new ThreadLocal<>();
    private final AtomicInteger inflight;

    static final class State {
        final String id = UUID.randomUUID().toString();
        final String spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        final long start;
        final Map<String, Long> stages = new LinkedHashMap<>();
        String locale = "unknown", outcome = "success";
        Long tookMs;
        State(long start) { this.start = start; }
    }

    RequestTelemetry(MeterRegistry registry, JsonMapper mapper, @Value("${commerce.telemetry.request-events.enabled:false}") boolean events) {
        this.registry = registry; this.mapper = mapper; this.events = events;
        this.inflight = registry.gauge("commerce.backend.inflight", new AtomicInteger());
    }

    State begin() {
        return begin(System.nanoTime());
    }
    State begin(long filterEntryNanos) {
        if (current.get() != null) throw new IllegalStateException("Leaked request timing context");
        State state = new State(filterEntryNanos); current.set(state); return state;
    }
    String requestId() { State state = current.get(); return state == null ? null : state.id; }
    String traceparent() {
        State state = current.get();
        return state == null ? null : "00-" + state.id.replace("-", "") + "-" + state.spanId + "-00";
    }
    void locale(String locale) { State state = current.get(); if (state != null) state.locale = SearchContract.LOCALES.contains(locale) ? locale : "unknown"; }
    void failure(String code) { State state = current.get(); if (state != null) state.outcome = outcome(code); }
    void acquired() { inflight.incrementAndGet(); }
    void released() { inflight.decrementAndGet(); }

    static String outcome(String code) {
        return switch (code) {
            case "INVALID_REQUEST", "REQUEST_TOO_LARGE", "HTTP_415", "HTTP_405", "HTTP_404" -> "invalid_request";
            case "BACKEND_TIMEOUT" -> "timeout";
            case "BACKEND_UNAVAILABLE" -> "unavailable";
            case "BACKEND_INCOMPLETE" -> "incomplete";
            case "BACKEND_INVALID_RESPONSE" -> "invalid_backend";
            case "OVERLOADED" -> "overloaded";
            default -> "internal";
        };
    }

    <T> T stage(String operation, String stage, Supplier<T> action) {
        long start = System.nanoTime(); String result = "success";
        try { return action.get(); }
        catch (ApiFailure failure) { result = outcome(failure.code); throw failure; }
        catch (RuntimeException failure) { result = "internal"; throw failure; }
        finally {
            long nanos = System.nanoTime() - start;
            timer("commerce.backend.stage", "operation", operation, "stage", stage, "outcome", result).record(nanos, TimeUnit.NANOSECONDS);
            State state = current.get(); if (state != null) state.stages.merge(operation + "." + stage, nanos, Long::sum);
        }
    }

    void took(Long milliseconds) {
        State state = current.get(); if (state != null) state.tookMs = milliseconds;
        if (milliseconds != null) timer("commerce.elasticsearch.took", "operation", "search").record(milliseconds, TimeUnit.MILLISECONDS);
    }

    Map<String, Object> finish(State state, int status) {
        long nanos = System.nanoTime() - state.start;
        try {
            String statusTag = Set.of(200, 400, 404, 405, 413, 415, 429, 500, 502, 503, 504).contains(status) ? Integer.toString(status) : "other";
            timer("commerce.search.request", "locale", state.locale, "status", statusTag, "outcome", state.outcome).record(nanos, TimeUnit.NANOSECONDS);
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("request_id", state.id); event.put("locale", state.locale); event.put("status", status); event.put("outcome", state.outcome);
            event.put("filter_ns", nanos); event.put("stages_ns", new LinkedHashMap<>(state.stages)); event.put("es_took_ms", state.tookMs);
            if (events) {
                try { LOG.info("REQUEST_TIMING {}", mapper.writeValueAsString(event)); }
                catch (RuntimeException ignored) { registry.counter("commerce.telemetry.event.failures").increment(); }
            }
            return event;
        } finally { current.remove(); }
    }

    private Timer timer(String name, String... tags) {
        return Timer.builder(name).tags(tags).minimumExpectedValue(Duration.ofNanos(1000)).maximumExpectedValue(Duration.ofSeconds(5))
            .publishPercentileHistogram().register(registry);
    }
}
