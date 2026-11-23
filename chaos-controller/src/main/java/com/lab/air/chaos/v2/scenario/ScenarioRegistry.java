package com.lab.air.chaos.v2.scenario;

import com.lab.air.chaos.v2.model.Model.Intensity;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Action;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Agg;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Control;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Criterion;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Ctx;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Plan;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Scope;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Random;

import static com.lab.air.chaos.v2.scenario.Metric.*;

/**
 * 20 GERÇEK chaos senaryosu. Hiçbiri sahte metrik üretmez: her eylem gerçek bir HTTP çağrısıdır ve uygulama içinde
 * gerçek SQL / CPU / heap / ağ etkisi oluşturur (bkz. lab-common ChaosWorkloads, netem-agent).
 *
 * <p>Yoğunluk değerleri dizileri [LIGHT, MEDIUM, HIGH, CRITICAL] sırasıyladır. Mutlak eşikler (ör. DB CPU %)
 * kalibrasyondan bağımsız hedeflerdir; worker üst sınırları ise keşfedilen kapasiteye (pool boyutu,
 * max_connections, uygulama başına chaos op limiti) göre kırpılır.
 */
public final class ScenarioRegistry {

    private final Map<String, ScenarioDef> defs = new LinkedHashMap<>();

    /** S20'nin rastgele birleştirebileceği, tek başına güvenli senaryolar. */
    public static final List<String> CASCADE_POOL = List.of("S02", "S03", "S04", "S06", "S07", "S10", "S13", "S17");

    public ScenarioRegistry() {
        register();
    }

    public List<ScenarioDef> all() {
        return List.copyOf(defs.values());
    }

    public Optional<ScenarioDef> get(String id) {
        return Optional.ofNullable(defs.get(id));
    }

    private void add(ScenarioDef d) {
        defs.put(d.id(), d);
    }

    // ------------------------------------------------------------------ yardımcılar

