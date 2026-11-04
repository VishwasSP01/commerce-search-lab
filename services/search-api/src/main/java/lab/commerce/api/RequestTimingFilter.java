package lab.commerce.api;

import java.io.IOException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
final class RequestTimingFilter extends OncePerRequestFilter {
    private final RequestTelemetry telemetry;
    RequestTimingFilter(RequestTelemetry telemetry) { this.telemetry = telemetry; }
    @Override protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getMethod().equals("POST") || !request.getRequestURI().equals("/v1/search");
    }
    @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain) throws ServletException, IOException {
        var state = telemetry.begin();
        response.setHeader("X-Request-ID", state.id);
        try { chain.doFilter(request, response); }
        catch (ServletException | IOException | RuntimeException failure) { telemetry.failure("INTERNAL_ERROR"); throw failure; }
        finally { telemetry.finish(state, response.getStatus()); }
    }
}
