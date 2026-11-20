package com.lab.air.common.lab;

import com.lab.air.common.BusinessException;
import com.lab.air.common.SqlTag;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.concurrent.Semaphore;

/**
 * Sınırlı, sentetik tutma işlemleri. pg_sleep YALNIZ lock veya connection'ı
 * belirli bir süre tutmak içindir; CPU/I/O yükü üretmez.
 * Eşzamanlı tutma sayısı semaforla sınırlıdır; süre {@link LabProperties#maxHoldMs()} ile kesilir.
 */
public class LabHolds {

    private final LabProperties props;
    private final Semaphore permits;

    public LabHolds(LabProperties props) {
        this.props = props;
        this.permits = new Semaphore(props.maxConcurrentHolds());
    }

    /** Mevcut transaction içinde (kilitler tutulurken) bekler. */
    public void holdInTransaction(JdbcClient jdbc, String kind, long requestedMs) {
        long ms = props.capHold(requestedMs);
        if (ms == 0) {
            return;
        }
        acquire();
        try {
            jdbc.sql(SqlTag.labTag(kind, "SELECT pg_sleep(?)")).param(ms / 1000.0).query().listOfRows();
        } finally {
            permits.release();
        }
    }

    /** Verilen bağlantıyı tutar (connection baskısı senaryoları). */
    public void holdConnection(Connection c, String kind, long requestedMs) throws SQLException {
        long ms = props.capHold(requestedMs);
        acquire();
        try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag(kind, "SELECT pg_sleep(?)"))) {
            ps.setDouble(1, ms / 1000.0);
            ps.execute();
        } finally {
            permits.release();
        }
    }

    public int inUse() {
        return props.maxConcurrentHolds() - permits.availablePermits();
    }

    private void acquire() {
        if (!permits.tryAcquire()) {
            throw new BusinessException(HttpStatus.TOO_MANY_REQUESTS, "LAB_HOLD_LIMIT",
                    "eşzamanlı lab tutma limiti dolu (" + props.maxConcurrentHolds() + ")");
        }
    }
}
