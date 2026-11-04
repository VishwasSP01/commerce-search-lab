package lab.commerce.api;

import org.springframework.http.ResponseEntity;
import org.springframework.web.ErrorResponse;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.resource.NoResourceFoundException;

@RestControllerAdvice
final class ApiErrors {
    private static final org.slf4j.Logger LOG = org.slf4j.LoggerFactory.getLogger(ApiErrors.class);
    record Error(String code, String message) {}
    @ExceptionHandler(ApiFailure.class)
    ResponseEntity<Error> failure(ApiFailure failure) {
        return ResponseEntity.status(failure.status).body(new Error(failure.code, failure.getMessage()));
    }
    @ExceptionHandler({HttpMediaTypeNotSupportedException.class, HttpRequestMethodNotSupportedException.class, NoResourceFoundException.class})
    ResponseEntity<Error> protocol(Exception failure) {
        int status = ((ErrorResponse) failure).getStatusCode().value();
        return ResponseEntity.status(status).body(new Error("HTTP_" + status, "Request does not match the HTTP contract."));
    }
    @ExceptionHandler(Exception.class)
    ResponseEntity<Error> unexpected(Exception failure) {
        LOG.error("Unhandled search API failure of type {}", failure.getClass().getName());
        return ResponseEntity.internalServerError().body(new Error("INTERNAL_ERROR", "Search request could not be completed."));
    }
}
