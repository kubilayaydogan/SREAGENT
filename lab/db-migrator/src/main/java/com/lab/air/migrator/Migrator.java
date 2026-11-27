package com.lab.air.migrator;

import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationInfo;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.HashMap;
import java.util.Map;

/**
 * Tek migration runner. Komutlar:
 * <pre>
 *   migrate                                   Flyway migration (lab_owner)
 *   info                                      migration durumu
 *   seed  --scale=N [--events-per-bag=N] [--hot-flights=N] [--hot-bookings=N]
 *   reset --confirm=&lt;veritabanı adı&gt;          (LAB_ALLOW_RESET=true gerekir) veriyi siler
 *   check                                     lab.integrity_check görünümünü yazdırır
 *   chaos-seed --rows=N                       chaos.synthetic_data boyutu (10k..5M, chaos v2 workload hedefi)
 * </pre>
 * Bağlantı: DB_HOST, DB_PORT, DB_NAME, DB_OWNER_USER, DB_OWNER_PASSWORD ortam değişkenleri.
 */
public final class Migrator {

    private Migrator() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            usage();
            System.exit(2);
        }
        Config cfg = Config.fromEnv(System.getenv());
        Map<String, String> opts = parseOpts(args);
        int rc = run(args[0], opts, cfg, System.getenv());
        System.exit(rc);
    }

    static int run(String cmd, Map<String, String> opts, Config cfg, Map<String, String> env) throws SQLException {
        switch (cmd) {
            case "migrate" -> {
                var result = flyway(cfg).migrate();
                System.out.printf("migrate: %d migration uygulandı, hedef sürüm=%s%n",
                        result.migrationsExecuted, result.targetSchemaVersion);
                return 0;
            }
            case "info" -> {
                for (MigrationInfo mi : flyway(cfg).info().all()) {
                    System.out.printf("%-4s %-30s %s%n", mi.getVersion(), mi.getDescription(), mi.getState());
                }
                return 0;
            }
            case "seed" -> {
                int scale = intOpt(opts, "scale", 1);
                int epb = intOpt(opts, "events-per-bag", 5);
                int hotFlights = intOpt(opts, "hot-flights", 4);
                int hotBookings = intOpt(opts, "hot-bookings", 2000);
                try (Connection c = connect(cfg);
                     PreparedStatement ps = c.prepareStatement("SELECT lab.seed(?, ?, ?, ?)::text")) {
                    ps.setInt(1, scale);
                    ps.setInt(2, epb);
                    ps.setInt(3, hotFlights);
                    ps.setInt(4, hotBookings);
                    long t0 = System.nanoTime();
                    try (ResultSet rs = ps.executeQuery()) {
                        rs.next();
                        System.out.printf("seed tamam (%.1fs): %s%n", (System.nanoTime() - t0) / 1e9, rs.getString(1));
                    }
                }
                return 0;
            }
            case "reset" -> {
                if (!"true".equalsIgnoreCase(env.getOrDefault("LAB_ALLOW_RESET", "false"))) {
                    System.err.println("RED: reset için LAB_ALLOW_RESET=true ortam değişkeni gerekli.");
                    return 3;
                }
                String confirm = opts.get("confirm");
                if (confirm == null || !confirm.equals(cfg.dbName())) {
                    System.err.printf("RED: --confirm=%s şeklinde veritabanı adını açıkça yazın.%n", cfg.dbName());
                    return 3;
                }
                try (Connection c = connect(cfg); var st = c.createStatement()) {
                    st.execute("SELECT lab.reset_data()");
                }
                System.out.println("reset tamam: tüm iş verisi silindi. Yeniden seed çalıştırın.");
                return 0;
            }
            case "chaos-seed" -> {
                int rows = intOpt(opts, "rows", 400_000);
                try (Connection c = connect(cfg);
                     PreparedStatement ps = c.prepareStatement("SELECT chaos.resize_synthetic(?)")) {
                    ps.setInt(1, rows);
                    long t0 = System.nanoTime();
                    try (ResultSet rs = ps.executeQuery()) {
                        rs.next();
                        System.out.printf("chaos-seed tamam (%.1fs): chaos.synthetic_data = %d satır%n",
                                (System.nanoTime() - t0) / 1e9, rs.getLong(1));
                    }
                }
                return 0;
            }
            case "check" -> {
                int bad = 0;
                try (Connection c = connect(cfg); var st = c.createStatement();
                     ResultSet rs = st.executeQuery("SELECT check_name, violations FROM lab.integrity_check")) {
                    while (rs.next()) {
                        long v = rs.getLong(2);
                        if (v != 0) {
                            bad++;
                        }
                        System.out.printf("%-32s %d%n", rs.getString(1), v);
                    }
                }
                return bad == 0 ? 0 : 4;
            }
            default -> {
                usage();
                return 2;
            }
        }
    }

    public static Flyway flyway(Config cfg) {
        return Flyway.configure()
                .dataSource(cfg.jdbcUrl(), cfg.user(), cfg.password())
                .schemas("lab")
                .defaultSchema("lab")
                .createSchemas(true)
                .locations("classpath:db/migration")
                .validateOnMigrate(true)
                .cleanDisabled(true)
                .load();
    }

    static Connection connect(Config cfg) throws SQLException {
        return DriverManager.getConnection(cfg.jdbcUrl(), cfg.user(), cfg.password());
    }

    static Map<String, String> parseOpts(String[] args) {
        Map<String, String> m = new HashMap<>();
        for (int i = 1; i < args.length; i++) {
            String a = args[i];
            if (a.startsWith("--") && a.contains("=")) {
                m.put(a.substring(2, a.indexOf('=')), a.substring(a.indexOf('=') + 1));
            } else {
                throw new IllegalArgumentException("Beklenmeyen argüman: " + a);
            }
        }
        return m;
    }

    private static int intOpt(Map<String, String> o, String k, int def) {
        return o.containsKey(k) ? Integer.parseInt(o.get(k)) : def;
    }

    private static void usage() {
        System.err.println("kullanım: migrate | info | check | seed --scale=N [--events-per-bag=N] | chaos-seed --rows=N | reset --confirm=<db>");
    }

    public record Config(String host, int port, String dbName, String user, String password) {
        public static Config fromEnv(Map<String, String> env) {
            String pw = env.get("DB_OWNER_PASSWORD");
            if (pw == null || pw.isBlank()) {
                throw new IllegalStateException("DB_OWNER_PASSWORD tanımlı değil");
            }
            return new Config(env.getOrDefault("DB_HOST", "localhost"),
                    Integer.parseInt(env.getOrDefault("DB_PORT", "5432")),
                    env.getOrDefault("DB_NAME", "airlab"),
                    env.getOrDefault("DB_OWNER_USER", "lab_owner"),
                    pw);
        }

        public String jdbcUrl() {
            return "jdbc:postgresql://" + host + ":" + port + "/" + dbName + "?ApplicationName=db-migrator";
        }
    }
}
