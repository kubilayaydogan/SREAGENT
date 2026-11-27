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
 * 20 GERÇEK chaos senaryosu; hepsinin hedefi paylaşılan PostgreSQL'dir. Hiçbiri sahte metrik üretmez: her eylem
 * gerçek bir HTTP çağrısıdır ve uygulama, kendi DB rolüyle gerçek SQL çalıştırır (bkz. lab-common ChaosWorkloads).
 *
 * <p>S01–S16 ve S18 birbirinden farklı tipte tek bir DB yükü üretir; S17, S19 ve S20 birden fazla yük tipini
 * birleştirir. Her senaryoda yükü, initiator ile birlikte seçilen tüm hedef uygulamalar başlatır
 * ({@link #each}); etki paylaşılan DB üzerinden diğer uygulamalara da yansıyabilir.
 *
 * <p>Yoğunluk değerleri dizileri [LIGHT, MEDIUM, HIGH, CRITICAL] sırasıyladır. Mutlak eşikler (ör. DB CPU %)
 * kalibrasyondan bağımsız hedeflerdir; worker üst sınırları ise keşfedilen kapasiteye (pool boyutu,
 * max_connections, uygulama başına chaos op limiti) göre kırpılır.
 */
public final class ScenarioRegistry {

    private final Map<String, ScenarioDef> defs = new LinkedHashMap<>();

    /** S20'nin rastgele birleştirebileceği, tek başına güvenli (ve birbirinden farklı tipte) DB yükleri. */
    public static final List<String> CASCADE_POOL = List.of("S02", "S04", "S06", "S07", "S10", "S11", "S12", "S13",
            "S15", "S16");

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

    /** Aynı workload'ı yüke katılan HER uygulamada başlatır (part adı: "<part>-<app>"). */
    private static List<Action> each(Ctx c, String part, String path, Map<String, String> query, int minWorkers,
                                     int maxWorkers, double ratePerWorker, boolean controlled) {
        List<Action> l = new ArrayList<>();
        for (String app : participants(c)) {
            l.add(Action.loop(part + "-" + app, app, path, new LinkedHashMap<>(query), minWorkers, maxWorkers,
                    ratePerWorker, controlled));
        }
        return l;
    }

    /** Initiator'ın kendi workload part adı (ölçütlerde kullanılır). */
    private static String own(Ctx c, String part) {
        return part + "-" + c.initiator();
    }

    /**
     * Hız hedefli workload'larda worker başına çağrı hızı: tüm uygulamaların tüm worker'ları açıkken toplam hız
     * ≈ 2 × hedef olur; controller hedefi tutturmak için worker sayısını ayarlar.
     */
    private static double paced(Ctx c, double targetPerSec, int unitsPerCall, int maxWorkers) {
        return Math.max(0.2, 2 * targetPerSec / ((double) unitsPerCall * maxWorkers * participants(c).size()));
    }

    // ------------------------------------------------------------------ senaryolar

    private void register() {
        add(new ScenarioDef("S01", "Table-level lock queue", "lock",
                "Bir uygulama chaos.lock_target tablosunu ACCESS EXCLUSIVE kilitleyip tutar (DDL / VACUUM FULL "
                        + "benzeri); tabloyu okuyan tüm uygulamalar relation lock kuyruğunda bekler.",
                List.of("V7 chaos şeması (chaos.lock_target)", "log_lock_waits=on"),
                "tabloyu kilitleyen uygulama", "PostgreSQL relation lock kuyruğu (chaos.lock_target)", 2, true,
                List.of("DB: pg_locks NOT granted ↑ (locktype = relation), Lock wait event",
                        "tabloyu okuyan her uygulama: okuma süresi ≈ kilidin tutulma süresi",
                        "kilit süresi lock_timeout'a (8 s) yaklaşırsa okuyanlarda 55P03 hataları"),
                List.of("lock_timeout ile yaygın hata (error % > 50)", "budget aşımı"),
                "worker'lar durur; /lab/cleanup kilidi tutan backend'i iptal eder → kuyruk boşalır",
                c -> {
                    double tgt = target(c, c.pick(2.0, 4.0, 8.0, 14.0));
                    int hold = Math.min(6000, txMs(c, c.pick(500, 1000, 2000, 4000)));
                    int n = participants(c).size();
                    // her uygulamanın kilidi sırayla tutması için çağrı aralığı: tablo sürenin ~%65'inde kilitli
                    double holdRate = 1000.0 / (hold * 1.5 * n);
                    List<Action> a = each(c, "tablelock-hold", "/lab/chaos/table-lock",
                            p("mode", "hold", "holdMs", "" + hold), 1, 1, holdRate, false);
                    a.addAll(each(c, "tablelock-read", "/lab/chaos/table-lock", p("mode", "read"), 1,
                            workersCap(c, c.pick(4, 8, 12, 20)), 0, true));
                    return new Plan(a, new Control(LOCK_WAITERS, Scope.DB, tgt, 25),
                            List.of(Criterion.atLeast("lock waiters gözlendi", LOCK_WAITERS, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.5)),
                                    Criterion.part("okuma p95 ≥ kilit süresinin yarısı", WL_P95_MS,
                                            own(c, "tablelock-read"), Agg.P90, hold * 0.5, false)),
                            List.of(), params("holdMs", hold, "targetLockWaiters", tgt, "apps", participants(c)));
                }));

        add(new ScenarioDef("S02", "Row lock contention", "lock",
                "Az sayıdaki paylaşılan satır (chaos.shared_records) üzerinde UPDATE + bekletme; diğer worker'lar "
                        + "ve uygulamalar aynı satırlarda row lock bekler.",
                List.of("V6 chaos şeması", "log_lock_waits=on"),
                "lock'u tutan ilk transaction", "paylaşılan PostgreSQL satırları", 1, false,
                List.of("DB: pg_locks NOT granted ↑, Lock wait event, log'da 'still waiting for ShareLock'",
                        "yüke katılan her uygulama: workload gecikmesi ≈ hold süresi kadar artar",
                        "Tomcat thread'leri bekleyen isteklerle dolabilir (CRITICAL)"),
                List.of("lock_timeout (8 s) ile yaygın hata (error % > 50)", "budget aşımı"),
                "worker'lar durur; /lab/cleanup lab:-etiketli backend'leri iptal eder → lock'lar bırakılır",
                c -> {
                    double tgt = target(c, c.pick(1.0, 3.0, 6.0, 12.0));
                    int hold = txMs(c, c.pick(300, 600, 1000, 2000));
                    String rec = "" + c.pick(5, 3, 2, 1);
                    List<Action> a = each(c, "contend", "/lab/chaos/contend",
                            p("records", rec, "holdMs", "" + hold, "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(4, 8, 16, 24)), 0, true);
                    return new Plan(a, new Control(LOCK_WAITERS, Scope.DB, tgt, 25),
                            List.of(Criterion.atLeast("lock waiters gözlendi", LOCK_WAITERS, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.7)),
                                    Criterion.part("contend p95 ≥ hold süresi", WL_P95_MS, own(c, "contend"),
                                            Agg.P90, hold, false)),
                            List.of(), params("records", rec, "holdMs", hold, "targetLockWaiters", tgt));
                }));

        add(new ScenarioDef("S03", "Long-running transactions", "lock",
                "Uygulamalar transaction'ı uzun süre açık tutar: initiator sıcak uçuşların envanter satırını (NO-OP "
                        + "UPDATE, veri değişmez), her katılımcı da ortak chaos satırlarını kilitler. Aynı satırlara "
                        + "gelen diğer transaction'lar bekler → blocking chain.",
                List.of("seed'de sıcak uçuşlar (lab.seed hot-flights)", "V6 chaos şeması"),
                "uzun transaction'ı açan uygulama", "PostgreSQL transaction süresi + tutulan row lock'lar", 1, true,
                List.of("DB: longest transaction age ↑, lock waiters ↑, active-in-transaction session'lar",
                        "sıcak uçuşa giden check-in / booking / status işlemleri: p95/p99 ↑, lock_timeout 503",
                        "aynı chaos satırlarını isteyen diğer uygulamaların workload'ı sırada bekler"),
                List.of("longest xact > süre + 30 s (cleanup başarısız)", "budget aşımı"),
                "long-tx döngüsü cleanup generation ile sonlanır; /lab/cleanup backend'i iptal eder",
                c -> {
                    int dur = txMs(c, c.pick(5000, 15000, 30000, 60000));
                    List<Action> a = new ArrayList<>();
                    a.add(Action.loop("long-tx-hot", c.initiator(), "/lab/chaos/long-tx",
                            p("scope", "business", "flightId", "{hotFlight}", "durationMs", "" + dur,
                                    "errorPct", "" + c.err()),
                            1, workersCap(c, c.pick(1, 1, 2, 3)), 0, false));
                    // uygulama başına en az 2 worker: ikincisi ilkinin arkasında bekler (garantili blocking chain)
                    a.addAll(each(c, "long-tx", "/lab/chaos/long-tx",
                            p("scope", "chaos", "rows", "" + c.pick(5, 20, 50, 100), "durationMs", "" + dur,
                                    "errorPct", "" + c.err()),
                            2, Math.max(2, workersCap(c, c.pick(2, 2, 3, 3))), 0, false));
                    return new Plan(a, null,
                            List.of(Criterion.atLeast("uzun transaction gözlendi (s)", LONGEST_XACT_SEC, Scope.DB,
                                            Agg.MAX, Math.min(dur, 8000) / 1000.0 * 0.6),
                                    Criterion.atLeast("blocking: lock waiters ≥ 1", LOCK_WAITERS, Scope.DB, Agg.MAX, 1),
                                    Criterion.rise("uygulamalarda p95 artışı", APP_P95_MS, Scope.ANY_TARGET, Agg.MAX,
                                            2.0, 50, false)),
                            List.of(), params("txDurationMs", dur, "apps", participants(c)));
                }));

        add(new ScenarioDef("S04", "CPU-intensive queries", "db-cpu",
                "Sentetik veri üzerinde md5 + join + agregasyon sorguları; adaptive controller DB_VM CPU'yu hedef "
                        + "yüzdede tutar.",
                List.of("chaos.synthetic_data (chaos-seed)", "lab_os.host_stats() (DB CPU ölçümü)"),
                "pahalı sorguyu gönderen uygulama", "paylaşılan PostgreSQL CPU'su", 1, false,
                List.of("DB_VM CPU ≈ hedef", "DB active sessions ↑",
                        "diğer uygulamalar: paylaşılan DB CPU nedeniyle sorgu ve p95 gecikmesi artabilir (yüksek yoğunlukta)"),
                List.of("DB CPU > budget (3 örnek)", "statement_timeout yaygın"),
                "worker'lar durur, çalışan sorgular /lab/cleanup ile iptal edilir",
                c -> {
                    double tgt = target(c, c.pick(35.0, 55.0, 70.0, 85.0));
                    return new Plan(each(c, "cpu-query", "/lab/chaos/cpu-query",
                            p("work", "" + c.pick(100, 200, 400, 600), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(4, 8, 16, 24)), rate(c, 0), true),
                            new Control(DB_CPU_PCT, Scope.DB, tgt, 10),
                            List.of(Criterion.atLeast("DB CPU hedefin %80'ine ulaştı", DB_CPU_PCT, Scope.DB, Agg.P90,
                                            tgt * 0.8),
                                    Criterion.rise("diğer uygulamalarda p95 artışı (paylaşılan DB)", APP_P95_MS,
                                            Scope.ANY_NON_INITIATOR, Agg.P90, 1.3, 10, false)),
                            List.of(), params("targetDbCpuPct", tgt));
                }));

        add(new ScenarioDef("S05", "Bulk insert / WAL volume", "db-io",
                "Çok satırlı toplu INSERT (chaos.write_sink): WAL hacmi, checkpoint ve disk write baskısı. Tablo "
                        + "periyodik budanır.",
                List.of("chaos.write_sink", "pg_stat_io (PG16+)"),
                "yazma yükünü üreten uygulama", "PostgreSQL WAL / disk write", 1, false,
                List.of("DB: tuples inserted/s ↑, I/O write MB/s ↑",
                        "diğer uygulamalar: commit gecikmesi (fsync) nedeniyle yazma işlemlerinde p95 ↑ olabilir"),
                List.of("DB boyutu deney başından beri > 1 GB arttı", "budget aşımı"),
                "worker'lar durur; write_sink TRUNCATE (SECURITY DEFINER trim_write_sink)",
                c -> {
                    double tgt = target(c, c.pick(1000.0, 4000.0, 10000.0, 20000.0));
                    int rows = c.pick(200, 500, 1000, 2000);
                    int maxW = workersCap(c, c.pick(2, 4, 8, 16));
                    // worker başına çağrı hızı sınırlı: tüm worker'lar açıkken bile insert hızı ≈ 2 × hedef.
                    // Sınırsız döngüde tek worker bile hedefi katlarca aşar (controller 1 worker'ın altına inemez)
                    // ve DB boyutu growth limitine takılır.
                    double perWorker = rate(c, paced(c, tgt, rows, maxW));
                    return new Plan(each(c, "write-burst", "/lab/chaos/write-burst",
                            p("rows", "" + rows, "payloadBytes", "256", "errorPct", "" + c.err()), 1,
                            maxW, perWorker, true),
                            new Control(INSERTS_PER_SEC, Scope.DB, tgt, 15),
                            List.of(Criterion.atLeast("insert hızı hedefin %50'si", INSERTS_PER_SEC, Scope.DB, Agg.AVG,
                                            tgt * 0.5),
                                    Criterion.rise("I/O write artışı", IO_WRITE_MB_PER_SEC, Scope.DB, Agg.P90, 1.5, 0.5,
                                            false)),
                            List.of(), params("rowsPerCall", rows, "targetInsertsPerSec", tgt));
                }));

        add(new ScenarioDef("S06", "Sequential scan reads", "db-io",
                "İndeks kullanmayan, tablonun büyük kısmını tarayan okuma + agregasyon (seq scan).",
                List.of("chaos.synthetic_data"),
                "tarama sorgularını gönderen uygulama", "PostgreSQL buffer cache / CPU / disk read", 1, false,
                List.of("DB CPU ↑, shared buffer okuma ↑ (cache dışına taşarsa I/O read ↑)",
                        "diğer uygulamalar: okuma sorgularında gecikme artışı"),
                List.of("budget aşımı"),
                "worker'lar durur, sorgular iptal edilir",
                c -> {
                    double tgt = target(c, c.pick(30.0, 50.0, 65.0, 80.0));
                    return new Plan(each(c, "heavy-read", "/lab/chaos/heavy-read",
                            p("scanPct", "" + c.pick(25, 50, 100, 100), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(4, 8, 12, 20)), rate(c, 0), true),
                            new Control(DB_CPU_PCT, Scope.DB, tgt, 10),
                            List.of(Criterion.atLeast("DB CPU hedefin %80'i", DB_CPU_PCT, Scope.DB, Agg.P90, tgt * 0.8),
                                    Criterion.rise("sorgu süresi artışı", QUERY_MEAN_MS, Scope.DB, Agg.P90, 1.2, 0.5,
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
                    return new Plan(each(c, "temp-spill", "/lab/chaos/temp-spill",
                            p("kRows", "" + c.pick(100, 200, 400, 800), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(2, 4, 8, 12)), rate(c, 0), true),
                            new Control(TEMP_MB_PER_SEC, Scope.DB, tgt, 20),
                            List.of(Criterion.atLeast("temp yazımı hedefin %50'si (MB/s)", TEMP_MB_PER_SEC, Scope.DB,
                                    Agg.P90, tgt * 0.5)),
                            List.of(), params("targetTempMbPerSec", tgt));
                }));

        add(new ScenarioDef("S08", "Serialization conflicts", "lock",
                "Uygulamalar REPEATABLE READ transaction içinde aynı satırları okuyup günceller; PostgreSQL çakışan "
                        + "transaction'ı 40001 (could not serialize access) ile geri alır, uygulama sınırlı retry yapar.",
                List.of("V6 chaos şeması", "en az 2 uygulama"),
                "çakışan transaction'ları başlatan ilk uygulama", "PostgreSQL MVCC / snapshot isolation", 2, true,
                List.of("DB: rollback/s ↑ (xact_rollback), log'da 'could not serialize access due to concurrent update'",
                        "katılan uygulamalarda 40001 hataları ve retry → workload gecikmesi ve hata oranı ↑",
                        "kısa süreli row lock beklemeleri (çakışan güncelleme commit olana dek)"),
                List.of("budget aşımı"),
                "worker'lar durur; açık transaction kalmaz",
                c -> {
                    double tgt = target(c, c.pick(1.0, 3.0, 8.0, 15.0));
                    int hold = Math.min(5000, txMs(c, c.pick(50, 100, 150, 250)));
                    String rec = "" + c.pick(40, 20, 8, 4);
                    String retries = "" + c.pick(1, 1, 2, 2);
                    List<Action> a = each(c, "serial", "/lab/chaos/serialization",
                            p("records", rec, "holdMs", "" + hold, "retries", retries), 1,
                            workersCap(c, c.pick(4, 8, 12, 16)), rate(c, 0), true);
                    return new Plan(a, new Control(ROLLBACKS_PER_SEC, Scope.DB, tgt, 30),
                            List.of(Criterion.atLeast("rollback gözlendi (/s)", ROLLBACKS_PER_SEC, Scope.DB, Agg.MAX,
                                            Math.max(0.5, tgt * 0.4)),
                                    Criterion.part("40001 → workload hataları", WL_ERROR_PCT, own(c, "serial"), Agg.MAX,
                                            0.1, false)),
                            List.of(), params("records", rec, "holdMs", hold, "targetRollbacksPerSec", tgt,
                                    "apps", participants(c)));
                }));

        add(new ScenarioDef("S09", "Deadlock", "lock",
                "Uygulamalar aynı satır çiftini ters sırayla kilitler; PostgreSQL deadlock_timeout sonrası birini "
                        + "40P01 ile iptal eder, uygulama sınırlı retry yapar.",
                List.of("chaos.deadlock_pairs", "deadlock_timeout=1s"),
                "a→b sırasıyla kilitleyen uygulama", "chaos.deadlock_pairs satır çiftleri", 1, false,
                List.of("DB: pg_stat_database.deadlocks ↑, log'da 'deadlock detected'",
                        "katılan uygulamalarda 40P01 hataları ve retry", "workload gecikmesi ≈ deadlock_timeout"),
                List.of("budget aşımı"),
                "worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(2.0, 6.0, 15.0, 30.0));
                    int hold = txMs(c, c.pick(300, 300, 500, 800));
                    String pairs = "" + c.pick(20, 10, 5, 3);
                    String retries = "" + c.pick(0, 1, 1, 2);
                    int w = workersCap(c, c.pick(2, 4, 6, 8));
                    List<String> apps = participants(c);
                    List<Action> a = new ArrayList<>();
                    // uygulamalar sırayla a→b ve b→a tarafını alır; tek uygulama varsa iki tarafı da o çalıştırır
                    for (int k = 0; k < Math.max(2, apps.size()); k++) {
                        String order = k % 2 == 0 ? "ab" : "ba";
                        String app = apps.get(k % apps.size());
                        a.add(Action.loop("deadlock-" + order + "-" + app, app, "/lab/chaos/deadlock",
                                p("pair", "{pair:" + pairs + "}", "order", order, "holdMs", "" + hold, "retries", retries),
                                1, w, 0, true));
                    }
                    return new Plan(a, new Control(DEADLOCKS_PER_MIN, Scope.DB, tgt, 30),
                            List.of(Criterion.atLeast("deadlock gözlendi (/dk)", DEADLOCKS_PER_MIN, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.3)),
                                    Criterion.part("40P01 → workload hataları", WL_ERROR_PCT,
                                            "deadlock-ab-" + c.initiator(), Agg.MAX, 0.1, false)),
                            List.of(), params("apps", apps, "pairs", pairs, "holdMs", hold));
                }));

        add(new ScenarioDef("S10", "Connection storm", "connection",
                "Havuz DIŞI kısa ömürlü fiziksel bağlantılar (TCP + auth + backend fork); DB bağlantı slotlarını işgal eder.",
                List.of("rol CONNECTION LIMIT (40)", "max_connections"),
                "bağlantı fırtınasını başlatan uygulama", "PostgreSQL max_connections / auth", 1, false,
                List.of("DB connections ↑ (hedef %), backend fork CPU ↑",
                        "yüksek seviyede diğer uygulamaların yeni bağlantıları reddedilir (FATAL: too many connections)",
                        "tek uygulama rol limiti (40) ile sınırlanır — birden fazla uygulama daha yüksek doluluk üretir"),
                List.of("DB connections ≥ %95 (3 örnek)", "budget aşımı"),
                "storm bağlantıları holdMs sonunda kapanır; worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(40.0, 55.0, 70.0, 85.0));
                    int count = c.custom() != null && c.custom().dbConnections() != null
                            ? c.custom().dbConnections() : c.pick(5, 8, 12, 15);
                    List<Action> a = each(c, "storm", "/lab/chaos/connection-storm",
                            p("count", "" + count, "holdMs", "" + txMs(c, c.pick(1000, 1500, 2500, 3000))), 1,
                            Math.max(1, Math.min(workersCap(c, c.pick(2, 3, 4, 6)),
                                    c.cal().roleConnectionLimit() / Math.max(1, count))), 0, true);
                    double achievable = 100.0 * Math.min(c.cal().maxConnections(),
                            participants(c).size() * c.cal().roleConnectionLimit()) / c.cal().maxConnections();
                    return new Plan(a, new Control(DB_CONNECTIONS_PCT, Scope.DB, Math.min(tgt, achievable - 5), 10),
                            List.of(Criterion.atLeast("DB bağlantı doluluğu", DB_CONNECTIONS_PCT, Scope.DB, Agg.MAX,
                                    Math.min(tgt * 0.8, achievable * 0.6))),
                            List.of("DB connections ≥ %95"),
                            params("connectionsPerCall", count, "achievablePct", Math.round(achievable)));
                }));

        add(new ScenarioDef("S11", "Commit storm", "db-io",
                "Her biri ayrı transaction olan tek satırlık INSERT'ler: satır hacmi küçük, commit sayısı çok yüksek. "
                        + "Her commit WAL flush (fsync) ister.",
                List.of("chaos.write_sink", "synchronous_commit=on"),
                "commit yükünü üreten uygulama", "PostgreSQL WAL flush / commit yolu", 1, false,
                List.of("DB: TPS (commit/s) ↑, WAL flush sıklığı ↑, I/O write küçük ve sık",
                        "diğer uygulamalar: her commit aynı WAL flush kuyruğuna girer → yazma işlemlerinde p95 ↑ olabilir"),
                List.of("DB boyutu deney başından beri > 1 GB arttı", "budget aşımı"),
                "worker'lar durur; write_sink budanır",
                c -> {
                    double tgt = target(c, c.pick(200.0, 500.0, 1000.0, 2000.0));
                    int commits = c.pick(100, 200, 400, 800);
                    int maxW = workersCap(c, c.pick(2, 4, 8, 12));
                    double perWorker = rate(c, paced(c, tgt, commits, maxW));
                    return new Plan(each(c, "commit-storm", "/lab/chaos/commit-storm",
                            p("commits", "" + commits, "payloadBytes", "64"), 1, maxW, perWorker, true),
                            new Control(DB_TPS, Scope.DB, tgt, 20),
                            List.of(Criterion.atLeast("commit hızı hedefin %50'si (TPS)", DB_TPS, Scope.DB, Agg.AVG,
                                            tgt * 0.5),
                                    Criterion.rise("diğer uygulamalarda p95 artışı (ortak WAL)", APP_P95_MS,
                                            Scope.ANY_NON_INITIATOR, Agg.P90, 1.3, 10, false)),
                            List.of(), params("commitsPerCall", commits, "targetTps", tgt));
                }));

        add(new ScenarioDef("S12", "Update churn / dead tuples", "db-maintenance",
                "chaos.churn satırları toplu ve sürekli güncellenir (indeksli sütun değişir → HOT olmayan UPDATE): "
                        + "dead tuple birikir, indeks ve WAL büyür, autovacuum işi artar.",
                List.of("V7 chaos şeması (chaos.churn)", "autovacuum açık"),
                "güncelleme yükünü üreten uygulama", "PostgreSQL MVCC bakımı (dead tuple / autovacuum)", 1, false,
                List.of("DB: tuples updated/s ↑, chaos şemasında dead tuple ↑, autovacuum worker devreye girer",
                        "I/O write ↑ (heap + indeks + WAL)",
                        "diğer uygulamalar: autovacuum ve WAL paylaşımı nedeniyle gecikme artabilir"),
                List.of("DB boyutu deney başından beri > 1 GB arttı", "budget aşımı"),
                "worker'lar durur; dead tuple'ları autovacuum temizler (tablo boyutu sabit satır sayısıyla sınırlı)",
                c -> {
                    double tgt = target(c, c.pick(2000.0, 6000.0, 15000.0, 30000.0));
                    int rows = c.pick(500, 1000, 2000, 4000);
                    int batches = 5;
                    int maxW = workersCap(c, c.pick(2, 4, 6, 10));
                    double perWorker = rate(c, paced(c, tgt, rows * batches, maxW));
                    return new Plan(each(c, "update-churn", "/lab/chaos/update-churn",
                            p("rows", "" + rows, "batches", "" + batches), 1, maxW, perWorker, true),
                            new Control(UPDATES_PER_SEC, Scope.DB, tgt, 20),
                            List.of(Criterion.atLeast("update hızı hedefin %50'si", UPDATES_PER_SEC, Scope.DB, Agg.AVG,
                                            tgt * 0.5),
                                    Criterion.rise("dead tuple birikimi", DEAD_TUPLES, Scope.DB, Agg.MAX, 1.0, tgt,
                                            false)),
                            List.of(), params("rowsPerCall", rows * batches, "targetUpdatesPerSec", tgt));
                }));

        add(new ScenarioDef("S13", "Advisory lock contention", "lock",
                "Uygulamalar az sayıdaki advisory lock anahtarını (pg_advisory_xact_lock) transaction boyunca tutar; "
                        + "aynı anahtarı isteyen diğer oturumlar sırada bekler. Tablo ya da satır kilitlenmez.",
                List.of("en az 2 uygulama"),
                "anahtarı ilk alan uygulama", "PostgreSQL advisory lock alanı", 2, true,
                List.of("DB: pg_locks NOT granted ↑ (locktype = advisory), Lock wait event",
                        "katılan uygulamalar: workload gecikmesi ≈ sıradaki oturum sayısı × tutma süresi",
                        "iş tabloları etkilenmez: bekleme yalnız uygulama seviyesindeki kilitte"),
                List.of("lock_timeout (8 s) ile yaygın hata (error % > 50)", "budget aşımı"),
                "worker'lar durur; transaction bitince advisory lock kendiliğinden bırakılır",
                c -> {
                    double tgt = target(c, c.pick(1.0, 3.0, 5.0, 7.0));
                    int hold = Math.min(10000, txMs(c, c.pick(200, 400, 600, 1000)));
                    String keys = "" + c.pick(8, 4, 2, 1);
                    List<Action> a = each(c, "advisory", "/lab/chaos/advisory-lock",
                            p("keys", keys, "holdMs", "" + hold), 1, workersCap(c, c.pick(4, 8, 12, 16)), 0, true);
                    return new Plan(a, new Control(LOCK_WAITERS, Scope.DB, tgt, 25),
                            List.of(Criterion.atLeast("lock waiters gözlendi", LOCK_WAITERS, Scope.DB, Agg.MAX,
                                            Math.max(1, tgt * 0.6)),
                                    Criterion.part("advisory p95 ≥ tutma süresi", WL_P95_MS, own(c, "advisory"), Agg.P90,
                                            hold, false)),
                            List.of(), params("keys", keys, "holdMs", hold, "targetLockWaiters", tgt,
                                    "apps", participants(c)));
                }));

        add(new ScenarioDef("S14", "Idle-in-transaction sessions", "connection",
                "Havuz DIŞI oturumlar transaction açıp bir kez okur ve hiçbir şey yapmadan bekler: 'idle in "
                        + "transaction'. Bağlantı slotu dolu kalır, snapshot tutulduğu için vacuum eski satır "
                        + "sürümlerini temizleyemez. Lock tutulmaz.",
                List.of("rol CONNECTION LIMIT (40)", "idle_in_transaction_session_timeout=30s"),
                "oturumları açık bırakan uygulama", "PostgreSQL bağlantı slotları + en eski snapshot (xmin)", 1, false,
                List.of("DB: idle in transaction session sayısı ↑, longest transaction age ↑, connections ↑",
                        "CPU ve I/O belirgin değişmez (oturumlar boşta)",
                        "birden fazla uygulama katılırsa boş bağlantı slotu azalır; vacuum geride kalır"),
                List.of("DB connections ≥ %95 (3 örnek)", "budget aşımı"),
                "oturumlar cleanup generation ile kapanır; sunucu tarafı sınır 30 s",
                c -> {
                    int sessions = c.custom() != null && c.custom().dbConnections() != null
                            ? c.custom().dbConnections() : c.pick(3, 5, 8, 10);
                    int hold = Math.min(25000, Math.max(3000, txMs(c, c.pick(8000, 12000, 18000, 25000))));
                    int maxW = Math.max(1, Math.min(workersCap(c, c.pick(1, 2, 3, 3)),
                            (c.cal().roleConnectionLimit() - c.cal().pool(c.initiator()) - 2) / Math.max(1, sessions)));
                    int achievable = participants(c).size() * maxW * sessions;
                    double tgt = Math.min(target(c, c.pick(4.0, 10.0, 20.0, 30.0)), achievable * 0.8);
                    List<Action> a = each(c, "idle-tx", "/lab/chaos/idle-in-tx",
                            p("sessions", "" + sessions, "holdMs", "" + hold), 1, maxW, 0, true);
                    return new Plan(a, new Control(IDLE_IN_TX, Scope.DB, tgt, 25),
                            List.of(Criterion.atLeast("idle in transaction oturumları gözlendi", IDLE_IN_TX, Scope.DB,
                                            Agg.MAX, Math.max(1, tgt * 0.6)),
                                    Criterion.atLeast("transaction yaşı (s)", LONGEST_XACT_SEC, Scope.DB, Agg.MAX,
                                            hold / 1000.0 * 0.5)),
                            List.of("DB connections ≥ %95"),
                            params("sessionsPerCall", sessions, "holdMs", hold, "targetIdleSessions", tgt));
                }));

        add(new ScenarioDef("S15", "Parallel query fan-out", "db-cpu",
                "Sorgular parallel query ile çalıştırılır: her sorgu birkaç parallel worker süreci ister ve aynı "
                        + "anda birden fazla CPU çekirdeğini kullanır. Eşzamanlı sorgular sunucunun ortak worker "
                        + "havuzunu (max_parallel_workers) tüketir.",
                List.of("chaos.synthetic_data", "max_parallel_workers > 0"),
                "parallel sorguları gönderen uygulama", "PostgreSQL parallel worker havuzu + CPU çekirdekleri", 2, false,
                List.of("DB: parallel worker süreçleri ↑, CPU çekirdeklerinin çoğu aynı anda meşgul",
                        "havuz dolunca yeni sorgular daha az worker ile (ya da tek süreçte) çalışır → süre dalgalanır",
                        "diğer uygulamalar: CPU paylaşımı nedeniyle p95 ↑ olabilir"),
                List.of("DB CPU > budget (3 örnek)"),
                "worker'lar durur, çalışan sorgular /lab/cleanup ile iptal edilir",
                c -> {
                    // Ölçüm (2026-11-27, 6 vCPU): 2 worker'lı tek sorgu ≈ 3 süreç ≈ %50 DB CPU; iki uygulamada birer
                    // kesintisiz döngü DB CPU'yu %96'ya çıkardı. Bu yüzden çağrılar seyrekleştirilir (worker başına
                    // 0,5 çağrı/sn) ve yük, parallel worker sayısına göre değil DB CPU'ya göre kontrol edilir.
                    double tgt = target(c, c.pick(30.0, 45.0, 60.0, 75.0));
                    int perQuery = c.pick(1, 2, 2, 3);
                    return new Plan(each(c, "parallel-query", "/lab/chaos/parallel-query",
                            p("workers", "" + perQuery, "scanPct", "" + c.pick(30, 50, 70, 100)), 1,
                            workersCap(c, c.pick(2, 3, 4, 5)), rate(c, 0.5), true),
                            new Control(DB_CPU_PCT, Scope.DB, tgt, 15),
                            List.of(Criterion.atLeast("parallel worker gözlendi", PARALLEL_WORKERS, Scope.DB, Agg.MAX, 1),
                                    Criterion.rise("DB CPU artışı", DB_CPU_PCT, Scope.DB, Agg.P90, 1.2, 5, false)),
                            List.of(), params("targetDbCpuPct", tgt, "workersPerQuery", perQuery,
                                    "apps", participants(c)));
                }));

        add(new ScenarioDef("S16", "Point-query flood", "db-cpu",
                "Çok sayıda küçük primary key sorgusu (her biri ayrı transaction): sorgu başına iş çok az, sorgu "
                        + "sayısı çok yüksek. Yük parse / plan / round-trip maliyetinden gelir; yazma yoktur.",
                List.of("chaos.synthetic_data"),
                "sorgu selini üreten uygulama", "PostgreSQL sorgu işleme hattı (TPS)", 1, false,
                List.of("DB: TPS ↑ (salt okuma), sorgu başına süre düşük kalır, CPU sorgu sayısıyla orantılı ↑",
                        "I/O write ve lock değişmez",
                        "diğer uygulamalar: CPU paylaşımı nedeniyle yüksek yoğunlukta gecikme artabilir"),
                List.of("DB CPU > budget (3 örnek)"),
                "worker'lar durur",
                c -> {
                    double tgt = target(c, c.pick(500.0, 1500.0, 4000.0, 8000.0));
                    int queries = c.pick(200, 500, 1000, 2000);
                    int maxW = workersCap(c, c.pick(2, 4, 8, 12));
                    double perWorker = rate(c, paced(c, tgt, queries, maxW));
                    return new Plan(each(c, "point-queries", "/lab/chaos/point-queries", p("queries", "" + queries), 1,
                            maxW, perWorker, true),
                            new Control(DB_TPS, Scope.DB, tgt, 20),
                            List.of(Criterion.atLeast("sorgu hızı hedefin %50'si (TPS)", DB_TPS, Scope.DB, Agg.AVG,
                                            tgt * 0.5),
                                    Criterion.rise("DB CPU artışı", DB_CPU_PCT, Scope.DB, Agg.P90, 1.1, 3, false)),
                            List.of(), params("queriesPerCall", queries, "targetTps", tgt));
                }));

        add(new ScenarioDef("S17", "CPU + scan saturation", "combined",
                "İki yük tipi birlikte: CPU-yoğun sorgular (S04) + seq scan okumaları (S06). Paylaşılan DB doyar, "
                        + "aynı DB'yi kullanan tüm uygulamaların sorguları yavaşlar.",
                List.of("chaos.synthetic_data", "en az 2 uygulama"),
                "DB'yi doyuran uygulama", "paylaşılan PostgreSQL (CPU + buffer cache + active sessions)", 2, true,
                List.of("DB CPU yüksek, active sessions ↑, query mean latency ↑",
                        "tüm uygulamalar: p95 ↑, pool usage ↑ (sorgular uzadığı için), yüksek seviyede pool timeout"),
                List.of("DB CPU > budget (3 örnek)"),
                "worker'lar durur, çalışan sorgular iptal edilir",
                c -> {
                    double tgt = target(c, c.pick(50.0, 65.0, 80.0, 90.0));
                    List<Action> a = each(c, "cpu-query", "/lab/chaos/cpu-query",
                            p("work", "" + c.pick(200, 300, 400, 600), "errorPct", "" + c.err()), 1,
                            workersCap(c, c.pick(6, 10, 16, 24)), 0, true);
                    a.addAll(each(c, "heavy-read", "/lab/chaos/heavy-read", p("scanPct", "100"), 0,
                            workersCap(c, c.pick(2, 4, 6, 8)), 0, true));
                    return new Plan(a, new Control(DB_CPU_PCT, Scope.DB, tgt, 10),
                            List.of(Criterion.atLeast("DB CPU hedefin %80'i", DB_CPU_PCT, Scope.DB, Agg.P90, tgt * 0.8),
                                    Criterion.rise("uygulamalarda p95 ≥ 1.5×", APP_P95_MS, Scope.ANY_TARGET,
                                            Agg.P90, 1.5, 10, false),
                                    Criterion.rise("query mean latency artışı", QUERY_MEAN_MS, Scope.DB, Agg.P90, 1.3, 0.2,
                                            false)),
                            List.of(), params("targetDbCpuPct", tgt, "loadTypes", List.of("cpu-query", "heavy-read"),
                                    "apps", participants(c)));
                }));

        add(new ScenarioDef("S18", "In-memory sort pressure", "db-memory",
                "work_mem transaction içinde yükseltilir ve büyük sort'lar tamamen bellekte yapılır: temp dosyası "
                        + "yazılmaz, her backend onlarca MB özel bellek kullanır.",
                List.of("lab_os.host_stats() (DB bellek ölçümü)"),
                "sort sorgularını gönderen uygulama", "PostgreSQL backend belleği (DB_VM RAM)", 1, false,
                List.of("DB_VM bellek kullanımı ↑ (eşzamanlı sort sayısı × sort boyutu); CPU yalnız sort anında kısa süre ↑",
                        "temp dosyası yazılmaz (S07'nin tersi)",
                        "page cache daralırsa diğer uygulamaların okuma sorguları yavaşlayabilir"),
                List.of("DB_VM memory > %95", "budget aşımı"),
                "worker'lar durur; bellek sorgu bitince serbest kalır",
                c -> {
                    int mb = c.pick(24, 48, 80, 110);                       // sort başına yaklaşık bellek
                    int total = c.pick(3, 5, 8, 12);                        // tüm uygulamalarda eşzamanlı sort
                    int n = participants(c).size();
                    int perApp = Math.max(1, Math.min(workersCap(c, (int) Math.ceil(total / (double) n)), 12 / n));
                    // open-loop: bellek sert bir kaynaktır; üst sınır = uygulama × worker × sort boyutu (≤ ~1.3 GB).
                    // Sort bitince bellek holdMs boyunca CPU harcamadan tutulur. Ölçüm (2026-11-27): tutma olmadan
                    // art arda sort'lar 5 worker ile DB CPU'yu %97'ye çıkardı (yük belleğe değil CPU'ya biniyordu).
                    int hold = c.pick(4000, 5000, 6000, 7000);
                    List<Action> a = each(c, "memory-sort", "/lab/chaos/memory-sort",
                            p("workMemMb", "" + (mb * 2), "kRows", "" + (mb * 3), "holdMs", "" + hold), 1, perApp, 0,
                            false);
                    return new Plan(a, null,
                            List.of(Criterion.part("sort workload'ı çalıştı", WL_CALLS_PER_SEC, own(c, "memory-sort"),
                                            Agg.AVG, 0.1, true),
                                    Criterion.rise("DB_VM bellek artışı (puan)", DB_MEM_PCT, Scope.DB, Agg.MAX, 1.0,
                                            c.pick(0.5, 1.5, 4.0, 8.0), c.i() >= 2),
                                    Criterion.stays("temp dosyası yazılmadı", TEMP_MB_PER_SEC, Scope.DB, Agg.P90, 1.0, 1)),
                            List.of(), params("sortMb", mb, "holdMs", hold, "workersPerApp", perApp,
                                    "estimatedPeakMb", mb * perApp * n, "apps", participants(c)));
                }));

        add(new ScenarioDef("S19", "Vacuum blocked by old snapshots", "combined",
                "İki yük tipi birlikte: idle-in-transaction oturumları eski snapshot'ı tutar (S14) + yoğun UPDATE "
                        + "dead tuple üretir (S12). Vacuum snapshot yüzünden temizleyemez → bloat hızla büyür.",
                List.of("V7 chaos şeması (chaos.churn)", "en az 2 uygulama"),
                "snapshot'ı tutan ve güncelleme yükünü başlatan uygulama",
                "PostgreSQL MVCC bakımı (xmin horizon + dead tuple)", 2, true,
                List.of("DB: longest transaction age ↑ ve idle in transaction oturumları",
                        "tuples updated/s ↑; dead tuple sayısı autovacuum çalışsa da düşmez",
                        "tablo ve indeks büyür → aynı sorgular daha çok sayfa okur; tüm uygulamalar etkilenebilir"),
                List.of("DB boyutu deney başından beri > 1 GB arttı", "budget aşımı"),
                "oturumlar kapanır, worker'lar durur; snapshot bırakılınca autovacuum dead tuple'ları temizler",
                c -> {
                    double tgt = target(c, c.pick(2000.0, 5000.0, 10000.0, 20000.0));
                    int rows = c.pick(500, 1000, 2000, 2000);
                    int batches = 5;
                    int maxW = workersCap(c, c.pick(2, 3, 4, 6));
                    int hold = 25000;
                    List<Action> a = each(c, "snapshot-hold", "/lab/chaos/idle-in-tx",
                            p("sessions", "" + c.pick(1, 2, 3, 4), "holdMs", "" + hold), 1, 1, 0, false);
                    a.addAll(each(c, "update-churn", "/lab/chaos/update-churn",
                            p("rows", "" + rows, "batches", "" + batches), 1, maxW,
                            paced(c, tgt, rows * batches, maxW), true));
                    return new Plan(a, new Control(UPDATES_PER_SEC, Scope.DB, tgt, 20),
                            List.of(Criterion.atLeast("eski snapshot tutuldu (transaction yaşı, s)", LONGEST_XACT_SEC,
                                            Scope.DB, Agg.MAX, hold / 1000.0 * 0.5),
                                    Criterion.atLeast("update hızı hedefin %50'si", UPDATES_PER_SEC, Scope.DB, Agg.AVG,
                                            tgt * 0.5),
                                    Criterion.rise("dead tuple birikimi (vacuum temizleyemiyor)", DEAD_TUPLES, Scope.DB,
                                            Agg.MAX, 1.0, tgt * 5, c.i() >= 1)),
                            List.of(), params("targetUpdatesPerSec", tgt, "snapshotHoldMs", hold,
                                    "loadTypes", List.of("idle-in-tx", "update-churn"), "apps", participants(c)));
                }));

        add(new ScenarioDef("S20", "Randomized mixed database load", "combined",
                "Seed'e bağlı olarak tek başına güvenli DB yük tiplerinden 2-3'ü seçilir, farklı uygulamalardan "
                        + "kademeli (staggered) başlatılır. Aynı seed aynı planı üretir.",
                List.of("CASCADE_POOL senaryolarının önkoşulları"),
                "seçilen yük tiplerini başlatan uygulamalar", "paylaşılan PostgreSQL (birden fazla kaynak birlikte)",
                2, true,
                List.of("seçilen yük tiplerinin beklenen etkileri birlikte", "en az 2 uygulamada gözlenen etki"),
                List.of("alt senaryoların abort koşulları", "budget aşımı"),
                "tüm alt workload'lar durur ve temizlenir",
                c -> composite(c, pickCascade(c.rnd(), c.i() >= 2 ? 3 : 2), true)));
    }

    static List<String> pickCascade(Random rnd, int n) {
        List<String> pool = new ArrayList<>(CASCADE_POOL);
        Collections.shuffle(pool, rnd);
        return List.copyOf(pool.subList(0, n));
    }

    /**
     * Birden fazla senaryoyu tek planda birleştirir (S20 ve rastgele mod paralel yürütme). Her alt senaryo
     * initiator'ı katılımcılardan sırayla alır (seed'e bağlı bir başlangıçtan; ardışık alt yükler farklı uygulamada); staggered=true ise başlangıçlar 20 s arayla kaydırılır.
     * Kontrol, ilk kontrollü alt senaryodan alınır; ölçütler birleşir ve yalnız ilk alt senaryonunkiler zorunludur.
     */
    public Plan composite(Ctx c, List<String> ids, boolean staggered) {
        List<String> apps = participants(c).size() >= 2 ? participants(c) : c.cal().apps();
        List<Action> actions = new ArrayList<>();
        List<Criterion> crit = new ArrayList<>();
        List<String> abort = new ArrayList<>();
        Control control = null;
        Map<String, Object> parts = new LinkedHashMap<>();
        int first = c.rnd().nextInt(apps.size());       // alt yükler FARKLI uygulamalardan başlar (sırayla)
        for (int k = 0; k < ids.size(); k++) {
            ScenarioDef d = defs.get(ids.get(k));
            String init = apps.get((first + k) % apps.size());
            List<String> others = apps.stream().filter(a -> !a.equals(init)).toList();
            // birleşik etkide toplam yük budget'ı aşmasın: alt senaryolar bir seviye düşük yoğunlukta
            Ctx sub = new Ctx(init, d.minApps() > 1 || d.defaultAllTargets() ? others : List.of(init),
                    lower(c.intensity()), c.custom(), c.cal(), c.rnd());
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

    static Intensity lower(Intensity i) {
        return switch (i) {
            case MEDIUM -> Intensity.LIGHT;
            case HIGH -> Intensity.MEDIUM;
            case CRITICAL -> Intensity.HIGH;
            default -> i;
        };
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
