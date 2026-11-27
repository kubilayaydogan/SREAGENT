package com.lab.air.common;

import org.junit.jupiter.api.Test;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.jdbc.CannotGetJdbcConnectionException;

import java.sql.SQLException;
import java.sql.SQLTransientConnectionException;

import static org.junit.jupiter.api.Assertions.assertEquals;

class DbErrorsTest {

    @Test
    void classifiesBySqlState() {
        assertEquals(DbErrors.Kind.LOCK_TIMEOUT,
                DbErrors.classify(new CannotAcquireLockException("x", new SQLException("lock", "55P03"))));
        assertEquals(DbErrors.Kind.STATEMENT_TIMEOUT, DbErrors.classify(new SQLException("cancel", "57014")));
        assertEquals(DbErrors.Kind.DEADLOCK, DbErrors.classify(new SQLException("dl", "40P01")));
        assertEquals(DbErrors.Kind.INTEGRITY, DbErrors.classify(new SQLException("dup", "23505")));
    }

    @Test
    void separatesOwnPoolTimeoutFromGlobalRefusal() {
        // Hikari: havuz dolu, DB tarafında ret yok
        var poolOnly = new CannotGetJdbcConnectionException("x",
                new SQLTransientConnectionException("reservation-pool - Connection is not available", "08003"));
        assertEquals(DbErrors.Kind.POOL_TIMEOUT, DbErrors.classify(poolOnly));
        // Hikari zaman aşımı + nedeni DB'nin 53300 reddi → global kapasite
        var refused = new SQLTransientConnectionException("pool - not available", "08003",
                new SQLException("remaining connection slots are reserved", "53300"));
        assertEquals(DbErrors.Kind.DB_CONNECTION_REFUSED, DbErrors.classify(refused));
        assertEquals(DbErrors.Kind.DB_CONNECTION_LOST,
                DbErrors.classify(new SQLException("An I/O error occurred while sending to the backend.", "08006")));
    }

    @Test
    void safeIdRejectsSqlCommentBreakers() {
        assertEquals(null, RequestContextFilter.safe("abc*/ DROP"));
        assertEquals("exp-20261120-ab12", RequestContextFilter.safe("exp-20261120-ab12"));
    }
}
