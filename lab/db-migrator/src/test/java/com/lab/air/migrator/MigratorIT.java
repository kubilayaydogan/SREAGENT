package com.lab.air.migrator;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.Container;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.MountableFile;

import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Rol betiği + migration + seed + reset onayı + yetki sınırları, gerçek PG17 üzerinde. */
class MigratorIT {

    static PostgreSQLContainer pg;
    static Migrator.Config cfg;

    @BeforeAll
    static void up() throws Exception {
        pg = new PostgreSQLContainer("postgres:17").withDatabaseName("postgres")
                .withCopyFileToContainer(MountableFile.forHostPath(Path.of("..", "..", "db-vm", "create-roles.sql")), "/tmp/r.sql")
                .withCopyFileToContainer(MountableFile.forHostPath(Path.of("..", "..", "db-vm", "chaos-v2-superuser.sql")),
                        "/tmp/chaos-v2-superuser.sql");
        pg.withCommand("postgres", "-c", "shared_preload_libraries=pg_stat_statements", "-c", "reserved_connections=3");
        pg.start();
        Container.ExecResult r = pg.execInContainer("psql", "-v", "ON_ERROR_STOP=1", "-U", pg.getUsername(), "-d", "postgres",
                "-v", "db_name=airlab", "-v", "owner_pw=o", "-v", "reservation_pw=s", "-v", "checkin_pw=s",
                "-v", "flightops_pw=s", "-v", "baggage_pw=s", "-v", "monitor_pw=s", "-v", "dynatrace_pw=s",
                "-f", "/tmp/r.sql");
        assertEquals(0, r.getExitCode(), r.getStderr());
        // İdempotent: ikinci çalıştırma da başarılı olmalı
        assertEquals(0, pg.execInContainer("psql", "-v", "ON_ERROR_STOP=1", "-U", pg.getUsername(), "-d", "postgres",
                "-v", "db_name=airlab", "-v", "owner_pw=o", "-v", "reservation_pw=s", "-v", "checkin_pw=s",
                "-v", "flightops_pw=s", "-v", "baggage_pw=s", "-v", "monitor_pw=s", "-v", "dynatrace_pw=s",
                "-f", "/tmp/r.sql").getExitCode());
        cfg = new Migrator.Config(pg.getHost(), pg.getMappedPort(5432), "airlab", "lab_owner", "o");
    }

    @AfterAll
    static void down() {
        pg.stop();
    }

    @Test
    void migrateSeedCheckResetFlow() throws Exception {
        assertEquals(0, Migrator.run("migrate", Map.of(), cfg, Map.of()));
        assertEquals(0, Migrator.run("migrate", Map.of(), cfg, Map.of()), "ikinci migrate no-op olmalı");
        assertEquals(0, Migrator.run("seed", Map.of("scale", "1", "events-per-bag", "2", "hot-bookings", "100"), cfg, Map.of()));
        assertEquals(0, Migrator.run("check", Map.of(), cfg, Map.of()), "seed sonrası tutarlılık ihlali olmamalı");

        // Veri varken ikinci seed reddedilir
        assertThrows(SQLException.class, () -> Migrator.run("seed", Map.of("scale", "1"), cfg, Map.of()));

        // Reset onaysız/yanlış onayla reddedilir
        assertEquals(3, Migrator.run("reset", Map.of("confirm", "airlab"), cfg, Map.of()));
        assertEquals(3, Migrator.run("reset", Map.of("confirm", "yanlis"), cfg, Map.of("LAB_ALLOW_RESET", "true")));
        assertEquals(0, Migrator.run("reset", Map.of("confirm", "airlab"), cfg, Map.of("LAB_ALLOW_RESET", "true")));
        assertEquals(0, Migrator.run("seed", Map.of("scale", "1", "events-per-bag", "1", "hot-bookings", "50"), cfg, Map.of()));
    }

    @Test
    void serviceRolesHaveLeastPrivilege() throws Exception {
        Migrator.run("migrate", Map.of(), cfg, Map.of());
        String url = cfg.jdbcUrl();
        try (var c = DriverManager.getConnection(url, "svc_baggage", "s"); var st = c.createStatement()) {
            // Baggage ortak envanteri güncelleyemez
            SQLException e = assertThrows(SQLException.class,
                    () -> st.executeUpdate("UPDATE flightops.flight_inventory SET sold = sold WHERE flight_id = -1"));
            assertEquals("42501", e.getSQLState());
            // seed fonksiyonunu çağıramaz
            assertThrows(SQLException.class, () -> st.execute("SELECT lab.reset_data()"));
        }
        try (var c = DriverManager.getConnection(url, "svc_reservation", "s"); var st = c.createStatement()) {
            // Reservation yalnız sold kolonunu güncelleyebilir, capacity'yi değil
            SQLException e = assertThrows(SQLException.class,
                    () -> st.executeUpdate("UPDATE flightops.flight_inventory SET capacity = capacity WHERE flight_id = -1"));
            assertEquals("42501", e.getSQLState());
            assertEquals(0, st.executeUpdate("UPDATE flightops.flight_inventory SET sold = sold WHERE flight_id = -1"));
            try (var rs = st.executeQuery("SHOW lock_timeout")) {
                rs.next();
                assertEquals("8s", rs.getString(1));
            }
        }
        try (var c = DriverManager.getConnection(url, "lab_monitor", "s"); var st = c.createStatement();
             var rs = st.executeQuery("SELECT count(*) FROM pg_stat_statements")) {
            assertTrue(rs.next());
        }
    }
}