    private static Map<String, String> p(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    private static int workersCap(Ctx c, int want) {
        int custom = c.custom() != null && c.custom().workers() != null ? c.custom().workers() : want;
        return Math.max(1, Math.min(custom, c.cal().labMaxConcurrentOps() - 2));
    }

    private static double target(Ctx c, double def) {
        return c.custom() != null && c.custom().targetValue() != null ? c.custom().targetValue() : def;
    }

    private static int txMs(Ctx c, int def) {
        int v = c.custom() != null && c.custom().txDurationMs() != null ? c.custom().txDurationMs() : def;
        return Math.max(0, v);
    }

    private static double rate(Ctx c, double def) {
        return c.custom() != null && c.custom().queryFrequency() != null ? c.custom().queryFrequency() : def;
    }

    private static int latency(Ctx c, int def) {
        return c.custom() != null && c.custom().networkLatencyMs() != null ? c.custom().networkLatencyMs() : def;
    }

    private static double loss(Ctx c, double def) {
        return c.custom() != null && c.custom().packetLossPct() != null ? c.custom().packetLossPct() : def;
    }

    private static double rps(Ctx c, double def) {
        return c.custom() != null && c.custom().rps() != null ? c.custom().rps() : def;
    }

    private static Map<String, Object> params(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    private static List<String> participants(Ctx c) {
        List<String> l = new ArrayList<>();
        l.add(c.initiator());
        c.targets().stream().filter(t -> !l.contains(t)).forEach(l::add);
        return l;
    }

    // ------------------------------------------------------------------ senaryolar

    private void register() {
        add(new ScenarioDef("S01", "Connection pool exhaustion", "connection",
                "Initiator uygulamanın Hikari main pool bağlantıları lab hold ile tutulur; pool dolunca iş istekleri "
                        + "connection-timeout (2 s) bekler ve 503 POOL_TIMEOUT alır.",
                List.of("initiator lab profili açık", "Hikari pool metrikleri actuator'da"),
                "pool'u tüketen uygulama", "initiator app'in kendi connection pool'u", 1, false,
                List.of("initiator: pool active = max, pending > 0 (HIGH+), p95/p99 ↑, 503 POOL_TIMEOUT",
                        "DB: initiator rolünün bağlantıları pool boyutunda sabit; DB CPU belirgin değişmez",
                        "diğer uygulamalar: pool'lar ayrık olduğundan doğrudan etki beklenmez"),
                List.of("initiator 30 s boyunca erişilemez", "budget aşımı"),
                "worker'lar durur, /lab/cleanup bekleyen hold'ları iptal eder",
                c -> {
                    int pool = c.cal().pool(c.initiator());
                    double tgt = target(c, c.pick(50.0, 80.0, 110.0, 150.0));
                    int hold = Math.min(c.cal().labMaxHoldMs(), txMs(c, c.pick(1500, 2500, 4000, 5000)));
                    int max = workersCap(c, (int) Math.ceil(pool * tgt / 100.0 * 1.6) + 1);
                    return new Plan(List.of(Action.loop("pool-hold", c.initiator(), "/lab/hold-connection",
                            p("holdMs", "" + hold, "pool", "main"), 1, max, 0, true)),
                            new Control(APP_POOL_DEMAND_PCT, Scope.INITIATOR, tgt, 10),
                            List.of(Criterion.atLeast("pool demand hedefe ulaştı", APP_POOL_DEMAND_PCT, Scope.INITIATOR,
                                            Agg.MAX, tgt * 0.8),
                                    tgt > 100 ? Criterion.atLeast("pool pending > 0 (bekleyen istek)", APP_POOL_PENDING,
                                            Scope.INITIATOR, Agg.MAX, 1) :
                                            Criterion.atLeast("pool usage ≥ %50", APP_POOL_USAGE_PCT, Scope.INITIATOR,
                                                    Agg.MAX, 50),
                                    Criterion.rise("initiator p95 artışı", APP_P95_MS, Scope.INITIATOR, Agg.P90, 1.5, 20,
                                            false),
                                    Criterion.stays("diğer uygulamalar etkilenmez (izolasyon)", APP_P95_MS,
                                            Scope.ANY_NON_INITIATOR, Agg.P90, 1.5, 50)),
                            List.of(), params("poolMax", pool, "holdMs", hold, "targetDemandPct", tgt, "maxWorkers", max));
                }));

        add(new ScenarioDef("S02", "Lock contention", "lock",
                "Az sayıdaki paylaşılan satır (chaos.shared_records) üzerinde UPDATE + bekletme; diğer worker'lar "
                        + "ve hedef uygulamalar aynı satırlarda row lock bekler.",
                List.of("V6 chaos şeması", "log_lock_waits=on"),
                "lock'u tutan ilk transaction", "paylaşılan PostgreSQL satırları", 1, false,
                List.of("DB: pg_locks NOT granted ↑, Lock wait event, log'da 'still waiting for ShareLock'",
                        "initiator ve contend eden hedefler: workload gecikmesi ≈ hold süresi kadar artar",
                        "Tomcat thread'leri bekleyen isteklerle dolabilir (CRITICAL)"),
                List.of("lock_timeout (8 s) ile yaygın hata (error % > 50)", "budget aşımı"),
                "worker'lar durur; /lab/cleanup lab:-etiketli backend'leri iptal eder → lock'lar bırakılır",
                c -> {
                    double tgt = target(c, c.pick(1.0, 3.0, 6.0, 12.0));
                    int hold = txMs(c, c.pick(300, 600, 1000, 2000));
                    String rec = "" + c.pick(5, 3, 2, 1);
                    List<Action> a = new ArrayList<>();
                    for (String app : participants(c)) {
                        a.add(Action.loop("contend-" + app, app, "/lab/chaos/contend",
                                p("records", rec, "holdMs", "" + hold, "errorPct", "" + c.err()), 1,
                                workersCap(c, c.pick(4, 8, 16, 24)), 0, true));
                    }
                    return new Plan(a, new Control(LOCK_WAITERS, Scope.DB, tgt, 25),
                            List.of(Criterion.atLeast("lock waiters gözlendi", LOCK_WAITERS, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.7)),
                                    Criterion.part("contend p95 ≥ hold süresi", WL_P95_MS, "contend-" + c.initiator(),
                                            Agg.P90, hold, false)),
                            List.of(), params("records", rec, "holdMs", hold, "targetLockWaiters", tgt));
                }));

        add(new ScenarioDef("S03", "Long-running transactions", "lock",
                "Initiator, sıcak uçuşların envanter satırına NO-OP UPDATE yapıp transaction'ı uzun süre açık tutar "
                        + "(veri değişmez). Hedef uygulamaların aynı uçuşlara giden check-in / booking / status "
                        + "işlemleri row lock bekler → blocking chain.",
                List.of("seed'de sıcak uçuşlar (lab.seed hot-flights)", "hedeflerde hotRow trafiği"),
                "uzun transaction'ı açan uygulama", "flightops.flight_inventory sıcak satırları", 1, true,
                List.of("DB: longest transaction age ↑, lock waiters ↑, idle/active-in-transaction",
                        "hedefler (reservation, checkin, flightops): sıcak uçuş işlemlerinde p95/p99 ↑, lock_timeout 503",
                        "initiator: kendi iş trafiği etkilenmez (yalnız lab bağlantısı tutulur)"),
                List.of("longest xact > süre + 30 s (cleanup başarısız)", "budget aşımı"),
                "long-tx döngüsü cleanup generation ile sonlanır; /lab/cleanup backend'i iptal eder",
                c -> {
                    int dur = txMs(c, c.pick(5000, 15000, 30000, 60000));
                    List<Action> a = new ArrayList<>();
                    a.add(Action.loop("long-tx", c.initiator(), "/lab/chaos/long-tx",
                            p("scope", "business", "flightId", "{hotFlight}", "durationMs", "" + dur,
                                    "errorPct", "" + c.err()),
                            1, workersCap(c, c.pick(1, 1, 2, 3)), 0, false));
                    for (String t : c.othersThanInitiator()) {
                        a.add(Action.traffic("victim-" + t, t, rps(c, c.pick(3.0, 5.0, 8.0, 12.0)), 8, 10000, 0,
                                true, false));
                    }
                    return new Plan(a, null,
                            List.of(Criterion.atLeast("uzun transaction gözlendi (s)", LONGEST_XACT_SEC, Scope.DB,
                                            Agg.MAX, dur / 1000.0 * 0.6),
                                    Criterion.atLeast("blocking: lock waiters ≥ 1", LOCK_WAITERS, Scope.DB, Agg.MAX, 1),
                                    Criterion.rise("hedef uygulamada p95 artışı", APP_P95_MS, Scope.ANY_TARGET, Agg.MAX,
                                            2.0, 50, c.i() >= 1)),
                            List.of(), params("txDurationMs", dur, "victims", c.othersThanInitiator()));
                }));

        add(new ScenarioDef("S04", "CPU-intensive queries", "db-cpu",
                "Initiator, sentetik veri üzerinde md5 + join + agregasyon sorguları çalıştırır; adaptive controller "
                        + "DB_VM CPU'yu hedef yüzdede tutar.",
                List.of("chaos.synthetic_data (chaos-seed)", "lab_os.host_stats() (DB CPU ölçümü)"),
                "pahalı sorguyu gönderen uygulama", "paylaşılan PostgreSQL CPU'su", 1, false,
                List.of("DB_VM CPU ≈ hedef", "DB active sessions ↑",
                        "diğer uygulamalar: paylaşılan DB CPU nedeniyle sorgu ve p95 gecikmesi artabilir (yüksek yoğunlukta)"),
                List.of("DB CPU > budget (3 örnek)", "statement_timeout yaygın"),
                "worker'lar durur, çalışan sorgular /lab/cleanup ile iptal edilir",
                c -> {
                    double tgt = target(c, c.pick(35.0, 55.0, 70.0, 85.0));
                    return new Plan(List.of(Action.loop("cpu-query", c.initiator(), "/lab/chaos/cpu-query",
                            p("work", "" + c.pick(100, 200, 400, 600), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(4, 8, 16, 24)), rate(c, 0), true)),
                            new Control(DB_CPU_PCT, Scope.DB, tgt, 10),
                            List.of(Criterion.atLeast("DB CPU hedefin %80'ine ulaştı", DB_CPU_PCT, Scope.DB, Agg.P90,
                                            tgt * 0.8),
                                    Criterion.rise("diğer uygulamalarda p95 artışı (paylaşılan DB)", APP_P95_MS,
                                            Scope.ANY_NON_INITIATOR, Agg.P90, 1.3, 10, false)),
                            List.of(), params("targetDbCpuPct", tgt));
                }));

        add(new ScenarioDef("S05", "High-frequency writes", "db-io",
                "Toplu INSERT (chaos.write_sink): WAL, checkpoint ve disk write baskısı. Tablo periyodik budanır.",
                List.of("chaos.write_sink", "pg_stat_io (PG16+)"),
                "yazma yükünü üreten uygulama", "PostgreSQL WAL / disk write", 1, false,
                List.of("DB: tuples inserted/s ↑, I/O write MB/s ↑, TPS ↑",
                        "diğer uygulamalar: commit gecikmesi (fsync) nedeniyle yazma işlemlerinde p95 ↑ olabilir"),
                List.of("DB boyutu deney başından beri > 1 GB arttı", "budget aşımı"),
                "worker'lar durur; write_sink TRUNCATE (SECURITY DEFINER trim_write_sink)",
                c -> {
                    double tgt = target(c, c.pick(2000.0, 8000.0, 20000.0, 40000.0));
                    int rows = c.pick(200, 500, 1000, 2000);
                    return new Plan(List.of(Action.loop("write-burst", c.initiator(), "/lab/chaos/write-burst",
                            p("rows", "" + rows, "payloadBytes", "256", "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(2, 4, 8, 16)), rate(c, 0), true)),
                            new Control(INSERTS_PER_SEC, Scope.DB, tgt, 15),
                            List.of(Criterion.atLeast("insert hızı hedefin %50'si", INSERTS_PER_SEC, Scope.DB, Agg.AVG,
                                            tgt * 0.5),
                                    Criterion.rise("I/O write artışı", IO_WRITE_MB_PER_SEC, Scope.DB, Agg.P90, 1.5, 0.5,
                                            false)),
                            List.of(), params("rowsPerCall", rows, "targetInsertsPerSec", tgt));
                }));

        add(new ScenarioDef("S06", "Heavy read workloads", "db-io",
                "İndeks kullanmayan, tablonun büyük kısmını tarayan okuma + agregasyon (seq scan).",
                List.of("chaos.synthetic_data"),
                "tarama sorgularını gönderen uygulama", "PostgreSQL buffer cache / CPU / disk read", 1, false,
                List.of("DB CPU ↑, shared buffer okuma ↑ (cache dışına taşarsa I/O read ↑)",
                        "diğer uygulamalar: okuma sorgularında gecikme artışı"),
                List.of("budget aşımı"),
                "worker'lar durur, sorgular iptal edilir",
                c -> {
                    double tgt = target(c, c.pick(30.0, 50.0, 65.0, 80.0));
                    return new Plan(List.of(Action.loop("heavy-read", c.initiator(), "/lab/chaos/heavy-read",
                            p("scanPct", "" + c.pick(25, 50, 100, 100), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(4, 8, 12, 20)), rate(c, 0), true)),
                            new Control(DB_CPU_PCT, Scope.DB, tgt, 10),
                            List.of(Criterion.atLeast("DB CPU hedefin %80'i", DB_CPU_PCT, Scope.DB, Agg.P90, tgt * 0.8),
                                    Criterion.rise("DB TPS / okuma etkisi", QUERY_MEAN_MS, Scope.DB, Agg.P90, 1.2, 0.5,
                                            false)),
                            List.of(), params("targetDbCpuPct", tgt));
                }));

        add(new ScenarioDef("S07", "Temporary file pressure", "db-io",
                "work_mem transaction içinde 64kB'a düşürülüp büyük sort yapılır → gerçek temp file yazımı.",
                List.of("svc_* temp_file_limit=2GB (disk koruması)"),
                "sort sorgularını gönderen uygulama", "PostgreSQL temp tablespace / disk", 1, false,
                List.of("DB: temp_files / temp_bytes ↑, I/O write ↑, log'da temporary file satırları",
                        "diğer uygulamalar: disk I/O paylaşımı nedeniyle gecikme artabilir"),
                List.of("temp_file_limit hatası yaygın", "DB boyutu > 1 GB arttı"),
                "worker'lar durur; temp dosyaları sorgu bitince PostgreSQL tarafından silinir",
                c -> {
                    double tgt = target(c, c.pick(5.0, 15.0, 40.0, 80.0));
                    return new Plan(List.of(Action.loop("temp-spill", c.initiator(), "/lab/chaos/temp-spill",
                            p("kRows", "" + c.pick(100, 200, 400, 800), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(2, 4, 8, 12)), rate(c, 0), true)),
                            new Control(TEMP_MB_PER_SEC, Scope.DB, tgt, 20),
                            List.of(Criterion.atLeast("temp yazımı hedefin %50'si (MB/s)", TEMP_MB_PER_SEC, Scope.DB,
                                    Agg.P90, tgt * 0.5)),
                            List.of(), params("targetTempMbPerSec", tgt));
                }));

        add(new ScenarioDef("S08", "Transaction contention (multi-app)", "lock",
                "Birden çok uygulama aynı küçük satır kümesinde kısa, yoğun transaction'lar çalıştırır.",
                List.of("en az 2 uygulama"),
                "contention'a katılan ilk uygulama", "paylaşılan satır kümesi", 2, true,
                List.of("DB: lock waiters ↑, TPS dalgalanır", "katılımcı uygulamaların workload p95'i ↑"),
                List.of("budget aşımı"),
                "worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(1.0, 2.0, 4.0, 8.0));
                    List<Action> a = new ArrayList<>();
                    for (String app : participants(c)) {
                        a.add(Action.loop("tx-" + app, app, "/lab/chaos/contend",
                                p("records", "" + c.pick(50, 20, 8, 3), "holdMs", "" + txMs(c, c.pick(20, 50, 100, 200)),
                                        "errorPct", "" + c.err()),
                                1, workersCap(c, c.pick(4, 8, 12, 16)), rate(c, 0), true));
                    }
                    return new Plan(a, new Control(LOCK_WAITERS, Scope.DB, tgt, 30),
                            List.of(Criterion.atLeast("lock waiters gözlendi", LOCK_WAITERS, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.5)),
                                    Criterion.rise("DB TPS", DB_TPS, Scope.DB, Agg.AVG, 1.1, 1, false)),
                            List.of(), params("participants", participants(c), "targetLockWaiters", tgt));
                }));

        add(new ScenarioDef("S09", "Deadlock", "lock",
                "İki uygulama aynı satır çiftini ters sırayla kilitler; PostgreSQL deadlock_timeout sonrası birini "
                        + "40P01 ile iptal eder, uygulama sınırlı retry yapar.",
                List.of("chaos.deadlock_pairs", "deadlock_timeout=1s"),
                "a→b sırasıyla kilitleyen uygulama", "chaos.deadlock_pairs satır çiftleri", 1, false,
                List.of("DB: pg_stat_database.deadlocks ↑, log'da 'deadlock detected'",
                        "her iki uygulamada 40P01 hataları ve retry", "workload gecikmesi ≈ deadlock_timeout"),
                List.of("budget aşımı"),
                "worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(2.0, 6.0, 15.0, 30.0));
                    String other = c.targets().stream().filter(t -> !t.equals(c.initiator())).findFirst()
                            .orElse(c.initiator());
                    int hold = txMs(c, c.pick(300, 300, 500, 800));
                    String pairs = "" + c.pick(20, 10, 5, 3);
                    String retries = "" + c.pick(0, 1, 1, 2);
                    int w = workersCap(c, c.pick(2, 4, 6, 8));
                    return new Plan(List.of(
                            Action.loop("deadlock-ab", c.initiator(), "/lab/chaos/deadlock",
                                    p("pair", "{pair:" + pairs + "}", "order", "ab", "holdMs", "" + hold, "retries", retries),
                                    1, w, 0, true),
                            Action.loop("deadlock-ba", other, "/lab/chaos/deadlock",
                                    p("pair", "{pair:" + pairs + "}", "order", "ba", "holdMs", "" + hold, "retries", retries),
                                    1, w, 0, true)),
                            new Control(DEADLOCKS_PER_MIN, Scope.DB, tgt, 30),
                            List.of(Criterion.atLeast("deadlock gözlendi (/dk)", DEADLOCKS_PER_MIN, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.3)),
                                    Criterion.part("40P01 → workload hataları", WL_ERROR_PCT, "deadlock-ab", Agg.MAX,
                                            0.1, false)),
                            List.of(), params("otherSide", other, "pairs", pairs, "holdMs", hold));
                }));

        add(new ScenarioDef("S10", "Connection storm", "connection",
                "Havuz DIŞI kısa ömürlü fiziksel bağlantılar (TCP + auth + backend fork); DB bağlantı slotlarını işgal eder.",
                List.of("rol CONNECTION LIMIT (40)", "max_connections"),
                "bağlantı fırtınasını başlatan uygulama", "PostgreSQL max_connections / auth", 1, false,
                List.of("DB connections ↑ (hedef %), backend fork CPU ↑",
                        "yüksek seviyede diğer uygulamaların yeni bağlantıları reddedilir (FATAL: too many connections)",
                        "tek uygulama rol limiti (40) ile sınırlanır — çoklu hedef daha yüksek doluluk üretir"),
                List.of("DB connections ≥ %95 (3 örnek)", "budget aşımı"),
                "storm bağlantıları holdMs sonunda kapanır; worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(40.0, 55.0, 70.0, 85.0));
                    int count = c.custom() != null && c.custom().dbConnections() != null
                            ? c.custom().dbConnections() : c.pick(5, 8, 12, 15);
                    List<Action> a = new ArrayList<>();
                    for (String app : participants(c)) {
                        a.add(Action.loop("storm-" + app, app, "/lab/chaos/connection-storm",
                                p("count", "" + count, "holdMs", "" + txMs(c, c.pick(1000, 1500, 2500, 3000))), 1,
                                Math.max(1, Math.min(workersCap(c, c.pick(2, 3, 4, 6)),
                                        c.cal().roleConnectionLimit() / Math.max(1, count))), 0, true));
                    }
                    double achievable = 100.0 * Math.min(c.cal().maxConnections(),
                            participants(c).size() * c.cal().roleConnectionLimit()) / c.cal().maxConnections();
                    return new Plan(a, new Control(DB_CONNECTIONS_PCT, Scope.DB, Math.min(tgt, achievable - 5), 10),
                            List.of(Criterion.atLeast("DB bağlantı doluluğu", DB_CONNECTIONS_PCT, Scope.DB, Agg.MAX,
                                    Math.min(tgt * 0.8, achievable * 0.6))),
                            List.of("DB connections ≥ %95"),
                            params("connectionsPerCall", count, "achievablePct", Math.round(achievable)));
                }));

