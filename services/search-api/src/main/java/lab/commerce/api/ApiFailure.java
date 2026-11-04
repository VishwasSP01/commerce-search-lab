package lab.commerce.api;

final class ApiFailure extends RuntimeException {
    final int status;
    final String code;
    ApiFailure(int status, String code, String message) { super(message); this.status = status; this.code = code; }
    static ApiFailure invalid() { return new ApiFailure(400, "INVALID_REQUEST", "Expected query, locale and optional integer limit within the documented bounds."); }
    static ApiFailure unavailable() { return new ApiFailure(503, "BACKEND_UNAVAILABLE", "Search backend is unavailable."); }
    static ApiFailure timeout() { return new ApiFailure(504, "BACKEND_TIMEOUT", "Search backend exceeded its deadline."); }
    static ApiFailure badResponse() { return new ApiFailure(502, "BACKEND_INVALID_RESPONSE", "Search backend returned an invalid response."); }
}
