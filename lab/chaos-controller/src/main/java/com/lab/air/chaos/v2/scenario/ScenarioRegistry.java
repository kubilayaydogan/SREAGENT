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
                "Bir uygulama bir tabloyu ACCESS EXCLUSIVE lock ile tutar; o tabloya gelen tüm "
                        + "query'ler, SELECT dahil, lock bırakılana kadar sırada bekler. Gerçek hayattaki "
                        + "karşılığı: yoğun saatte çalıştırılan ALTER TABLE, VACUUM FULL ya da uzun süren bir "
                        + "migration.",
                List.of("V7 chaos şeması (chaos.lock_target)", "log_lock_waits=on"),
                "tabloyu lock'layan uygulama",
                "tek bir tablo (relation lock kuyruğu)", 2, true,
                List.of("pg_locks'ta granted = false olan relation lock'ları ve Lock wait event'i artar",
                        "Tabloyu okuyan her uygulamada query süresi, lock'un tutulduğu süre kadar uzar",
                        "Bekleme lock_timeout'a (8 sn) ulaşırsa okuyan uygulamalar 55P03 hatası alır"),
                List.of("lock_timeout hataları yaygınlaşırsa (error rate > %50)",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur ve lock'u tutan session sonlandırılır; bekleyen query'ler hemen devam"
                        + " eder.",
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
                "Uygulamalar az sayıda ortak row'u güncelleyip transaction'ı bir süre açık tutar; "
                        + "aynı row'ları isteyen diğer transaction'lar row lock için bekler. Gerçek hayattaki "
                        + "karşılığı: sayaç, stok ya da bakiye gibi herkesin güncellediği 'sıcak' row'lar.",
                List.of("V6 chaos şeması", "log_lock_waits=on"),
                "row lock'u tutan ilk transaction",
                "ortak row'lar (row lock)", 1, false,
                List.of("pg_locks'ta bekleyen (granted = false) lock'lar ve PostgreSQL logunda 'still waiting"
                                + " for ShareLock' satırları",
                        "Yüke katılan uygulamalarda işlem süresi, lock'un tutulduğu süre kadar artar",
                        "Kritik yoğunlukta bekleyen request'ler uygulamanın thread'lerini doldurabilir"),
                List.of("lock_timeout hataları yaygınlaşırsa (error rate > %50)",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur ve lock tutan session'lar sonlandırılır; row lock'lar bırakılır.",
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
                "Uygulamalar transaction'ı uzun süre açık bırakır ve bu sürede aldığı row lock'ları "
                        + "tutar; aynı row'lara gelen işlemler arkasında zincirleme bekler (blocking chain). "
                        + "Gerçek hayattaki karşılığı: transaction içinde dış servis çağrısı yapan ya da commit"
                        + " etmeyi unutan kod.",
                List.of("seed'de sıcak uçuşlar (lab.seed hot-flights)", "V6 chaos şeması"),
                "transaction'ı açık tutan uygulama",
                "açık transaction ve tuttuğu row lock'lar", 1, true,
                List.of("En uzun transaction'ın yaşı ve lock bekleyen session sayısı artar",
                        "Yoğun kullanılan uçuşlara giden check-in, booking ve status işlemlerinde p95 / p99 "
                                + "yükselir; lock_timeout dolarsa 503 döner",
                        "Aynı row'ları isteyen diğer uygulamaların işlemleri sıraya girer"),
                List.of("En uzun transaction planlanan süreyi 30 sn'den fazla aşarsa (temizlik başarısız "
                                + "demektir)",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Açık transaction'lar kapatılır; kapanmayan session'lar sonlandırılır.",
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
                "Uygulama; hash hesaplayan, join ve aggregation yapan pahalı query'ler gönderir. Yük,"
                        + " DB CPU'su seçilen yüzdeye ulaşacak şekilde kendiliğinden ayarlanır. Gerçek "
                        + "hayattaki karşılığı: index'siz bir rapor query'si ya da kötü bir execution plan.",
                List.of("chaos.synthetic_data (chaos-seed)", "lab_os.host_stats() (DB CPU ölçümü)"),
                "pahalı query'leri gönderen uygulama",
                "PostgreSQL CPU'su", 1, false,
                List.of("DB_VM CPU kullanımı hedef yüzdeye çıkar, active session sayısı artar",
                        "Yüksek yoğunlukta diğer uygulamaların query süreleri ve p95'i de artar (CPU "
                                + "paylaşılır)"),
                List.of("DB CPU, güvenlik sınırını art arda 3 ölçümde aşarsa",
                        "Query'ler yaygın biçimde statement_timeout'a takılırsa"),
                "Yük durdurulur, çalışan query'ler iptal edilir.",
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
                "Uygulama büyük partiler hâlinde INSERT yapar; WAL hacmi, checkpoint yükü ve disk "
                        + "write artar. Gerçek hayattaki karşılığı: toplu veri yükleme, ETL job'ı ya da "
                        + "kontrolsüz bir import.",
                List.of("chaos.write_sink", "pg_stat_io (PG16+)"),
                "INSERT yükünü üreten uygulama",
                "WAL ve disk write", 1, false,
                List.of("Saniyede eklenen row sayısı, WAL üretimi ve disk write hızı artar",
                        "Diğer uygulamaların write işlemlerinde commit süresi (WAL flush) ve p95 yükselebilir"),
                List.of("Veritabanı boyutu deney başından beri 1 GB'tan fazla artarsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur ve deneyin yazdığı tablo boşaltılır (TRUNCATE).",
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
                "Uygulama, index kullanmadan tablonun büyük bölümünü tarayan query'ler gönderir "
                        + "(sequential scan). Gerçek hayattaki karşılığı: eksik index, WHERE koşulu olmayan bir"
                        + " query ya da tüm tabloyu okuyan bir rapor.",
                List.of("chaos.synthetic_data"),
                "tarama query'lerini gönderen uygulama",
                "buffer cache, CPU ve disk read", 1, false,
                List.of("DB CPU ve okunan row sayısı artar; veri bellekte değilse disk read de artar",
                        "Diğer uygulamaların okuma query'leri yavaşlayabilir"),
                List.of("Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur, çalışan query'ler iptal edilir.",
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
                "Uygulama, belleğe sığmayan büyük sort'lar yapar; PostgreSQL ara sonucu diskteki temp"
                        + " dosyalarına yazar. Gerçek hayattaki karşılığı: work_mem'e sığmayan ORDER BY, hash "
                        + "join ya da büyük bir GROUP BY.",
                List.of("svc_* temp_file_limit=2GB (disk koruması)"),
                "sort query'lerini gönderen uygulama",
                "temp dosyaları ve disk", 1, false,
                List.of("temp_files / temp_bytes sayaçları ve disk write artar; PostgreSQL logunda 'temporary"
                                + " file' satırları görülür",
                        "Aynı diski kullanan diğer query'ler yavaşlayabilir"),
                List.of("temp_file_limit hataları yaygınlaşırsa",
                        "Veritabanı boyutu deney başından beri 1 GB'tan fazla artarsa"),
                "Yük durdurulur; temp dosyalarını PostgreSQL query bitince kendisi siler.",
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
                "Uygulamalar REPEATABLE READ isolation level'ında aynı row'ları okuyup günceller; "
                        + "PostgreSQL çakışan transaction'ı 'could not serialize access' (40001) hatasıyla "
                        + "iptal eder, uygulama sınırlı sayıda retry yapar. Gerçek hayattaki karşılığı: yüksek "
                        + "isolation level kullanan ve aynı kayıtlar üzerinde yarışan servisler.",
                List.of("V6 chaos şeması", "en az 2 uygulama"),
                "çakışan transaction'ları başlatan ilk uygulama",
                "eşzamanlı güncellenen row'lar (snapshot isolation)", 2, true,
                List.of("PostgreSQL logunda 'could not serialize access due to concurrent update' satırları "
                                + "ve rollback sayısında artış",
                        "Katılan uygulamalarda 40001 hataları ve retry'lar: işlem süresi ve error rate artar",
                        "Çakışan güncelleme commit olana kadar kısa row lock beklemeleri"),
                List.of("Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur; açık transaction kalmaz.",
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
                "İki uygulama aynı iki row'u ters sırayla lock'lar ve birbirini bekler; PostgreSQL "
                        + "bunu 1 saniye sonra fark edip transaction'lardan birini 'deadlock detected' (40P01) "
                        + "hatasıyla iptal eder. Gerçek hayattaki karşılığı: aynı kayıtları farklı sırayla "
                        + "güncelleyen iki kod yolu.",
                List.of("chaos.deadlock_pairs", "deadlock_timeout=1s"),
                "row'ları ilk sırayla lock'layan uygulama",
                "ters sırayla lock'lanan row çiftleri", 1, false,
                List.of("pg_stat_database.deadlocks sayacı artar; PostgreSQL logunda 'deadlock detected' "
                                + "satırları görülür",
                        "Katılan uygulamalarda 40P01 hataları ve retry'lar",
                        "Deadlock'a giren her işlem yaklaşık 1 saniye (deadlock_timeout) gecikir"),
                List.of("Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur; açık transaction kalmaz.",
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
                "Uygulama, connection pool kullanmadan çok sayıda kısa ömürlü connection açıp "
                        + "kapatır; her biri veritabanında bir connection slot'u ve yeni bir backend process'i "
                        + "harcar. Gerçek hayattaki karşılığı: pool'suz çalışan bir script, yanlış ayarlanmış "
                        + "bir pool ya da restart sonrası aynı anda bağlanan servisler.",
                List.of("rol CONNECTION LIMIT (40)", "max_connections"),
                "connection'ları açan uygulama",
                "max_connections (connection slot'ları)", 1, false,
                List.of("DB connection kullanımı hedef yüzdeye çıkar; connection kurma maliyeti CPU'yu "
                                + "artırır",
                        "Yüksek yoğunlukta diğer uygulamaların yeni connection'ları 'too many connections' "
                                + "ile reddedilir",
                        "Tek uygulama en fazla 40 connection açabilir; daha yüksek doluluk için birden fazla "
                                + "uygulama seçin"),
                List.of("DB connection kullanımı art arda 3 ölçümde %95'e ulaşırsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur; açılan connection'lar kısa süre içinde kendiliğinden kapanır.",
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
                "Uygulama tek row'luk INSERT'leri ayrı ayrı commit eder: veri hacmi küçük, commit "
                        + "sayısı çok yüksektir ve her commit WAL'ın diske yazılmasını (fsync) bekler. Gerçek "
                        + "hayattaki karşılığı: döngü içinde her kaydı ayrı transaction'da yazan kod (batch "
                        + "yerine tek tek INSERT).",
                List.of("chaos.write_sink", "synchronous_commit=on"),
                "commit yükünü üreten uygulama",
                "WAL flush (commit yolu)", 1, false,
                List.of("Saniyedeki commit sayısı (TPS) ve WAL flush sıklığı artar; disk write küçük ama çok "
                                + "sık olur",
                        "Diğer uygulamaların commit'leri aynı WAL flush sırasına girer; write işlemlerinde "
                                + "p95 yükselebilir"),
                List.of("Veritabanı boyutu deney başından beri 1 GB'tan fazla artarsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur ve deneyin yazdığı tablo temizlenir.",
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
                "Uygulama aynı row'ları sürekli ve toplu hâlde günceller; her UPDATE eski row "
                        + "sürümünü dead tuple olarak bırakır. Tablo, index'ler ve WAL büyür, autovacuum'un işi"
                        + " artar. Gerçek hayattaki karşılığı: sık güncellenen durum ya da sayaç tabloları; "
                        + "vacuum'un yetişemediği yoğun UPDATE trafiği.",
                List.of("V7 chaos şeması (chaos.churn)", "autovacuum açık"),
                "UPDATE yükünü üreten uygulama",
                "dead tuple birikimi ve autovacuum", 1, false,
                List.of("Saniyede güncellenen row sayısı ve dead tuple sayısı artar; autovacuum worker'ları "
                                + "devreye girer",
                        "Disk write artar (tablo, index ve WAL birlikte)",
                        "Autovacuum ve WAL paylaşıldığı için diğer uygulamalarda gecikme artabilir"),
                List.of("Veritabanı boyutu deney başından beri 1 GB'tan fazla artarsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur; dead tuple'ları autovacuum temizler. Tablodaki row sayısı sabit "
                        + "olduğu için boyut sınırlı kalır.",
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
                "Uygulamalar az sayıdaki advisory lock key'ini transaction boyunca tutar; aynı key'i "
                        + "isteyen diğer session'lar sırada bekler. Tablo ya da row lock'lanmaz. Gerçek "
                        + "hayattaki karşılığı: 'aynı anda tek işlem çalışsın' diye advisory lock alan job'lar "
                        + "ya da PostgreSQL'in distributed lock olarak kullanılması.",
                List.of("en az 2 uygulama"),
                "key'i ilk alan uygulama",
                "advisory lock key'leri", 2, true,
                List.of("pg_locks'ta bekleyen advisory lock'lar (locktype = advisory) ve Lock wait event'i",
                        "Katılan uygulamalarda işlem süresi, sıradaki session sayısı × lock'un tutulma süresi"
                                + " kadar artar",
                        "İş tabloları etkilenmez; bekleme yalnız uygulamanın kendi tanımladığı lock'tadır"),
                List.of("lock_timeout hataları yaygınlaşırsa (error rate > %50)",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur; advisory lock'lar transaction bitince kendiliğinden bırakılır.",
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
                "Uygulama, connection pool dışında session'lar açar, transaction başlatır ve hiçbir "
                        + "şey yapmadan bekler ('idle in transaction'). Connection slot'u dolu kalır ve açık "
                        + "snapshot yüzünden vacuum eski row sürümlerini temizleyemez; lock tutulmaz. Gerçek "
                        + "hayattaki karşılığı: commit / rollback edilmeden bırakılmış bir psql oturumu ya da "
                        + "transaction'ı kapatmayan bir uygulama hatası.",
                List.of("rol CONNECTION LIMIT (40)", "idle_in_transaction_session_timeout=30s"),
                "session'ları açık bırakan uygulama",
                "connection slot'ları ve en eski snapshot", 1, false,
                List.of("pg_stat_activity'de 'idle in transaction' session sayısı, en uzun transaction'ın "
                                + "yaşı ve connection sayısı artar",
                        "CPU ve I/O belirgin değişmez (session'lar boşta bekler)",
                        "Birden fazla uygulama katılırsa boş connection slot'u azalır ve vacuum geride kalır"),
                List.of("DB connection kullanımı art arda 3 ölçümde %95'e ulaşırsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Açılan session'lar kapatılır; kapanmayan olursa PostgreSQL 30 sn sonra kendisi "
                        + "sonlandırır (idle_in_transaction_session_timeout).",
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
                "Uygulama, her biri birkaç parallel worker process'i kullanan query'ler gönderir; tek"
                        + " bir query aynı anda birden fazla CPU çekirdeğini meşgul eder ve eşzamanlı query'ler"
                        + " sunucunun ortak worker havuzunu tüketir. Gerçek hayattaki karşılığı: büyük tablolar"
                        + " üzerinde çalışan analitik ve rapor query'leri.",
                List.of("chaos.synthetic_data", "max_parallel_workers > 0"),
                "parallel query'leri gönderen uygulama",
                "parallel worker havuzu ve CPU çekirdekleri", 2, false,
                List.of("Parallel worker process sayısı artar; CPU çekirdeklerinin çoğu aynı anda meşgul olur",
                        "Worker havuzu dolunca yeni query'ler daha az worker ile çalışır ve süreleri "
                                + "dalgalanır",
                        "CPU paylaşıldığı için diğer uygulamalarda p95 yükselebilir"),
                List.of("DB CPU, güvenlik sınırını art arda 3 ölçümde aşarsa"),
                "Yük durdurulur, çalışan query'ler iptal edilir.",
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
                "Uygulama çok sayıda küçük primary key query'si gönderir; her biri çok ucuzdur ama "
                        + "sayıları çok yüksektir. Yük, query başına sabit maliyetten (parse, plan, network "
                        + "round-trip) gelir; write yoktur. Gerçek hayattaki karşılığı: N+1 query pattern'i ya "
                        + "da döngü içinde tek tek lookup yapan kod.",
                List.of("chaos.synthetic_data"),
                "query selini üreten uygulama",
                "query işleme kapasitesi (TPS)", 1, false,
                List.of("Saniyedeki query / transaction sayısı (TPS) artar, query başına süre düşük kalır; "
                                + "CPU query sayısıyla orantılı yükselir",
                        "Disk write ve lock sayısı değişmez",
                        "Yüksek yoğunlukta CPU paylaşımı nedeniyle diğer uygulamalarda gecikme artabilir"),
                List.of("DB CPU, güvenlik sınırını art arda 3 ölçümde aşarsa"),
                "Yük durdurulur.",
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
                "İki yük birlikte uygulanır: CPU-intensive query'ler (S04) ve sequential scan "
                        + "okumaları (S06). Veritabanı doygunluğa ulaşır; aynı veritabanını kullanan tüm "
                        + "uygulamaların query'leri yavaşlar. Gerçek hayattaki karşılığı: yoğun saatte çalışan "
                        + "ağır bir rapor ile index'siz bir query'nin üst üste gelmesi.",
                List.of("chaos.synthetic_data", "en az 2 uygulama"),
                "veritabanını doyuran uygulamalar",
                "PostgreSQL CPU, buffer cache ve active session'lar", 2, true,
                List.of("DB CPU yüksek kalır; active session sayısı ve ortalama query süresi artar",
                        "Tüm uygulamalarda p95 ve connection pool kullanımı yükselir; yüksek yoğunlukta pool "
                                + "timeout görülür"),
                List.of("DB CPU, güvenlik sınırını art arda 3 ölçümde aşarsa"),
                "Yük durdurulur, çalışan query'ler iptal edilir.",
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
                "Uygulama, tamamı bellekte yapılan büyük sort'lar çalıştırır; temp dosyası yazılmaz, "
                        + "her backend onlarca MB bellek kullanır. Gerçek hayattaki karşılığı: work_mem'in "
                        + "yüksek ayarlandığı bir sistemde aynı anda çalışan çok sayıda büyük sort ya da hash "
                        + "işlemi.",
                List.of("lab_os.host_stats() (DB bellek ölçümü)"),
                "sort query'lerini gönderen uygulama",
                "PostgreSQL backend belleği (DB_VM RAM)", 1, false,
                List.of("DB_VM bellek kullanımı artar (eşzamanlı sort sayısı × sort boyutu); CPU yalnız sort "
                                + "sırasında kısa süre yükselir",
                        "Temp dosyası yazılmaz (S07'nin tersi)",
                        "Page cache daralırsa diğer uygulamaların okuma query'leri yavaşlayabilir"),
                List.of("DB_VM bellek kullanımı %95'i aşarsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Yük durdurulur; bellek query bitince serbest kalır.",
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
                "İki yük birlikte uygulanır: idle in transaction session'ları eski snapshot'ı açık "
                        + "tutar (S14) ve yoğun UPDATE dead tuple üretir (S12). Vacuum, açık snapshot yüzünden "
                        + "bu dead tuple'ları temizleyemez; tablo ve index'ler hızla büyür (bloat). Gerçek "
                        + "hayattaki karşılığı: unutulmuş açık bir transaction yüzünden autovacuum'un işe "
                        + "yaramadığı ve tabloların şiştiği durum.",
                List.of("V7 chaos şeması (chaos.churn)", "en az 2 uygulama"),
                "snapshot'ı tutan ve UPDATE yükünü başlatan uygulama",
                "vacuum (en eski snapshot ve dead tuple'lar)", 2, true,
                List.of("En uzun transaction'ın yaşı artar; idle in transaction session'ları görülür",
                        "Güncellenen row sayısı artar; dead tuple sayısı autovacuum çalışsa da düşmez",
                        "Tablo ve index büyüdükçe aynı query'ler daha çok sayfa okur; tüm uygulamalar "
                                + "etkilenebilir"),
                List.of("Veritabanı boyutu deney başından beri 1 GB'tan fazla artarsa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Session'lar kapatılır ve yük durdurulur; snapshot bırakılınca autovacuum dead "
                        + "tuple'ları temizler.",
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
                "Tek başına güvenli olan yük tiplerinden 2–3'ü rastgele seçilir ve farklı "
                        + "uygulamalardan kademeli olarak başlatılır; aynı seed aynı seçimi üretir. Gerçek "
                        + "hayattaki karşılığı: nedeni tek olmayan, birden fazla sorunun üst üste bindiği "
                        + "incident'lar. Teşhis pratiği için uygundur.",
                List.of("CASCADE_POOL senaryolarının önkoşulları"),
                "seçilen yükleri başlatan uygulamalar",
                "PostgreSQL (birden fazla kaynak aynı anda)", 2, true,
                List.of("Seçilen yük tiplerinin belirtileri birlikte görülür",
                        "En az iki uygulamada etki gözlenir"),
                List.of("Seçilen yüklerden herhangi birinin durma koşulu oluşursa",
                        "Deney için belirlenen güvenlik sınırları (budget) aşılırsa"),
                "Tüm yükler durdurulur ve her biri kendi temizliğini yapar.",
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