        add(new ScenarioDef("S11", "Application CPU saturation", "app-resource",
                "Initiator JVM'inde sınırlı CPU burn; container CPU quota (1 vCPU) dolar, request işleme yavaşlar.",
                List.of("container cpus limiti", "LAB_CHAOS_MAX_CPU_THREADS"),
                "CPU'su doyurulan uygulama", "initiator container CPU'su", 1, false,
                List.of("initiator: process CPU ≈ hedef, p95/p99 ↑ (CFS throttling)",
                        "DB ve diğer uygulamalar: doğrudan etki beklenmez (APP_VM CPU hafif ↑)"),
                List.of("initiator 30 s erişilemez", "APP_VM CPU > %95"),
                "burn thread'leri cleanup generation ile durur",
                c -> {
                    double tgt = target(c, c.pick(40.0, 60.0, 80.0, 95.0));
                    return new Plan(List.of(Action.loop("cpu-burn", c.initiator(), "/lab/chaos/cpu-burn",
                            p("ms", "250", "threads", "1"), 0, workersCap(c, c.pick(2, 3, 4, 6)), 1.0, true)),
                            new Control(APP_CPU_PCT, Scope.INITIATOR, tgt, 10),
                            List.of(Criterion.atLeast("initiator CPU hedefin %80'i", APP_CPU_PCT, Scope.INITIATOR,
                                            Agg.P90, tgt * 0.8),
                                    Criterion.rise("initiator p95 artışı", APP_P95_MS, Scope.INITIATOR, Agg.P90, 1.3, 5,
                                            c.i() >= 2),
                                    Criterion.stays("DB CPU belirgin değişmez", DB_CPU_PCT, Scope.DB, Agg.P90, 1.5, 15)),
                            List.of(), params("targetAppCpuPct", tgt));
                }));

