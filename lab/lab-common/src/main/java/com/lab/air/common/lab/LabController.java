package com.lab.air.common.lab;

import com.lab.air.common.Op;
import com.zaxxer.hikari.HikariDataSource;
import com.zaxxer.hikari.HikariPoolMXBean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.env.Environment;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Dört iş uygulamasında ortak lab uçları (yalnız lab profili + token + allowlist).
 * Keyfi SQL almaz; yalnız sınırlı süreli sabit işlemler yapar.
 */
@RestController
@RequestMapping("/lab")
public class LabController {

    private static final Logger log = LoggerFactory.getLogger(LabController.class);

    private final DataSource ds;
    private final LabPressurePool pressure;
    private final LabHolds holds;
    private final LabProperties props;
    private final Environment env;
    private final ChaosWorkloads chaos;

    public LabController(DataSource ds, LabPressurePool pressure, LabHolds holds, LabProperties props, Environment env,
                         ChaosWorkloads chaos) {
        this.ds = ds;
        this.pressure = pressure;
        this.holds = holds;
        this.props = props;
        this.env = env;
        this.chaos = chaos;
    }

    /**
     * Bir bağlantıyı holdMs boyunca tutar.
     * pool=main     → uygulamanın kendi Hikari havuzunu doldurur (tek-pool doygunluğu)
     * pool=pressure → ayrı lab havuzundan ek DB bağlantısı açar (global connection baskısı)
     */
    @Op("lab_hold_connection")
    @PostMapping("/hold-connection")
    public Map<String, Object> holdConnection(@RequestParam long holdMs,
                                              @RequestParam(defaultValue = "main") String pool) throws SQLException {
        long t0 = System.nanoTime();
        DataSource src = switch (pool) {
            case "main" -> ds;
            case "pressure" -> pressure.pool();
            default -> throw new IllegalArgumentException("pool main|pressure olmalı");
        };
        try (Connection c = src.getConnection()) {
            long acquiredMs = (System.nanoTime() - t0) / 1_000_000;
            holds.holdConnection(c, "hold-connection-" + pool, holdMs);
            return Map.of("pool", pool, "acquireMs", acquiredMs, "heldMs", props.capHold(holdMs));
        }
    }

    /**
     * Bu servisin kendi rolüyle açık, "lab:" işaretli aktif sorgularını iptal eder ve
     * pressure havuzunu kapatır. Havuz dolu olabileceği için havuz DIŞI yeni bir
     * bağlantı kullanır; global bağlantılar tükenmişse sunucu tarafı
     * statement_timeout / client_connection_check_interval devrededir.
     */
    @Op("lab_cleanup")
    @PostMapping("/cleanup")
    public Map<String, Object> cleanup() {
        Map<String, Object> out = new LinkedHashMap<>();
        if (chaos != null) {
            out.put("chaos", chaos.cleanup());      // burn/long-tx döngüleri biter, ballast bırakılır, write_sink boşalır
        }
        boolean wasOpen = pressure.isOpen();
        pressure.close();
        out.put("pressurePoolClosed", wasOpen);
        String sql = """
                SELECT count(*) FILTER (WHERE pg_cancel_backend(pid)) AS cancelled
                  FROM pg_stat_activity
                 WHERE usename = current_user AND datname = current_database()
                   AND pid <> pg_backend_pid()
                   AND state IN ('active', 'idle in transaction')
                   AND query LIKE '%lab:%'""";
        try (Connection c = DriverManager.getConnection(env.getRequiredProperty("spring.datasource.url")
                        .replaceAll("ApplicationName=[^&]*", "ApplicationName="
                                + env.getProperty("spring.application.name") + "-lab-cleanup"),
                env.getRequiredProperty("spring.datasource.username"),
                env.getRequiredProperty("spring.datasource.password"));
             PreparedStatement ps = c.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {
            rs.next();
            out.put("cancelledBackends", rs.getLong(1));
        } catch (SQLException e) {
            log.warn("lab cleanup DB bağlantısı kurulamadı: {} (sunucu tarafı timeout'lar devrede)", e.getMessage());
            out.put("cancelledBackends", -1);
            out.put("error", e.getSQLState() + " " + e.getMessage());
        }
        log.info("lab cleanup: {}", out);
        return out;
    }

    @Op("lab_status")
    @GetMapping("/status")
    public Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("mainPool", poolStats(ds));
        out.put("pressurePool", pressure.isOpen() ? poolStats(pressure.pool()) : Map.of("open", false));
        out.put("activeHolds", holds.inUse());
        out.put("maxHoldMs", props.maxHoldMs());
        return out;
    }

    private static Map<String, Object> poolStats(DataSource d) {
        if (d instanceof HikariDataSource h && h.getHikariPoolMXBean() != null) {
            HikariPoolMXBean m = h.getHikariPoolMXBean();
            return Map.of("name", h.getPoolName(), "max", h.getMaximumPoolSize(), "active", m.getActiveConnections(),
                    "idle", m.getIdleConnections(), "pending", m.getThreadsAwaitingConnection());
        }
        return Map.of("open", false);
    }
}
