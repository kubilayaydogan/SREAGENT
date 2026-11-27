package com.lab.air.chaos.v2.metrics;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.v2.V2Properties;
import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.DbSample;
import com.lab.air.chaos.v2.model.Model.HostSample;
import com.lab.air.chaos.v2.model.Model.Sample;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.File;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.function.Predicate;

/**
 * Canlı ölçüm: her {@code sampleIntervalMs}'de
 * <ul>
 *   <li>uygulamalar: actuator/prometheus (process CPU, heap, Hikari, http.server.requests histogram → p95/p99,
 *       RPS, 5xx oranı; /lab ve /actuator hariç, 10 s kayan pencere)</li>
 *   <li>PostgreSQL: read-only lab_monitor ile pg_stat_activity, pg_locks, pg_stat_database, pg_stat_io,
 *       pg_stat_statements; DB_VM host CPU/RAM: lab_os.host_stats()</li>
 *   <li>APP_VM: /proc/stat, /proc/meminfo (container içinden host görünümü), chaos-data volume boş disk</li>
 * </ul>
 * Ölçülemeyen her değer null'dır; tahmin / sabit değer yazılmaz.
 */
@Component
public class MetricsCollector {

    private static final Logger log = LoggerFactory.getLogger(MetricsCollector.class);
    private static final long WINDOW_MS = 10_000;
    private static final int HISTORY = 450;      // 2 s × 450 = 15 dk