        add(new ScenarioDef("S12", "Application memory pressure", "app-resource",
                "Initiator heap'inde dokunulmuş byte[] ballast; GC baskısı ve pause süresi artar.",
                List.of("LAB_CHAOS_MAX_MEMORY_FRACTION", "container mem_limit"),
                "heap'i dolan uygulama", "initiator JVM heap", 1, false,
                List.of("initiator: heap % ≈ hedef, GC sıklığı ↑, p99 ↑", "OOM beklenmez (ballast sınırlı)"),
                List.of("heap ≥ %97", "initiator erişilemez"),
                "ballast /lab/cleanup ile bırakılır",
                c -> {
                    double tgt = target(c, c.pick(50.0, 65.0, 80.0, 90.0));
                    return new Plan(List.of(Action.loop("memory", c.initiator(), "/lab/chaos/memory",
                            p("mb", "16", "holdMs", "120000"), 0, workersCap(c, c.pick(1, 2, 3, 4)), 0.5, true)),
                            new Control(APP_HEAP_PCT, Scope.INITIATOR, tgt, 8),
                            List.of(Criterion.atLeast("heap hedefin %85'i", APP_HEAP_PCT, Scope.INITIATOR, Agg.MAX,
                                            tgt * 0.85),
                                    Criterion.rise("initiator p99 artışı", APP_P99_MS, Scope.INITIATOR, Agg.P90, 1.2, 5,
                                            false)),
                            List.of("heap ≥ %97"), params("targetHeapPct", tgt));
                }));

