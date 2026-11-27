package com.lab.air.common;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * İş operasyonunun düşük cardinality'li adı (ör. create_booking). Log MDC'sine
 * "operation" olarak, SQL yorumuna "op=" olarak ve http.server.requests metriğine
 * "operation" etiketi olarak yansır.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface Op {
    String value();
}
