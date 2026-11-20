package com.lab.air.common;

import org.springframework.http.HttpStatus;

import java.sql.SQLException;
import java.sql.SQLTransientConnectionException;

/**
 * DB hata zincirini SQLState'e göre sınıflandırır. Ayrım SRE analizinin temelidir:
 * <ul>
 *   <li>POOL_TIMEOUT: Hikari connectionTimeout doldu, DB tarafında ret yok → uygulamanın KENDİ havuzu dolu</li>
 *   <li>DB_CONNECTION_REFUSED: DB yeni bağlantıyı reddetti (53300 too_many_connections) → GLOBAL kapasite</li>
 *   <li>LOCK_TIMEOUT: 55P03 lock_not_available (lock_timeout)</li>
 *   <li>STATEMENT_TIMEOUT: 57014 query_canceled (statement_timeout / pg_cancel_backend)</li>
 *   <li>DEADLOCK / SERIALIZATION / INTEGRITY / DB_UNAVAILABLE</li>
 * </ul>
 */
public final class DbErrors {

    public enum Kind {
        LOCK_TIMEOUT(HttpStatus.SERVICE_UNAVAILABLE),
        STATEMENT_TIMEOUT(HttpStatus.SERVICE_UNAVAILABLE),
        POOL_TIMEOUT(HttpStatus.SERVICE_UNAVAILABLE),
        DB_CONNECTION_REFUSED(HttpStatus.SERVICE_UNAVAILABLE),
        DB_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE),
        /** Kurulu bağlantı koptu (ör. lab cleanup havuzu kapattı, ağ/DB restart). */
        DB_CONNECTION_LOST(HttpStatus.SERVICE_UNAVAILABLE),
        DEADLOCK(HttpStatus.CONFLICT),
        SERIALIZATION(HttpStatus.CONFLICT),
        INTEGRITY(HttpStatus.CONFLICT),
        DB_ERROR(HttpStatus.INTERNAL_SERVER_ERROR);

        public final HttpStatus status;

        Kind(HttpStatus s) {
            this.status = s;
        }
    }

    private DbErrors() {
    }

    public static Kind classify(Throwable t) {
        boolean poolTimeout = false;
        for (Throwable c = t; c != null; c = c.getCause() == c ? null : c.getCause()) {
            if (c instanceof SQLException se && se.getSQLState() != null) {
                String s = se.getSQLState();
                switch (s) {
                    case "53300": return Kind.DB_CONNECTION_REFUSED;
                    case "55P03": return Kind.LOCK_TIMEOUT;
                    case "57014": return Kind.STATEMENT_TIMEOUT;
                    case "40P01": return Kind.DEADLOCK;
                    case "40001": return Kind.SERIALIZATION;
                    case "57P01", "57P02", "57P03": return Kind.DB_UNAVAILABLE;
                    default:
                        if (s.startsWith("23")) {
                            return Kind.INTEGRITY;
                        }
                        if (s.startsWith("08") && !(c instanceof SQLTransientConnectionException)
                                && String.valueOf(se.getMessage()).contains("I/O error")) {
                            return Kind.DB_CONNECTION_LOST;
                        }
                        if (s.startsWith("08")) {
                            // Hikari kendi timeout'unu 08xxx ile de raporlayabilir; nedeni zincirde ara
                            if (c instanceof SQLTransientConnectionException) {
                                poolTimeout = true;
                                continue;
                            }
                            return Kind.DB_UNAVAILABLE;
                        }
                }
            }
            if (c instanceof SQLTransientConnectionException) {
                poolTimeout = true;
            }
        }
        return poolTimeout ? Kind.POOL_TIMEOUT : Kind.DB_ERROR;
    }

    public static String rootMessage(Throwable t) {
        Throwable c = t;
        while (c.getCause() != null && c.getCause() != c) {
            c = c.getCause();
        }
        String m = c.getMessage();
        return m == null ? c.getClass().getSimpleName() : m.lines().findFirst().orElse(m);
    }
}