        add(new ScenarioDef("S13", "Database network latency", "network",
                "Initiator container'ının DB'ye giden paketlerine tc netem delay (yalnız o container, yalnız DB:5432).",
                List.of("netem sidecar (NET_ADMIN)"),
                "ağı yavaşlatılan uygulama", "initiator → PostgreSQL ağ yolu", 1, false,
                List.of("initiator: her SQL round-trip'e delay eklenir → p95 ↑ (sorgu sayısı × delay)",
                        "initiator pool usage ↑ (bağlantılar daha uzun tutulur), yüksek seviyede pool timeout",
                        "DB ve diğer uygulamalar: etki beklenmez (izolasyon kontrolü)"),
                List.of("initiator error % > 80 (3 örnek)"),
                "netem kuralı silinir (TTL ile de otomatik kalkar)",
                c -> {
                    int d = latency(c, c.pick(20, 50, 150, 400));
                    return new Plan(List.of(Action.netem("netem-db", c.initiator(), "db", d, d / 10, 0),
                            Action.traffic("traffic-" + c.initiator(), c.initiator(), rps(c, c.pick(5.0, 8.0, 10.0, 12.0)),
                                    8, 10000, 0, false, false)),
                            null,
                            List.of(Criterion.rise("initiator p95 ≥ baseline + delay", APP_P95_MS, Scope.INITIATOR,
                                            Agg.P90, 1.0, d, true),
                                    Criterion.rise("initiator pool usage artışı", APP_POOL_USAGE_PCT, Scope.INITIATOR,
                                            Agg.MAX, 1.0, 10, false),
                                    Criterion.stays("diğer uygulamalar etkilenmez", APP_P95_MS, Scope.ANY_NON_INITIATOR,
                                            Agg.P90, 1.5, 30)),
                            List.of(), params("delayMs", d));
                }));