    private final ChaosProperties props;
    private final V2Properties v2;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2))
            .version(HttpClient.Version.HTTP_1_1).build();
    private final ScheduledExecutorService exec = Executors.newSingleThreadScheduledExecutor(
            r -> Thread.ofPlatform().daemon().name("v2-metrics").unstarted(r));
    private final Map<String, Deque<AppSnap>> appSnaps = new ConcurrentHashMap<>();
    private final Deque<DbSnap> dbSnaps = new ArrayDeque<>();
    private final ProcStats dbCpu = new ProcStats();
    private final ProcStats vmCpu = new ProcStats();
    private final Deque<Sample> history = new ArrayDeque<>();
    private final List<Consumer<Sample>> listeners = new CopyOnWriteArrayList<>();
    private final long t0 = System.nanoTime();
    private volatile Sample latest;
    private volatile String dbError;
    private Connection db;

    record AppSnap(long tMs, TreeMap<Double, Double> buckets, double count, double errors) {
    }

    record DbSnap(long tMs, double xacts, double deadlocks, double tempBytes, double inserts, Double readBytes,
                  Double writeBytes, Double calls, Double execMs) {
    }

    public MetricsCollector(ChaosProperties props, V2Properties v2) {
        this.props = props;
        this.v2 = v2;
    }

    @PostConstruct
    void start() {
        exec.scheduleWithFixedDelay(() -> {
            try {
                Sample s = collect();
                latest = s;
                synchronized (history) {
                    history.addLast(s);
                    while (history.size() > HISTORY) {
                        history.removeFirst();
                    }
                }
                for (Consumer<Sample> l : listeners) {
                    try {
                        l.accept(s);
                    } catch (RuntimeException e) {
                        log.warn("sample listener hatası: {}", e.toString());
                    }
                }
            } catch (RuntimeException e) {
                log.warn("ölçüm turu başarısız: {}", e.toString());
            }
        }, 500, v2.sampleIntervalMs(), TimeUnit.MILLISECONDS);
    }

    @PreDestroy
    void stop() {
        exec.shutdownNow();
        closeDb();
    }

    public void addListener(Consumer<Sample> l) {
        listeners.add(l);
    }

    public void removeListener(Consumer<Sample> l) {
        listeners.remove(l);
    }

    public Sample latest() {
        return latest;
    }

    public List<Sample> history(int max) {
        synchronized (history) {
            List<Sample> l = new ArrayList<>(history);
            return l.subList(Math.max(0, l.size() - max), l.size());
        }
    }

    public String dbError() {
        return dbError;
    }

    long nowMs() {
        return (System.nanoTime() - t0) / 1_000_000;
    }

    Sample collect() {
        Map<String, AppSample> apps = new LinkedHashMap<>();
        Map<String, int[]> perAppDb = new HashMap<>();
        DbSample dbs = collectDb(perAppDb);
        for (String app : v2.allowedApps()) {
            String base = props.apps().get(app);
            apps.put(app, base == null ? down() : collectApp(app, base, perAppDb.get(app)));
        }
        return new Sample(Instant.now().toString(), nowMs(), null, apps, dbs, collectAppVm(), List.of(), Map.of());
    }

    private static AppSample down() {
        return new AppSample(false, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null);
    }

    // ------------------------------------------------------------------ uygulamalar

    private static final Predicate<PromText.Line> BUSINESS = l -> {
        String uri = l.label("uri");
        return !uri.startsWith("/lab") && !uri.startsWith("/actuator");
    };

    AppSample collectApp(String app, String base, int[] dbConns) {
        String body;
        try {
            HttpResponse<String> r = http.send(HttpRequest.newBuilder(URI.create(base + "/actuator/prometheus"))
                    .timeout(Duration.ofSeconds(3)).GET().build(), HttpResponse.BodyHandlers.ofString());
            if (r.statusCode() != 200) {
                return down();
            }
            body = r.body();
        } catch (Exception e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            return down();
        }
        return fromPrometheus(app, PromText.parse(body), dbConns);
    }

    public AppSample fromPrometheus(String app, PromText p, int[] dbConns) {
        long t = nowMs();
        Double cpu = p.first("process_cpu_usage", l -> true);
        Double heapUsed = p.sum("jvm_memory_used_bytes", l -> "heap".equals(l.label("area")));
        Double heapMax = p.sum("jvm_memory_max_bytes", l -> "heap".equals(l.label("area")) && l.value() > 0);
        String pool = app + "-pool";
        Predicate<PromText.Line> mainPool = l -> pool.equals(l.label("pool"));
        Double active = p.first("hikaricp_connections_active", mainPool);
        Double idle = p.first("hikaricp_connections_idle", mainPool);
        Double pending = p.first("hikaricp_connections_pending", mainPool);
        Double max = p.first("hikaricp_connections_max", mainPool);

        TreeMap<Double, Double> buckets = p.buckets("http_server_requests_seconds_bucket", BUSINESS);
        Double count = p.sum("http_server_requests_seconds_count", BUSINESS);
        Double errors = p.sum("http_server_requests_seconds_count",
                l -> BUSINESS.test(l) && l.label("status").startsWith("5"));
        AppSnap now = new AppSnap(t, buckets, count == null ? 0 : count, errors == null ? 0 : errors);
        Deque<AppSnap> q = appSnaps.computeIfAbsent(app, k -> new ArrayDeque<>());
        AppSnap ref;
        synchronized (q) {
            q.addLast(now);
            while (q.size() > 2 && q.peekFirst().tMs < t - WINDOW_MS) {
                q.removeFirst();
            }
            ref = q.peekFirst();
        }
        Double rps = null;
        Double errPct = null;
        Double p95 = null;
        Double p99 = null;
        if (ref != null && ref != now && now.count >= ref.count) {
            double dt = (now.tMs - ref.tMs) / 1000.0;
            double dc = now.count - ref.count;
            rps = dt > 0 ? dc / dt : null;
            errPct = dc > 0 ? 100 * (now.errors - ref.errors) / dc : 0.0;
            TreeMap<Double, Double> d = PromText.delta(now.buckets, ref.buckets);
            Double q95 = PromText.quantile(0.95, d);
            Double q99 = PromText.quantile(0.99, d);
            p95 = q95 == null ? null : q95 * 1000;
            p99 = q99 == null ? null : q99 * 1000;
        }
        return new AppSample(true, cpu == null ? null : cpu * 100, mb(heapUsed), mb(heapMax), i(active), i(idle),
                i(pending), i(max), rps, errPct, p95, p99, dbConns == null ? null : dbConns[0],
                dbConns == null ? null : dbConns[1], mb(p.sum("jvm_gc_live_data_size_bytes", l -> true)),
                p.sum("process_start_time_seconds", l -> true));
    }

    private static Double mb(Double b) {
        return b == null ? null : b / (1024 * 1024);
    }

    private static Integer i(Double d) {
        return d == null ? null : (int) Math.round(d);
    }

    // ------------------------------------------------------------------ PostgreSQL (read-only)

    private synchronized Connection dbConn() throws SQLException {
        if (v2.monitorDbUrl() == null || v2.monitorDbUrl().isBlank()) {
            throw new SQLException("MONITOR_DB_URL tanımlı değil");
        }
        if (db == null || db.isClosed() || !db.isValid(2)) {
            closeDb();
            db = DriverManager.getConnection(v2.monitorDbUrl(), v2.monitorDbUser(), v2.monitorDbPassword());
            db.setReadOnly(true);
            try (Statement st = db.createStatement()) {
                st.execute("SET statement_timeout = 3000");
            }
        }
        return db;
    }

    private synchronized void closeDb() {
        if (db != null) {
            try {
                db.close();
            } catch (SQLException ignored) {
                // kapatma en iyi çaba
            }
            db = null;
        }
    }

    private static final String Q_MAIN = """
            SELECT (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend') AS conns,
                   current_setting('max_connections')::int AS maxc,
                   (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND state = 'active'
                       AND application_name <> 'chaos-observer') AS active,
                   (SELECT count(*) FROM pg_stat_activity WHERE state LIKE 'idle in transaction%') AS idle_tx,
                   (SELECT count(*) FROM pg_locks WHERE NOT granted) AS lock_waiters,
                   (SELECT coalesce(extract(epoch FROM max(clock_timestamp() - xact_start)), 0)
                      FROM pg_stat_activity WHERE backend_type = 'client backend' AND xact_start IS NOT NULL
                       AND application_name <> 'chaos-observer') AS longest_xact,
                   d.xact_commit + d.xact_rollback AS xacts, d.deadlocks, d.temp_bytes, d.tup_inserted,
                   pg_database_size(current_database()) AS dbsize
              FROM pg_stat_database d WHERE d.datname = current_database()""";

    DbSample collectDb(Map<String, int[]> perApp) {
        long t = nowMs();
        try {
            Connection c = dbConn();
            int conns;
            int maxc;
            int active;
            int idleTx;
            int waiters;
            double longest;
            double xacts;
            double deadlocks;
            double temp;
            double ins;
            double size;
            try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(Q_MAIN)) {
                rs.next();
                conns = rs.getInt("conns");
                maxc = rs.getInt("maxc");
                active = rs.getInt("active");
                idleTx = rs.getInt("idle_tx");
                waiters = rs.getInt("lock_waiters");
                longest = rs.getDouble("longest_xact");
                xacts = rs.getDouble("xacts");
                deadlocks = rs.getDouble("deadlocks");
                temp = rs.getDouble("temp_bytes");
                ins = rs.getDouble("tup_inserted");
                size = rs.getDouble("dbsize");
            }
            try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery("""
                    SELECT regexp_replace(application_name, '(-service)?(-lab-storm)?$', '') AS app, count(*) AS n,
                           count(*) FILTER (WHERE wait_event_type = 'Lock') AS lw
                      FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1""")) {
                while (rs.next()) {
                    perApp.merge(rs.getString(1), new int[]{rs.getInt(2), rs.getInt(3)},
                            (a, b) -> new int[]{a[0] + b[0], a[1] + b[1]});
                }
            }
            Double readB = null;
            Double writeB = null;
            try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(
                    "SELECT sum(reads * op_bytes)::float8, sum(writes * op_bytes)::float8 FROM pg_stat_io")) {
                rs.next();
                readB = rs.getDouble(1);
                writeB = rs.getDouble(2);
            } catch (SQLException e) {
                log.debug("pg_stat_io okunamadı: {}", e.getMessage());
            }
            Double calls = null;
            Double execMs = null;
            try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery("""
                    SELECT sum(calls)::float8, sum(total_exec_time)::float8 FROM pg_stat_statements
                     WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                       AND query NOT LIKE '%pg_stat%'""")) {
                rs.next();
                calls = rs.getObject(1) == null ? null : rs.getDouble(1);
                execMs = rs.getObject(2) == null ? null : rs.getDouble(2);
            } catch (SQLException e) {
                log.debug("pg_stat_statements okunamadı: {}", e.getMessage());
            }
            Double cpu = null;
            Double mem = null;
            Double load = null;
            Integer cpus = null;
            try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(
                    "SELECT stat, meminfo, loadavg FROM lab_os.host_stats()")) {
                rs.next();
                cpu = dbCpu.cpuPct(rs.getString(1));
                cpus = dbCpu.cpus();
                mem = ProcStats.memUsedPct(rs.getString(2));
                load = ProcStats.load1(rs.getString(3));
            } catch (SQLException e) {
                log.debug("lab_os.host_stats() okunamadı: {}", e.getMessage());
            }
            DbSnap now = new DbSnap(t, xacts, deadlocks, temp, ins, readB, writeB, calls, execMs);
            DbSnap ref;
            synchronized (dbSnaps) {
                dbSnaps.addLast(now);
                while (dbSnaps.size() > 2 && dbSnaps.peekFirst().tMs < t - WINDOW_MS) {
                    dbSnaps.removeFirst();
                }
                ref = dbSnaps.peekFirst();
            }
            Double tps = null;
            Double dlMin = null;
            Double tempMb = null;
            Double insS = null;
            Double rMb = null;
            Double wMb = null;
            Double qMean = null;
            if (ref != null && ref != now) {
                double dt = (now.tMs - ref.tMs) / 1000.0;
                tps = rate(now.xacts, ref.xacts, dt);
                Double dl = rate(now.deadlocks, ref.deadlocks, dt);
                dlMin = dl == null ? null : dl * 60;
                Double tb = rate(now.tempBytes, ref.tempBytes, dt);
                tempMb = tb == null ? null : tb / 1048576;
                insS = rate(now.inserts, ref.inserts, dt);
                if (now.readBytes != null && ref.readBytes != null) {
                    Double r = rate(now.readBytes, ref.readBytes, dt);
                    Double w = rate(now.writeBytes, ref.writeBytes, dt);
                    rMb = r == null ? null : r / 1048576;
                    wMb = w == null ? null : w / 1048576;
                }
                if (now.calls != null && ref.calls != null && now.calls > ref.calls) {
                    qMean = (now.execMs - ref.execMs) / (now.calls - ref.calls);
                }
            }
            dbError = null;
            return new DbSample(true, cpu, mem, load, cpus, conns, maxc, active, idleTx, waiters, longest, tps, dlMin,
                    tempMb, rMb, wMb, insS, qMean, size / 1048576);
        } catch (SQLException e) {
            dbError = e.getMessage();
            closeDb();
            return new DbSample(false, null, null, null, null, null, null, null, null, null, null, null, null, null,
                    null, null, null, null, null);
        }
    }

    private static Double rate(double now, double before, double dt) {
        return dt <= 0 || now < before ? null : (now - before) / dt;     // sayaç sıfırlandıysa (stats reset) null
    }

    // ------------------------------------------------------------------ APP_VM

    HostSample collectAppVm() {
        Double cpu = null;
        Double mem = null;
        try {
            cpu = vmCpu.cpuPct(Files.readString(Path.of("/proc/stat")));
            mem = ProcStats.memUsedPct(Files.readString(Path.of("/proc/meminfo")));
        } catch (Exception e) {
            log.debug("/proc okunamadı: {}", e.toString());
        }
        File data = new File(props.dataDir());
        File probe = data.exists() ? data : new File(".");
        double free = probe.getUsableSpace() / 1e9;
        return new HostSample(cpu, mem, free > 0 ? free : null);
    }
}
