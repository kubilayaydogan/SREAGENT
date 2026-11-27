package com.lab.air.testsupport;

import com.lab.air.migrator.Migrator;
import org.testcontainers.containers.Container;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.MountableFile;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

/**
 * Testler için JVM başına tek PostgreSQL 17 container'ı. DB_VM ile aynı
 * create-roles.sql, aynı Flyway migration'ları ve küçük ölçekli seed kullanılır.
 */
public final class LabPostgres {

    public static final String DB = "airlab";
    public static final String OWNER_PW = "owner-test-pw";
    public static final String SVC_PW = "svc-test-pw";

    private static PostgreSQLContainer pg;

    private LabPostgres() {
    }

    public static synchronized PostgreSQLContainer start() {
        if (pg != null) {
            return pg;
        }
        PostgreSQLContainer c = new PostgreSQLContainer("postgres:17")
                .withDatabaseName("postgres")
                .withUsername("postgres")
                .withPassword("postgres")
                .withCopyFileToContainer(MountableFile.forClasspathResource("create-roles.sql"), "/tmp/create-roles.sql")
                .withCopyFileToContainer(MountableFile.forClasspathResource("chaos-v2-superuser.sql"),
                        "/tmp/chaos-v2-superuser.sql");
        c.withCommand("postgres",
                "-c", "shared_preload_libraries=pg_stat_statements",
                "-c", "compute_query_id=on",
                "-c", "track_io_timing=on",
                "-c", "max_connections=100",
                "-c", "reserved_connections=3",
                "-c", "log_lock_waits=on",
                "-c", "deadlock_timeout=500ms");
        c.start();
        try {
            Container.ExecResult r = c.execInContainer("psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
                    "-v", "db_name=" + DB,
                    "-v", "owner_pw=" + OWNER_PW,
                    "-v", "reservation_pw=" + SVC_PW,
                    "-v", "checkin_pw=" + SVC_PW,
                    "-v", "flightops_pw=" + SVC_PW,
                    "-v", "baggage_pw=" + SVC_PW,
                    "-v", "monitor_pw=" + SVC_PW,
                    "-v", "dynatrace_pw=" + SVC_PW,
                    "-f", "/tmp/create-roles.sql");
            if (r.getExitCode() != 0) {
                throw new IllegalStateException("create-roles.sql başarısız: " + r.getStderr());
            }
            Migrator.flyway(ownerConfig(c)).migrate();
            try (Connection con = owner(c); var st = con.createStatement()) {
                st.execute("SELECT lab.seed(1, 2, 2, 300)");
            }
        } catch (Exception e) {
            c.stop();
            throw new IllegalStateException(e);
        }
        pg = c;
        return pg;
    }

    public static Migrator.Config ownerConfig(PostgreSQLContainer c) {
        return new Migrator.Config(c.getHost(), c.getMappedPort(5432), DB, "lab_owner", OWNER_PW);
    }

    public static String jdbcUrl(String applicationName) {
        PostgreSQLContainer c = start();
        return "jdbc:postgresql://" + c.getHost() + ":" + c.getMappedPort(5432) + "/" + DB
                + "?ApplicationName=" + applicationName;
    }

    public static Connection owner(PostgreSQLContainer c) throws SQLException {
        return DriverManager.getConnection(ownerConfig(c).jdbcUrl(), "lab_owner", OWNER_PW);
    }

    /** Doğrulama sorguları için (lab_owner tüm şemaların sahibidir). */
    public static Connection owner() throws SQLException {
        return owner(start());
    }

    public static Connection as(String role, String appName) throws SQLException {
        return DriverManager.getConnection(jdbcUrl(appName), role, SVC_PW);
    }
}
