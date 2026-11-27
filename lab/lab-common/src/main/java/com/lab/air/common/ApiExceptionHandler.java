package com.lab.air.common;

import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.TransactionException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.sql.SQLException;
import java.time.Instant;

/**
 * REST uçları için tek tip hata modeli. DB hataları {@link DbErrors} ile
 * SRE açısından ayrıştırılabilir kodlara çevrilir (LOCK_TIMEOUT, STATEMENT_TIMEOUT,
 * POOL_TIMEOUT, DB_CONNECTION_REFUSED ...).
 */
@RestControllerAdvice(annotations = RestController.class)
public class ApiExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    public record ApiError(Instant timestamp, int status, String code, String message, String path, String requestId) {
    }

    @ExceptionHandler(BusinessException.class)
    ResponseEntity<ApiError> business(BusinessException e, HttpServletRequest r) {
        return body(e.status(), e.code(), e.getMessage(), r);
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, MissingServletRequestParameterException.class,
            MethodArgumentTypeMismatchException.class, IllegalArgumentException.class})
    ResponseEntity<ApiError> badRequest(Exception e, HttpServletRequest r) {
        return body(HttpStatus.BAD_REQUEST, "VALIDATION", e.getMessage(), r);
    }

    @ExceptionHandler({DataAccessException.class, TransactionException.class, SQLException.class})
    ResponseEntity<ApiError> db(Exception e, HttpServletRequest r) {
        DbErrors.Kind k = DbErrors.classify(e);
        String msg = DbErrors.rootMessage(e);
        log.warn("db failure kind={} msg={}", k, msg);
        return body(k.status, k.name(), msg, r);
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ApiError> other(Exception e, HttpServletRequest r) {
        log.error("unhandled error", e);
        return body(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL", e.getClass().getSimpleName(), r);
    }

    private ResponseEntity<ApiError> body(HttpStatus s, String code, String msg, HttpServletRequest r) {
        return ResponseEntity.status(s).body(new ApiError(Instant.now(), s.value(), code, msg, r.getRequestURI(),
                MDC.get(RequestContextFilter.MDC_REQUEST_ID)));
    }
}
