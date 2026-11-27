package com.lab.air.common;

import org.slf4j.MDC;

/**
 * SQL'in başına kimlik yorumu ekler:
 * <pre>/* app=reservation-service op=create_booking rid=... exp=... *&#47; UPDATE ...</pre>
 * pg_stat_activity.query bu yorumu gösterir; böylece read-only collector blocking
 * zincirindeki oturumları istek/deney kimliğiyle eşleyebilir. pg_stat_statements
 * queryid'si parse ağacından hesaplandığı için yorum fingerprint'i değiştirmez.
 * Değerler {@link RequestContextFilter#SAFE_ID} ile doğrulandığından enjeksiyon riski yoktur.
 */
public final class SqlTag {

    private static volatile String app = "unknown";

    private SqlTag() {
    }

    static void setApp(String name) {
        app = name;
    }

    public static String tag(String sql) {
        StringBuilder b = new StringBuilder(96 + sql.length()).append("/* app=").append(app);
        String op = MDC.get(RequestContextFilter.MDC_OPERATION);
        if (op != null) {
            b.append(" op=").append(op);
        }
        String rid = MDC.get(RequestContextFilter.MDC_REQUEST_ID);
        if (rid != null) {
            b.append(" rid=").append(rid);
        }
        String exp = MDC.get(RequestContextFilter.MDC_EXPERIMENT_ID);
        if (exp != null) {
            b.append(" exp=").append(exp);
        }
        return b.append(" */ ").append(sql).toString();
    }

    /** Lab tutma işlemleri için ek "lab:" işareti (cleanup bunu arar). */
    public static String labTag(String kind, String sql) {
        return tag("/* lab:" + kind + " */ " + sql);
    }
}
