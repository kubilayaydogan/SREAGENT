package com.lab.air.common.lab;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.core.env.Environment;

import java.sql.Connection;
import java.sql.SQLException;

/**
 * Global DB connection baskısı için ana havuzdan AYRI, tembel açılan küçük havuz.
 * application_name = "&lt;servis&gt;-lab-pressure" olduğundan pg_stat_activity'de ve
 * Hikari metriklerinde (pool=&lt;servis&gt;-pressure) normal trafikten ayrışır.
 * Böylece "tek uygulamanın Hikari havuzu doldu" ile "DB instance bağlantıları tükendi"
 * durumları farklı paydalarla ölçülebilir.
 */
public class LabPressurePool implements AutoCloseable {

    private final Environment env;
    private final LabProperties props;
    private final MeterRegistry registry;
    private volatile HikariDataSource ds;

    public LabPressurePool(Environment env, LabProperties props, MeterRegistry registry) {
        this.env = env;
        this.props = props;
        this.registry = registry;
    }

    public Connection getConnection() throws SQLException {
        return pool().getConnection();
    }

    public synchronized HikariDataSource pool() {
        if (ds == null || ds.isClosed()) {
            String app = env.getProperty("spring.application.name", "app");
            String url = env.getRequiredProperty("spring.datasource.url")
                    .replaceAll("ApplicationName=[^&]*", "ApplicationName=" + app + "-lab-pressure");
            HikariConfig c = new HikariConfig();
            c.setPoolName(app + "-pressure");
            c.setJdbcUrl(url);
            c.setUsername(env.getRequiredProperty("spring.datasource.username"));
            c.setPassword(env.getRequiredProperty("spring.datasource.password"));
            c.setMaximumPoolSize(props.pressurePoolMax());
            c.setMinimumIdle(0);
            c.setIdleTimeout(10_000);
            c.setConnectionTimeout(2_000);
            c.setInitializationFailTimeout(-1);   // açılışta bağlantı denemez
            if (registry != null) {
                c.setMetricRegistry(registry);
            }
            ds = new HikariDataSource(c);
        }
        return ds;
    }

    public synchronized boolean isOpen() {
        return ds != null && !ds.isClosed();
    }

    /** Havuzu kapatır; kullanımdaki bağlantılar Hikari tarafından abort edilir. */
    @Override
    public synchronized void close() {
        if (ds != null) {
            ds.close();
            ds = null;
        }
    }
}
