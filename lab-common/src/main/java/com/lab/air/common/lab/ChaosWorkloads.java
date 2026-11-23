package com.lab.air.common.lab;

import com.lab.air.common.BusinessException;
import com.lab.air.common.RequestContextFilter;
import com.lab.air.common.SqlTag;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.env.Environment;
import org.springframework.http.HttpStatus;

import javax.sql.DataSource;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Semaphore;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Chaos v2 GERÇEK workload'ları. Her biri uygulamanın KENDİ DB rolü ve bağlantı havuzu (veya açıkça ayrı
 * işaretlenmiş fiziksel bağlantılar) ile çalışır; böylece PostgreSQL'deki etki (lock, CPU, temp, WAL, connection)
 * pg_stat_activity / pg_stat_statements / blocking zincirinde doğru uygulamaya atfedilir.
 *
 * <p>Hiçbiri iş verisini yıkıcı biçimde değiştirmez: sentetik workload'lar yalnız {@code chaos.*} tablolarına
 * dokunur; "business" kapsamlı long transaction iş satırına yalnız NO-OP UPDATE (updated_at = updated_at)
 * yapar (gerçek row lock, veri değişmez). Tüm süreler / boyutlar {@link ChaosLabProperties} ile sınırlıdır;
 * SQL'ler {@code lab:chaos-*} etiketlidir ki {@code /lab/cleanup} iptal edebilsin.
 *
 * <p>Error injection: {@code errorPct} verilirse iş yapıldıktan sonra o olasılıkla GERÇEK bir DB hatası
 * (division by zero) üretilir ve transaction rollback olur; PostgreSQL logunda ve uygulama 5xx'inde görünür.
 */
public class ChaosWorkloads {

    private static final Logger log = LoggerFactory.getLogger(ChaosWorkloads.class);
    private static final int MB = 1024 * 1024;