        add(new ScenarioDef("S14", "Database packet loss", "network",
                "Initiator → DB paketlerinin bir yüzdesi düşürülür; TCP retransmission (RTO ≥ 200 ms) tail latency üretir.",
                List.of("netem sidecar (NET_ADMIN)"),
                "paket kaybı yaşayan uygulama", "initiator → PostgreSQL ağ yolu", 1, false,
                List.of("initiator: p99 ↑ (retransmission), yüksek seviyede socket timeout / connection hatası",
                        "diğer uygulamalar: etki beklenmez"),
                List.of("initiator error % > 80 (3 örnek)"),
                "netem kuralı silinir",
                c -> {
                    double l = loss(c, c.pick(1.0, 3.0, 8.0, 15.0));
                    return new Plan(List.of(Action.netem("netem-db", c.initiator(), "db", 0, 0, l),
                            Action.traffic("traffic-" + c.initiator(), c.initiator(), rps(c, c.pick(5.0, 8.0, 10.0, 12.0)),
                                    8, 10000, 0, false, false)),
                            null,
                            List.of(Criterion.rise("initiator p99 artışı", APP_P99_MS, Scope.INITIATOR, Agg.MAX, 1.5,
                                            c.pick(20.0, 100.0, 150.0, 200.0), true),
                                    Criterion.stays("diğer uygulamalar etkilenmez", APP_P95_MS, Scope.ANY_NON_INITIATOR,
                                            Agg.P90, 1.5, 30)),
                            List.of(), params("lossPct", l));
                }));

        add(new ScenarioDef("S15", "App-to-app dependency latency", "network",
                "Initiator, bir peer uygulamayı (allowlist) senkron çağırır; initiator → peer yoluna netem delay eklenir.",
                List.of("LAB_CHAOS_PEERS_* allowlist", "netem sidecar"),
                "bağımlılığı çağıran uygulama", "initiator → peer HTTP yolu", 2, false,
                List.of("initiator: dependency call gecikmesi ≈ delay, Tomcat thread'leri daha uzun meşgul",
                        "peer: kendi metriklerinde değişim beklenmez (gecikme ağda)"),
                List.of("dependency error % > 90"),
                "netem silinir, worker'lar durur",
                c -> {
                    String peer = c.peer();
                    int d = latency(c, c.pick(50, 150, 400, 1000));
                    return new Plan(List.of(Action.netem("netem-peer", c.initiator(), peer, d, d / 10, 0),
                            Action.loop("dependency", c.initiator(), "/lab/chaos/dependency-call",
                                    p("peer", peer, "timeoutMs", "" + c.pick(2000, 2000, 2000, 3000), "retries", "0"), 1,
                                    workersCap(c, c.pick(2, 4, 8, 12)), rate(c, 2.0), false)),
                            null,
                            List.of(Criterion.part("dependency p95 ≥ delay", WL_P95_MS, "dependency", Agg.P90, d, true),
                                    Criterion.stays("peer p95 değişmez", APP_P95_MS, Scope.ANY_TARGET, Agg.P90, 1.5, 30)),
                            List.of(), params("peer", peer, "delayMs", d));
                }));

        add(new ScenarioDef("S16", "Retry storm", "amplification",
                "Peer uygulamanın pool'u hold ile yavaşlatılır; initiator kısa timeout + agresif retry ile çağırır, "
                        + "istemci trafiği de retry yapar → gerçek retry amplification.",
                List.of("peer allowlist", "Hikari connection-timeout"),
                "retry yapan istemci uygulama", "yavaşlatılan peer uygulama", 2, false,
                List.of("initiator: attempts/calls (amplification) ≥ 1.5, 502 DEPENDENCY_FAILED",
                        "peer: pool demand > %100, p95 ↑, 503 POOL_TIMEOUT; gelen istek sayısı retry ile katlanır"),
                List.of("budget aşımı"),
                "worker'lar durur, peer hold'ları /lab/cleanup ile iptal edilir",
                c -> {
                    String peer = c.peer();
                    int pool = c.cal().pool(peer);
                    return new Plan(List.of(
                            Action.loop("peer-slowdown", peer, "/lab/hold-connection",
                                    p("holdMs", "" + Math.min(c.cal().labMaxHoldMs(), c.pick(1500, 2500, 4000, 5000)),
                                            "pool", "main"), pool, pool + c.pick(0, 0, 2, 4), 0, false),
                            Action.loop("retrying-client", c.initiator(), "/lab/chaos/dependency-call",
                                    p("peer", peer, "timeoutMs", "" + c.pick(1000, 800, 500, 300),
                                            "retries", "" + c.pick(2, 3, 4, 5), "backoffMs", "0"), 1,
                                    workersCap(c, c.pick(4, 8, 12, 16)), rate(c, 2.0), false),
                            Action.traffic("traffic-" + peer, peer, rps(c, c.pick(4.0, 6.0, 8.0, 10.0)), 8,
                                    c.pick(1500, 1000, 800, 600), c.pick(1, 2, 3, 3), false, false)),
                            null,
                            List.of(Criterion.part("retry amplification ≥ 1.5", WL_AMPLIFICATION, "retrying-client",
                                            Agg.MAX, 1.5, true),
                                    Criterion.rise("peer p95 artışı", APP_P95_MS, Scope.ANY_TARGET, Agg.P90, 1.5, 50, false),
                                    Criterion.atLeast("peer pool demand ≥ %100", APP_POOL_DEMAND_PCT, Scope.ANY_TARGET,
                                            Agg.MAX, 100).optional()),
                            List.of(), params("peer", peer));
                }));

