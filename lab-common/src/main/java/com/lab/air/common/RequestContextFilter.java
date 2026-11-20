package com.lab.air.common;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * X-Request-Id ve X-Experiment-Id başlıklarını doğrular, MDC'ye koyar ve
 * yanıtta X-Request-Id'yi geri döndürür. Bu kimlikler YÜKSEK CARDINALITY'dir:
 * yalnız log/span/SQL yorumunda taşınır, metrik boyutu yapılmaz.
 */
public class RequestContextFilter extends OncePerRequestFilter {

    public static final String REQUEST_ID = "X-Request-Id";
    public static final String EXPERIMENT_ID = "X-Experiment-Id";
    public static final String MDC_REQUEST_ID = "request_id";
    public static final String MDC_EXPERIMENT_ID = "experiment_id";
    public static final String MDC_OPERATION = "operation";
    public static final String MDC_DB_ROLE = "db_role";

    /** SQL yorumuna da girdiği için '*' ve '/' gibi karakterler kabul edilmez. */
    /**
     * İstek başına bir yapısal log satırı (logger "airlab.access"). Kapatmak için:
     * LOGGING_LEVEL_AIRLAB_ACCESS=WARN (yalnız 5xx ve yavaş istekler kalır).
     */
    private static final Logger ACCESS = LoggerFactory.getLogger("airlab.access");
    private static final long SLOW_MS = Long.getLong("airlab.access.slow-ms", 1000);

    static final Pattern SAFE_ID = Pattern.compile("[A-Za-z0-9._:-]{1,64}");

    private final String dbRole;

    /** Servis adı ECS formatında zaten service.name olarak yazılır; MDC'ye tekrar konmaz. */
    public RequestContextFilter(String dbRole) {
        this.dbRole = dbRole;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String rid = safe(req.getHeader(REQUEST_ID));
        if (rid == null) {
            rid = UUID.randomUUID().toString();
        }
        String exp = safe(req.getHeader(EXPERIMENT_ID));
        MDC.put(MDC_REQUEST_ID, rid);
        MDC.put(MDC_DB_ROLE, dbRole);
        if (exp != null) {
            MDC.put(MDC_EXPERIMENT_ID, exp);
        }
        res.setHeader(REQUEST_ID, rid);
        long t0 = System.nanoTime();
        try {
            chain.doFilter(req, res);
        } finally {
            long ms = (System.nanoTime() - t0) / 1_000_000;
            int status = res.getStatus();
            String uri = req.getRequestURI();
            if (!uri.startsWith("/actuator") && !uri.endsWith(".css")) {
                var ev = status >= 500 || ms >= SLOW_MS ? ACCESS.atWarn() : ACCESS.atInfo();
                ev.addKeyValue("http.method", req.getMethod())
                        .addKeyValue("http.route", uri)
                        .addKeyValue("http.status", status)
                        .addKeyValue("duration_ms", ms)
                        .log("{} {} {} {}ms", req.getMethod(), uri, status, ms);
            }
            MDC.remove(MDC_REQUEST_ID);
            MDC.remove(MDC_EXPERIMENT_ID);
            MDC.remove(MDC_OPERATION);
            MDC.remove(MDC_DB_ROLE);
        }
    }

    static String safe(String v) {
        return v != null && SAFE_ID.matcher(v).matches() ? v : null;
    }
}