    private final DataSource ds;
    private final ChaosLabProperties props;
    private final Environment env;
    private final String app;
    private final Semaphore permits;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2))
            .version(HttpClient.Version.HTTP_1_1).build();      // h2c upgrade denemesi Tomcat ile IOException verebiliyor

    /** cleanup her çağrıldığında artar; uzun süren döngüler (burn, long tx) bunu görüp çıkar. */
    private final AtomicLong generation = new AtomicLong();
    private final AtomicInteger burning = new AtomicInteger();
    private final AtomicInteger longTx = new AtomicInteger();
    private final ConcurrentLinkedQueue<byte[]> ballast = new ConcurrentLinkedQueue<>();
    private final AtomicLong ballastBytes = new AtomicLong();
    private final AtomicLong writes = new AtomicLong();
    private final AtomicLong deadlocksSeen = new AtomicLong();

    public ChaosWorkloads(DataSource ds, ChaosLabProperties props, Environment env) {
        this.ds = ds;
        this.props = props;
        this.env = env;
        this.app = env.getProperty("spring.application.name", "app");
        this.permits = new Semaphore(props.maxConcurrentOps());
    }

    // ------------------------------------------------------------------ row contention (S02, S08)

    /** Paylaşılan sentetik satırlardan birini günceller ve lock'u holdMs tutar. records küçüldükçe contention artar. */
    public Map<String, Object> contend(int records, long holdMs, int errorPct) throws SQLException {
        int n = clamp(records, 1, 1000);
        long hold = Math.min(Math.max(0, holdMs), props.maxLongTxMs());
        int id = ThreadLocalRandom.current().nextInt(n);
        return withPermit(() -> {
            try (Connection c = ds.getConnection()) {
                c.setAutoCommit(false);
                try {
                    long t0 = System.nanoTime();
                    try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-contend",
                            "UPDATE chaos.shared_records SET counter = counter + 1, touched_by = ?, touched_at = now() WHERE id = ?"))) {
                        ps.setString(1, app);
                        ps.setInt(2, id);
                        ps.executeUpdate();
                    }
                    long lockWaitMs = (System.nanoTime() - t0) / 1_000_000;
                    sleepInTx(c, "chaos-contend", hold);
                    maybeFail(c, errorPct);
                    c.commit();
                    return result("record", id, "lockWaitMs", lockWaitMs, "heldMs", hold);
                } catch (SQLException e) {
                    c.rollback();
                    throw e;
                }
            }
        });
    }

    // ------------------------------------------------------------------ long transaction (S03)

    /**
     * Transaction'ı durationMs boyunca açık tutar. scope=chaos → ilk {@code rows} paylaşılan sentetik satır;
     * scope=business → verilen uçuşun envanter satırına NO-OP UPDATE (gerçek iş akışlarıyla aynı satırı kilitler,
     * veriyi değiştirmez). Bekleme kısa pg_sleep adımlarıyla yapılır: oturum "active" kalır, cleanup iptal edebilir.
     */
    public Map<String, Object> longTransaction(String scope, Long flightId, int rows, long durationMs, int errorPct)
            throws SQLException {
        long dur = Math.min(Math.max(0, durationMs), props.maxLongTxMs());
        long gen = generation.get();
        return withPermit(() -> {
            longTx.incrementAndGet();
            try (Connection c = ds.getConnection()) {
                c.setAutoCommit(false);
                try {
                    int locked;
                    if ("business".equals(scope)) {
                        if (flightId == null) {
                            throw BusinessException.badRequest("FLIGHT_REQUIRED", "scope=business için flightId gerekli");
                        }
                        try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-longtx",
                                "UPDATE flightops.flight_inventory SET updated_at = updated_at WHERE flight_id = ?"))) {
                            ps.setLong(1, flightId);
                            locked = ps.executeUpdate();
                        }
                    } else {
                        try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-longtx",
                                "UPDATE chaos.shared_records SET touched_by = ?, touched_at = now() WHERE id < ?"))) {
                            ps.setString(1, app);
                            ps.setInt(2, clamp(rows, 1, 1000));
                            locked = ps.executeUpdate();
                        }
                    }
                    long end = System.nanoTime() + dur * 1_000_000;
                    while (System.nanoTime() < end && generation.get() == gen) {
                        long left = (end - System.nanoTime()) / 1_000_000;
                        sleepInTx(c, "chaos-longtx", Math.min(2_000, Math.max(1, left)));
                    }
                    maybeFail(c, errorPct);
                    c.commit();
                    return result("scope", scope, "rowsLocked", locked, "heldMs", dur,
                            "interrupted", generation.get() != gen);
                } catch (SQLException e) {
                    c.rollback();
                    throw e;
                }
            } finally {
                longTx.decrementAndGet();
            }
        });
    }

    // ------------------------------------------------------------------ CPU-intensive SQL (S04)

    /** Gerçek CPU: md5 + agregasyon. work = bin satır (1..2000) → bir çağrı ~work×1000 satır işler. */
    public Map<String, Object> cpuQuery(int work, int errorPct) throws SQLException {
        int rows = clamp(work, 1, 2_000) * 1_000;
        return withPermit(() -> timedQuery("chaos-cpu", """
                SELECT count(*), max(h) FROM (
                  SELECT md5(md5(g::text || s.payload)) AS h
                    FROM generate_series(1, ?) g
                    JOIN chaos.synthetic_data s ON s.id = (g::bigint * 7919) % 400000
                ) x""", rows, errorPct));
    }

    // ------------------------------------------------------------------ heavy read (S06)

    /** İndeksiz sıralı tarama + agregasyon; scanPct = tablonun taranan yüzdesi. */
    public Map<String, Object> heavyRead(int scanPct, int errorPct) throws SQLException {
        int pct = clamp(scanPct, 1, 100);
        int mod = ThreadLocalRandom.current().nextInt(3, 97);
        return withPermit(() -> {
            try (Connection c = ds.getConnection()) {
                c.setAutoCommit(false);
                try {
                    long t0 = System.nanoTime();
                    long cnt;
                    try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-read", """
                            SELECT count(*), avg(val), max(payload) FROM chaos.synthetic_data
                             WHERE id < (SELECT (max(id) * ?::numeric / 100)::bigint FROM chaos.synthetic_data)
                               AND k % ? = ?"""))) {
                        ps.setInt(1, pct);
                        ps.setInt(2, mod);
                        ps.setInt(3, ThreadLocalRandom.current().nextInt(mod));
                        try (ResultSet rs = ps.executeQuery()) {
                            rs.next();
                            cnt = rs.getLong(1);
                        }
                    }
                    maybeFail(c, errorPct);
                    c.commit();
                    return result("matched", cnt, "scanPct", pct, "ms", (System.nanoTime() - t0) / 1_000_000);
                } catch (SQLException e) {
                    c.rollback();
                    throw e;
                }
            }
        });
    }

    // ------------------------------------------------------------------ temp file pressure (S07)

    /** work_mem'i transaction içinde 64kB'a düşürüp büyük bir sort yapar → gerçek temp file I/O. */
    public Map<String, Object> tempSpill(int kRows, int errorPct) throws SQLException {
        int rows = clamp(kRows, 10, 2_000) * 1_000;
        return withPermit(() -> {
            try (Connection c = ds.getConnection()) {
                c.setAutoCommit(false);
                try (Statement st = c.createStatement()) {
                    st.execute("SET LOCAL work_mem = '64kB'");
                    long t0 = System.nanoTime();
                    try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-temp", """
                            SELECT payload FROM (
                              SELECT s.payload, md5(s.payload || g::text) AS o
                                FROM generate_series(1, ?) g
                                JOIN chaos.synthetic_data s ON s.id = (g::bigint * 104729) % 400000
                            ) x ORDER BY o OFFSET ? LIMIT 1"""))) {
                        ps.setInt(1, rows);
                        ps.setInt(2, rows / 2);
                        ps.executeQuery().close();
                    }
                    maybeFail(c, errorPct);
                    c.commit();
                    return result("sortedRows", rows, "ms", (System.nanoTime() - t0) / 1_000_000);
                } catch (SQLException e) {
                    c.rollback();
                    throw e;
                }
            }
        });
    }

    // ------------------------------------------------------------------ high-frequency writes (S05)

    /** Toplu INSERT (WAL + I/O). write_sink belirli boyutu aşınca SECURITY DEFINER fonksiyonla budanır. */
    public Map<String, Object> writeBurst(int rows, int payloadBytes, int errorPct) throws SQLException {
        int n = clamp(rows, 1, props.maxWriteRows());
        int reps = clamp(payloadBytes / 32, 1, 64);
        return withPermit(() -> {
            try (Connection c = ds.getConnection()) {
                c.setAutoCommit(false);
                try {
                    long t0 = System.nanoTime();
                    try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-write", """
                            INSERT INTO chaos.write_sink (app, experiment_id, payload)
                            SELECT ?, ?, repeat(md5(random()::text), ?) FROM generate_series(1, ?)"""))) {
                        ps.setString(1, app);
                        ps.setString(2, MDC.get(RequestContextFilter.MDC_EXPERIMENT_ID));
                        ps.setInt(3, reps);
                        ps.setInt(4, n);
                        ps.executeUpdate();
                    }
                    maybeFail(c, errorPct);
                    c.commit();
                    long total = writes.addAndGet(n);
                    if (total / props.writeSinkKeep() != (total - n) / props.writeSinkKeep()) {
                        trimWriteSink(c, props.writeSinkKeep());
                    }
                    return result("rows", n, "bytesPerRow", reps * 32, "ms", (System.nanoTime() - t0) / 1_000_000);
                } catch (SQLException e) {
                    c.rollback();
                    throw e;
                }
            }
        });
    }

    // ------------------------------------------------------------------ deadlock (S09)

    /**
     * (2·pair, 2·pair+1) satırlarını order=ab veya ba sırasıyla kilitler; iki uygulama zıt sırayla aynı çifti
     * çalıştırınca PostgreSQL deadlock_timeout sonrası birini 40P01 ile iptal eder. Uygulama, gerçek bir istemci
     * gibi sınırlı retry yapar; tüm denemeler başarısızsa hata döner.
     */
    public Map<String, Object> deadlock(int pair, String order, long holdMs, int retries) throws SQLException {
        int p = clamp(pair, 0, 199);
        int a = 2 * p;
        int b = 2 * p + 1;
        boolean ab = !"ba".equals(order);
        long hold = Math.min(Math.max(0, holdMs), 5_000);
        int maxAttempts = clamp(retries, 0, 5) + 1;
        return withPermit(() -> {
            int deadlocks = 0;
            for (int attempt = 1; attempt <= maxAttempts; attempt++) {
                try (Connection c = ds.getConnection()) {
                    c.setAutoCommit(false);
                    try {
                        updatePair(c, ab ? a : b);
                        sleepInTx(c, "chaos-deadlock", hold);
                        updatePair(c, ab ? b : a);
                        c.commit();
                        return result("pair", p, "order", ab ? "ab" : "ba", "attempts", attempt, "deadlocks", deadlocks);
                    } catch (SQLException e) {
                        c.rollback();
                        if (!"40P01".equals(e.getSQLState())) {
                            throw e;
                        }
                        deadlocks++;
                        deadlocksSeen.incrementAndGet();
                        log.warn("chaos deadlock algılandı (pair={}, attempt={}/{})", p, attempt, maxAttempts);
                        if (attempt == maxAttempts) {
                            throw e;
                        }
                        sleepQuietly(50L * attempt + ThreadLocalRandom.current().nextInt(50));
                    }
                }
            }
            throw new IllegalStateException("unreachable");
        });
    }

    private void updatePair(Connection c, int id) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-deadlock",
                "UPDATE chaos.deadlock_pairs SET v = v + 1 WHERE id = ?"))) {
            ps.setInt(1, id);
            ps.executeUpdate();
        }
    }

    // ------------------------------------------------------------------ connection storm (S10)

    /**
     * Havuz DIŞI, kısa ömürlü fiziksel bağlantılar (application_name = &lt;servis&gt;-lab-storm). Her bağlantı
     * gerçek TCP + auth + backend fork maliyeti üretir ve holdMs boyunca bir slot işgal eder. Sunucu tarafı
     * sınırlar: rol CONNECTION LIMIT, max_connections.
     */
    public Map<String, Object> connectionStorm(int count, long holdMs) {
        int n = clamp(count, 1, props.maxStormConnections());
        long hold = Math.min(Math.max(0, holdMs), 10_000);
        String url = env.getRequiredProperty("spring.datasource.url")
                .replaceAll("ApplicationName=[^&]*", "ApplicationName=" + app + "-lab-storm");
        String user = env.getRequiredProperty("spring.datasource.username");
        String pw = env.getRequiredProperty("spring.datasource.password");
        AtomicInteger ok = new AtomicInteger();
        AtomicInteger failed = new AtomicInteger();
        AtomicLong connectMs = new AtomicLong();
        List<Thread> ts = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            ts.add(Thread.ofVirtual().start(() -> {
                long t0 = System.nanoTime();
                try (Connection c = DriverManager.getConnection(url, user, pw);
                     Statement st = c.createStatement()) {
                    connectMs.addAndGet((System.nanoTime() - t0) / 1_000_000);
                    st.execute(SqlTag.labTag("chaos-storm", "SELECT 1"));
                    ok.incrementAndGet();
                    sleepQuietly(hold);
                } catch (SQLException e) {
                    failed.incrementAndGet();
                }
            }));
        }
        for (Thread t : ts) {
            try {
                t.join(hold + 15_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        if (ok.get() == 0) {
            throw new BusinessException(HttpStatus.SERVICE_UNAVAILABLE, "STORM_ALL_FAILED",
                    "yeni bağlantıların hepsi reddedildi (" + failed.get() + ")");
        }
        return result("opened", ok.get(), "failed", failed.get(),
                "avgConnectMs", ok.get() == 0 ? 0 : connectMs.get() / ok.get(), "heldMs", hold);
    }

    // ------------------------------------------------------------------ application CPU (S11)

    /** JVM içinde sınırlı CPU burn (container cpus limitiyle gerçek CPU saturation). */
    public Map<String, Object> cpuBurn(long ms, int threads) {
        long dur = Math.min(Math.max(1, ms), props.maxCpuBurnMs());
        int want = clamp(threads, 1, props.maxCpuThreads());
        int got = 0;
        long gen = generation.get();
        List<Thread> ts = new ArrayList<>();
        for (int i = 0; i < want; i++) {
            if (burning.incrementAndGet() > props.maxCpuThreads()) {
                burning.decrementAndGet();
                break;
            }
            got++;
            ts.add(Thread.ofPlatform().name("chaos-burn-" + i).daemon(true).start(() -> {
                try {
                    long end = System.nanoTime() + dur * 1_000_000;
                    double x = 1.0;
                    while (System.nanoTime() < end && generation.get() == gen) {
                        for (int k = 0; k < 20_000; k++) {
                            x = Math.sqrt(x * 1.000001 + k) % 1_000_003;
                        }
                    }
                    if (x == -1) {
                        log.trace("unreachable {}", x);
                    }
                } finally {
                    burning.decrementAndGet();
                }
            }));
        }
        if (got == 0) {
            throw new BusinessException(HttpStatus.TOO_MANY_REQUESTS, "CPU_BURN_LIMIT", "CPU burn thread limiti dolu");
        }
        for (Thread t : ts) {
            try {
                t.join(dur + 2_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        return result("threads", got, "ms", dur);
    }

    // ------------------------------------------------------------------ memory pressure (S12)

    /** Gerçek heap ballast: mb kadar dokunulmuş byte[] holdMs tutulur; toplam max heap oranıyla sınırlı. */
    public Map<String, Object> memory(int mb, long holdMs) {
        long max = Runtime.getRuntime().maxMemory();
        long cap = (long) (max * props.maxMemoryFraction());
        long want = (long) clamp(mb, 1, 4_096) * MB;
        long allowed = Math.max(0, Math.min(want, cap - ballastBytes.get()));
        if (allowed < MB) {
            throw new BusinessException(HttpStatus.TOO_MANY_REQUESTS, "MEMORY_LIMIT",
                    "ballast limiti dolu (" + ballastBytes.get() / MB + " / " + cap / MB + " MB)");
        }
        long hold = Math.min(Math.max(0, holdMs), props.maxMemoryHoldMs());
        List<byte[]> mine = new ArrayList<>();
        for (long done = 0; done + MB <= allowed; done += MB) {
            byte[] chunk = new byte[MB];
            for (int i = 0; i < chunk.length; i += 4096) {
                chunk[i] = (byte) i;               // sayfaları gerçekten dokun (RSS)
            }
            mine.add(chunk);
        }
        long bytes = (long) mine.size() * MB;
        ballast.addAll(mine);
        ballastBytes.addAndGet(bytes);
        long gen = generation.get();
        Thread.ofVirtual().start(() -> {
            sleepQuietly(hold);
            if (generation.get() == gen) {
                release(mine, bytes);
            }
        });
        return result("allocatedMb", bytes / MB, "retainedMb", ballastBytes.get() / MB, "capMb", cap / MB, "holdMs", hold);
    }

    private void release(List<byte[]> mine, long bytes) {
        if (ballast.removeAll(mine)) {
            ballastBytes.addAndGet(-bytes);
        }
    }

    // ------------------------------------------------------------------ app → app dependency (S15, S16, S19)

    private static final Map<String, String> PEER_PATHS = Map.of(
            "reservation", "/api/flights/hot",
            "checkin", "/api/checkin/candidates?limit=20",
            "flightops", "/api/flights/sample?limit=20",
            "baggage", "/api/bags/sample?limit=20");

    /**
     * Allowlist'teki bir peer uygulamanın DB'ye dokunan okuma ucunu çağırır; timeout veya 5xx'te retry eder.
     * Döndürülen attempts/calls oranı retry amplification'dır. Bütün denemeler başarısızsa 502 döner.
     */
    public Map<String, Object> dependencyCall(String peer, int timeoutMs, int retries, long backoffMs) {
        String base = props.peers().get(peer);
        String path = PEER_PATHS.get(peer);
        if (base == null || path == null) {
            throw BusinessException.badRequest("PEER_NOT_ALLOWED", "peer allowlist'te değil: " + peer);
        }
        int maxAttempts = clamp(retries, 0, props.maxDependencyAttempts() - 1) + 1;
        int to = clamp(timeoutMs, 100, 15_000);
        long backoff = Math.min(Math.max(0, backoffMs), 2_000);
        long t0 = System.nanoTime();
        String last = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            var b = HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofMillis(to)).GET()
                    .header("X-Request-Id", "dep-" + ThreadLocalRandom.current().nextLong(1L << 40));
            String exp = MDC.get(RequestContextFilter.MDC_EXPERIMENT_ID);
            if (exp != null) {
                b.header(RequestContextFilter.EXPERIMENT_ID, exp);
            }
            try {
                HttpResponse<Void> res = http.send(b.build(), HttpResponse.BodyHandlers.discarding());
                if (res.statusCode() < 400) {
                    return result("peer", peer, "attempts", attempt, "status", res.statusCode(),
                            "ms", (System.nanoTime() - t0) / 1_000_000);
                }
                last = "HTTP " + res.statusCode();
            } catch (java.net.http.HttpTimeoutException e) {
                last = "timeout";
            } catch (Exception e) {
                last = e.getClass().getSimpleName();
                if (e instanceof InterruptedException) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            if (attempt < maxAttempts && backoff > 0) {
                sleepQuietly(backoff * attempt);
            }
        }
        throw new BusinessException(HttpStatus.BAD_GATEWAY, "DEPENDENCY_FAILED",
                peer + " " + maxAttempts + " denemede başarısız (son: " + last + ")");
    }

    // ------------------------------------------------------------------ cleanup / status

    /** İdempotent: burn ve long tx döngülerini bitirir, ballast'ı bırakır, write_sink'i boşaltır. */
    public Map<String, Object> cleanup() {
        generation.incrementAndGet();
        long freed = ballastBytes.getAndSet(0);
        ballast.clear();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ballastFreedMb", freed / MB);
        out.put("burningThreads", burning.get());
        try (Connection c = ds.getConnection()) {
            out.put("writeSinkTrimmed", trimWriteSink(c, 0));
        } catch (SQLException e) {
            out.put("writeSinkTrimmed", -1);
            out.put("writeSinkError", e.getSQLState());
        }
        return out;
    }

    public Map<String, Object> status() {
        Runtime rt = Runtime.getRuntime();
        return result("activeOps", props.maxConcurrentOps() - permits.availablePermits(),
                "maxOps", props.maxConcurrentOps(), "burningThreads", burning.get(), "longTransactions", longTx.get(),
                "ballastMb", ballastBytes.get() / MB, "heapUsedMb", (rt.totalMemory() - rt.freeMemory()) / MB,
                "heapMaxMb", rt.maxMemory() / MB, "deadlocksSeen", deadlocksSeen.get(),
                "peers", props.peers().keySet());
    }

    // ------------------------------------------------------------------ yardımcılar

    private long trimWriteSink(Connection c, int keep) throws SQLException {
        boolean auto = c.getAutoCommit();
        c.setAutoCommit(true);
        try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag("chaos-trim", "SELECT chaos.trim_write_sink(?)"))) {
            ps.setInt(1, keep);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                return rs.getLong(1);
            }
        } finally {
            c.setAutoCommit(auto);
        }
    }

    private Map<String, Object> timedQuery(String kind, String sql, int param, int errorPct) throws SQLException {
        try (Connection c = ds.getConnection()) {
            c.setAutoCommit(false);
            try {
                long t0 = System.nanoTime();
                try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag(kind, sql))) {
                    ps.setInt(1, param);
                    ps.executeQuery().close();
                }
                maybeFail(c, errorPct);
                c.commit();
                return result("rows", param, "ms", (System.nanoTime() - t0) / 1_000_000);
            } catch (SQLException e) {
                c.rollback();
                throw e;
            }
        }
    }

    private static void sleepInTx(Connection c, String kind, long ms) throws SQLException {
        if (ms <= 0) {
            return;
        }
        try (PreparedStatement ps = c.prepareStatement(SqlTag.labTag(kind, "SELECT pg_sleep(?)"))) {
            ps.setDouble(1, ms / 1000.0);
            ps.execute();
        }
    }

    /** errorPct olasılıkla GERÇEK bir SQL hatası üretir (22012 division_by_zero) → çağıran rollback eder. */
    private static void maybeFail(Connection c, int errorPct) throws SQLException {
        if (errorPct > 0 && ThreadLocalRandom.current().nextInt(100) < Math.min(errorPct, 100)) {
            try (Statement st = c.createStatement()) {
                st.execute(SqlTag.labTag("chaos-error", "SELECT 1 / 0"));
            }
        }
    }

    @FunctionalInterface
    private interface SqlWork {
        Map<String, Object> run() throws SQLException;
    }

    private Map<String, Object> withPermit(SqlWork w) throws SQLException {
        if (!permits.tryAcquire()) {
            throw new BusinessException(HttpStatus.TOO_MANY_REQUESTS, "CHAOS_OPS_LIMIT",
                    "eşzamanlı chaos operasyonu limiti dolu (" + props.maxConcurrentOps() + ")");
        }
        try {
            return w.run();
        } finally {
            permits.release();
        }
    }

    private static Map<String, Object> result(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    private static void sleepQuietly(long ms) {
        if (ms <= 0) {
            return;
        }
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
