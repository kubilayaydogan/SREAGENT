package com.lab.air.common;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

/** {@link Op} anotasyonunu MDC'ye ve istek özniteliğine taşır. */
public class OpInterceptor implements HandlerInterceptor {

    public static final String ATTR = "lab.operation";

    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse res, Object handler) {
        if (handler instanceof HandlerMethod hm) {
            Op op = hm.getMethodAnnotation(Op.class);
            if (op != null) {
                MDC.put(RequestContextFilter.MDC_OPERATION, op.value());
                req.setAttribute(ATTR, op.value());
            }
        }
        return true;
    }
}