        add(new ScenarioDef("S17", "Cascading database saturation", "cascade",
                "Initiator pahalı okuma + CPU sorgularıyla paylaşılan DB'yi doyurur; diğer uygulamaların normal iş "
                        + "trafiği yavaşlar (cascade). Etki, initiator dışındaki uygulamalarda ölçülür.",
                List.of("en az 1 hedef uygulamada trafik"),
                "DB'yi doyuran uygulama", "paylaşılan PostgreSQL (CPU + active sessions)", 2, true,
                List.of("DB CPU yüksek, active sessions ↑, query mean latency ↑",
                        "hedef uygulamalar: p95 ↑, pool usage ↑ (sorgular uzadığı için), yüksek seviyede pool timeout",
                        "initiator kendi workload'ında da yavaşlar"),
                List.of("DB CPU > budget (3 örnek)"),
                "worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(50.0, 65.0, 80.0, 90.0));
                    List<Action> a = new ArrayList<>();
                    a.add(Action.loop("cpu-query", c.initiator(), "/lab/chaos/cpu-query",
                            p("work", "" + c.pick(200, 300, 400, 600), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(6, 10, 16, 24)), 0, true));
                    a.add(Action.loop("heavy-read", c.initiator(), "/lab/chaos/heavy-read", p("scanPct", "100"), 0,
                            workersCap(c, c.pick(2, 4, 6, 8)), 0, true));
                    for (String t : c.othersThanInitiator()) {
                        a.add(Action.traffic("victim-" + t, t, rps(c, c.pick(5.0, 8.0, 10.0, 12.0)), 10, 5000, 0, false,
                                false));
                    }
                    return new Plan(a, new Control(DB_CPU_PCT, Scope.DB, tgt, 10),
                            List.of(Criterion.atLeast("DB CPU hedefin %80'i", DB_CPU_PCT, Scope.DB, Agg.P90, tgt * 0.8),
                                    Criterion.rise("cascade: hedef uygulamada p95 ≥ 1.5×", APP_P95_MS, Scope.ANY_TARGET,
                                            Agg.P90, 1.5, 10, c.i() >= 1),
                                    Criterion.rise("query mean latency artışı", QUERY_MEAN_MS, Scope.DB, Agg.P90, 1.3, 0.2,
                                            false)),
                            List.of(), params("targetDbCpuPct", tgt, "victims", c.othersThanInitiator()));
                }));

        add(new ScenarioDef("S18", "Multi-application concurrent load", "load",
                "Seçilen tüm uygulamalara gerçek iş trafiği (Catalog karışımı); adaptive controller kullanıcı sayısını "
                        + "DB CPU hedefine göre ayarlar.",
                List.of("DataPools (PNR / uçuş havuzları) dolu"),
                "trafiği başlatan ilk uygulama", "tüm uygulamalar + paylaşılan DB", 1, true,
                List.of("tüm hedeflerde RPS ↑, DB TPS ve CPU ↑", "kapasiteye yaklaşınca p95 ↑, pool usage ↑"),
                List.of("budget aşımı", "error % > 50 (3 örnek)"),
                "trafik durur",
                c -> {
                    double tgt = target(c, c.pick(30.0, 45.0, 60.0, 75.0));
                    double r = rps(c, c.pick(10.0, 25.0, 50.0, 80.0));
                    List<Action> a = new ArrayList<>();
                    for (String app : participants(c)) {
                        a.add(Action.traffic("load-" + app, app, r, workersCap(c, c.pick(8, 16, 24, 30)), 5000, 0, false,
                                true));
                    }
                    return new Plan(a, new Control(DB_CPU_PCT, Scope.DB, tgt, 15),
                            List.of(Criterion.atLeast("DB CPU hedefin %60'ı", DB_CPU_PCT, Scope.DB, Agg.P90, tgt * 0.6),
                                    Criterion.rise("hedeflerde RPS artışı", APP_RPS, Scope.ANY_TARGET, Agg.AVG, 1.5, 2, true),
                                    Criterion.rise("DB TPS artışı", DB_TPS, Scope.DB, Agg.AVG, 1.3, 5, false)),
                            List.of(), params("rpsPerApp", r, "targetDbCpuPct", tgt, "apps", participants(c)));
                }));

        add(new ScenarioDef("S19", "Slow database + retry amplification", "amplification",
                "Initiator → DB yoluna delay + initiator'a kısa timeout ve retry'lı istemci trafiği: yavaşlık retry ile "
                        + "ek yüke dönüşür.",
                List.of("netem sidecar"),
                "yavaş DB'yi gören ve retry'a maruz kalan uygulama", "initiator → PostgreSQL + istemci retry politikası",
                1, false,
                List.of("istemci: timeout → retry, attempts/requests > 1", "initiator: pool usage ↑, p95 ↑, hata ↑",
                        "DB: initiator rolünün active session'ları ↑"),
                List.of("initiator error % > 90 (3 örnek)"),
                "netem silinir, trafik durur",
                c -> {
                    int d = latency(c, c.pick(50, 100, 200, 400));
                    return new Plan(List.of(Action.netem("netem-db", c.initiator(), "db", d, d / 10, 0),
                            Action.traffic("retrying-traffic", c.initiator(), rps(c, c.pick(8.0, 12.0, 16.0, 20.0)),
                                    c.pick(8, 12, 16, 20), c.pick(1500, 1000, 700, 500), c.pick(1, 2, 3, 3), false,
                                    false)),
                            null,
                            List.of(Criterion.part("retry amplification > 1.1", WL_AMPLIFICATION, "retrying-traffic",
                                            Agg.MAX, 1.1, true),
                                    Criterion.rise("initiator pool usage artışı", APP_POOL_USAGE_PCT, Scope.INITIATOR,
                                            Agg.MAX, 1.0, 15, false),
                                    Criterion.rise("initiator p95 artışı", APP_P95_MS, Scope.INITIATOR, Agg.P90, 1.0, d,
                                            true)),
                            List.of(), params("delayMs", d));
                }));

        add(new ScenarioDef("S20", "Randomized cascading failure", "cascade",
                "Seed'e bağlı olarak tek başına güvenli senaryolardan 2-3'ü seçilir, farklı initiator'larla kademeli "
                        + "(staggered) başlatılır. Aynı seed aynı planı üretir.",
                List.of("CASCADE_POOL senaryolarının önkoşulları"),
                "seçilen alt senaryoların initiator'ları", "birleşik (DB + uygulamalar)", 2, true,
                List.of("alt senaryoların beklenen etkileri birlikte", "en az 2 uygulamada gözlenen etki"),
                List.of("alt senaryoların abort koşulları", "budget aşımı"),
                "tüm alt workload'lar ve netem kuralları temizlenir",
                c -> composite(c, pickCascade(c.rnd(), c.i() >= 2 ? 3 : 2), true)));
    }

    static List<String> pickCascade(Random rnd, int n) {
        List<String> pool = new ArrayList<>(CASCADE_POOL);
        Collections.shuffle(pool, rnd);
        return List.copyOf(pool.subList(0, n));
    }

    /**
     * Birden fazla senaryoyu tek planda birleştirir (S20 ve rastgele mod paralel yürütme). Her alt senaryo
     * initiator'ı katılımcılardan sırayla alır; staggered=true ise başlangıçlar 20 s arayla kaydırılır.
     * Kontrol, ilk kontrollü alt senaryodan alınır; ölçütler birleşir ve yalnız ilk alt senaryonunkiler zorunludur.
     */
    public Plan composite(Ctx c, List<String> ids, boolean staggered) {
        List<String> apps = participants(c).size() >= 2 ? participants(c) : c.cal().apps();
        List<Action> actions = new ArrayList<>();
        List<Criterion> crit = new ArrayList<>();
        List<String> abort = new ArrayList<>();
        Control control = null;
        Map<String, Object> parts = new LinkedHashMap<>();
        for (int k = 0; k < ids.size(); k++) {
            ScenarioDef d = defs.get(ids.get(k));
            String init = apps.get((k + c.rnd().nextInt(apps.size())) % apps.size());
            List<String> others = apps.stream().filter(a -> !a.equals(init)).toList();
            Ctx sub = new Ctx(init, d.minApps() > 1 || d.defaultAllTargets() ? others : List.of(init),
                    c.intensity(), c.custom(), c.cal(), c.rnd());
            Plan p = d.plan().apply(sub);
            int off = staggered ? 20 * k : 0;
            for (Action a : p.actions()) {
                actions.add(a.renamed(d.id() + ":" + a.part()).offset(off + a.startOffsetSec()));
            }
            if (control == null && p.control() != null) {
                control = p.control().scope() == Scope.INITIATOR
                        ? new Control(p.control().metric(), Scope.INITIATOR, p.control().target(), p.control().tolerancePct())
                        : p.control();
            }
            for (Criterion cr : p.criteria()) {
                Criterion x = cr.part() == null ? cr : new Criterion(cr.description(), cr.metric(), cr.scope(), cr.agg(),
                        cr.gte(), cr.absolute(), cr.factor(), cr.add(), cr.required(), d.id() + ":" + cr.part());
                crit.add(k == 0 ? x : x.optional());
            }
            abort.addAll(p.abortExtra());
            parts.put(d.id(), Map.of("initiator", init, "offsetSec", off, "parameters", p.parameters()));
        }
        crit.add(Criterion.rise("en az bir hedef uygulamada p95 artışı", APP_P95_MS, Scope.ANY_TARGET, Agg.P90, 1.3, 10,
                false));
        if (control != null && control.scope() == Scope.INITIATOR) {
            control = null;        // alt senaryonun initiator'ı ana initiator ile aynı olmayabilir → sabit seviye
        }
        return new Plan(actions, control, crit, abort, params("subScenarios", ids, "parts", parts));
    }

    /** UI / API için senaryo özeti (plan fonksiyonu hariç). */
    public static Map<String, Object> describe(ScenarioDef d) {
        return params("id", d.id(), "name", d.name(), "category", d.category(), "description", d.description(),
                "prerequisites", d.prerequisites(), "initiatorRole", d.initiatorRole(), "directTarget", d.directTarget(),
                "minApps", d.minApps(), "defaultAllTargets", d.defaultAllTargets(), "expectedImpact", d.expectedImpact(),
                "abortConditions", d.abortConditions(), "cleanup", d.cleanup(),
                "intensities", List.of(Intensity.LIGHT, Intensity.MEDIUM, Intensity.HIGH, Intensity.CRITICAL,
                        Intensity.CUSTOM));
    }
}
