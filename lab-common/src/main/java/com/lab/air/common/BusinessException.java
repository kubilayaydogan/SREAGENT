package com.lab.air.common;

import org.springframework.http.HttpStatus;

/** Beklenen iş kuralı ihlali (ör. SOLD_OUT, ALREADY_CHECKED_IN). */
public class BusinessException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public BusinessException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public static BusinessException notFound(String code, String msg) {
        return new BusinessException(HttpStatus.NOT_FOUND, code, msg);
    }

    public static BusinessException conflict(String code, String msg) {
        return new BusinessException(HttpStatus.CONFLICT, code, msg);
    }

    public static BusinessException badRequest(String code, String msg) {
        return new BusinessException(HttpStatus.BAD_REQUEST, code, msg);
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }
}
