package lab.commerce.api;

import java.io.IOException;
import java.util.Map;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

@RestController
final class SearchController {
    private final ElasticsearchBackend backend;
    private final JsonMapper mapper;
    SearchController(ElasticsearchBackend backend, JsonMapper mapper) { this.backend = backend; this.mapper = mapper; }

    @PostMapping(value = "/v1/search", consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    SearchContract.Result search(HttpServletRequest request) {
        if (request.getContentLengthLong() > 8192) throw oversized();
        byte[] bytes;
        try { bytes = request.getInputStream().readNBytes(8193); }
        catch (IOException failure) { throw ApiFailure.invalid(); }
        if (bytes.length > 8192) throw oversized();
        SearchContract.Query query;
        try { query = SearchContract.parse(mapper.readTree(bytes)); }
        catch (JacksonException failure) { throw ApiFailure.invalid(); }
        return backend.search(query);
    }

    private static ApiFailure oversized() { return new ApiFailure(413, "REQUEST_TOO_LARGE", "JSON body exceeds 8192 bytes."); }

    @GetMapping("/health/live") Map<String, String> live() { return Map.of("status", "UP"); }
    @GetMapping("/health/ready") Map<String, String> ready() {
        try { backend.ready(); }
        catch (ApiFailure failure) { throw ApiFailure.unavailable(); }
        return Map.of("status", "UP");
    }
}
