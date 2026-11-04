package lab.commerce.api;

import java.net.URI;
import java.util.Set;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("commerce")
record BackendSettings(URI backendUrl, String index, int connectTimeoutMs, int requestTimeoutMs) {
    BackendSettings {
        if (backendUrl == null || !"http".equals(backendUrl.getScheme())
            || backendUrl.getHost() == null || !Set.of("localhost", "127.0.0.1").contains(backendUrl.getHost())
            || backendUrl.getPort() < 1 || backendUrl.getPort() > 65535
            || backendUrl.getUserInfo() != null || backendUrl.getQuery() != null || backendUrl.getFragment() != null
            || !Set.of("", "/").contains(backendUrl.getPath())) {
            throw new IllegalArgumentException("Backend must be an explicit loopback HTTP URL without credentials/path");
        }
        if (index == null || !index.matches("csl-(?:pilot|fixture)-[a-z0-9-]{1,80}")) {
            throw new IllegalArgumentException("Index must be a concrete task-owned pilot/fixture index");
        }
        if (connectTimeoutMs < 1 || requestTimeoutMs < connectTimeoutMs || requestTimeoutMs > 10000) {
            throw new IllegalArgumentException("Invalid bounded backend timeouts");
        }
    }
}
