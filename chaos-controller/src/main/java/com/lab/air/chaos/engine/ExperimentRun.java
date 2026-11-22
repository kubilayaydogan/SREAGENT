package com.lab.air.chaos.engine;

import com.lab.air.chaos.engine.ExperimentSpec.ActivePhases;
import com.lab.air.chaos.engine.ExperimentSpec.Mode;
import com.lab.air.chaos.engine.ExperimentSpec.TargetSpec;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.ops.DataPools;
import com.lab.air.chaos.ops.OpDef;
import com.lab.air.chaos.store.ExperimentRecord;
import com.sun.management.OperatingSystemMXBean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.util.AbstractMap;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Tek bir deneyin yaşam döngüsü. Saat iş parçacığı fazları ilerletir:
 * WARMUP → BASELINE → LOAD → RECOVERY → DONE. Arka plan trafiği baştan sona sürer;
 * stres hedefleri yalnız LOAD fazında, probe hedefleri (activePhases=ALL) tüm fazlarda çalışır.
 */
public final class ExperimentRun {

    private static final Logger log = LoggerFactory.getLogger(ExperimentRun.class);

    public enum State { RUNNING, STOPPING, COMPLETED, STOPPED, FAILED }

    private final String id;
    private final ExperimentSpec spec;
    private final String specHash;
    private final Catalog catalog;
    private final DataPools pools;
    private final Sender sender;
    private final Stats stats;
    private final HttpClient http;
    private final Map<String, String> baseUrls;
    private final String labToken;
    private final Consumer<ExperimentRecord> persister;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
    private final Instant createdAt = Instant.now();

    private final List<Generator> background = new ArrayList<>();
    private final List<Generator> stress = new ArrayList<>();
    private final List<Generator> probes = new ArrayList<>();
    private final EnumMap<Phase, Instant> phaseStart = new EnumMap<>(Phase.class);
    private final EnumMap<Phase, Instant> phaseEnd = new EnumMap<>(Phase.class);
    private final Map<String, String> cleanup = new LinkedHashMap<>();
    private final Map<String, Object> producer = new LinkedHashMap<>();

    private volatile Phase phase = Phase.WARMUP;
    private volatile State state = State.RUNNING;
    private volatile String stopReason;
    private volatile boolean stopRequested;
    private volatile Instant startedAt;
    private volatile Instant endedAt;
    private Thread clock;

    // üretici kaynak örnekleri
    private double cpuSum;
    private double cpuMax;
    private long heapMax;
    private int threadsMax;
    private int samples;

    // İzleme sayfası: olay günlüğü + 5 sn'lik zaman serisi (kayıtla birlikte saklanır; boyutları sınırlı)
    private static final int MAX_EVENTS = 1000;
    private static final int MAX_TIMELINE = 1440;          // 5 sn × 1440 = 2 saat
    private static final long TIMELINE_STEP_MS = 5000;
    private static final long ERROR_EVENT_EVERY_MS = 30_000;
    private final List<Map<String, Object>> events = new ArrayList<>();
    private final List<Map<String, Object>> timeline = new ArrayList<>();
    private final Map<String, long[]> prevTotals = new LinkedHashMap<>();   // kaynak/app → completed, errors, dropped
    private final Map<String, long[]> pendingErr = new LinkedHashMap<>();   // kaynak/app → bildirilmemiş hata, düşen, son olay ms
    private long prevSampleMs;

    public ExperimentRun(String id, ExperimentSpec spec, String specHash, Catalog catalog, DataPools pools,
                         Sender sender, Stats stats, HttpClient http, Map<String, String> baseUrls, String labToken,
                         Consumer<ExperimentRecord> persister) {
        this.id = id;
        this.spec = spec;
        this.specHash = specHash;
        this.catalog = catalog;
        this.pools = pools;
        this.sender = sender;
        this.stats = stats;
        this.http = http;
        this.baseUrls = baseUrls;
        this.labToken = labToken;
        this.persister = persister;
    }

    public void start() {
        startedAt = Instant.now();
        buildGenerators();
        clock = Thread.ofPlatform().name("exp-clock-" + id).daemon().start(this::runClock);
    }

    private void buildGenerators() {
        var bg = spec.background();
        if (bg.enabled() && bg.rpsPerApp() > 0) {
            List<String> apps = bg.apps() == null || bg.apps().isEmpty() ? Catalog.APPS : bg.apps();
            for (String app : apps) {
                var ctx = new OpDef.OpContext(new OpDef.Params(1, false, 0), pools, sender.ctxTemplate().json());
                background.add(new Generator.Rps(app, "background", weighted(app, catalog.normalMix(app)), ctx, sender,
                        stats, this::phase, 5000, 0, bg.rpsPerApp(), 16, executor));
            }
        }
        for (TargetSpec t : spec.targets()) {
            var ctx = new OpDef.OpContext(new OpDef.Params(t.dataSize(), t.hotRow(), t.holdMs()), pools,
                    sender.ctxTemplate().json());
            var w = weighted(t.app(), catalog.mixFor(t.app(), t.mix(), t.readWriteRatio()));
            String source = "probe".equals(t.role()) ? "probe" : "target";
            Generator g = t.mode() == Mode.RPS
                    ? new Generator.Rps(t.app(), source, w, ctx, sender, stats, this::phase, t.timeoutMs(), t.rampUpSec(),
                    t.rps(), t.maxInFlight(), executor)
                    : new Generator.Concurrency(t.app(), source, w, ctx, sender, stats, this::phase, t.timeoutMs(),
                    t.rampUpSec(), t.concurrency());
            (t.activePhases() == ActivePhases.ALL ? probes : stress).add(g);
        }
    }

    private List<Map.Entry<OpDef, Integer>> weighted(String app, Map<String, Integer> mix) {
        List<Map.Entry<OpDef, Integer>> l = new ArrayList<>();
        mix.forEach((op, w) -> {
            if (w != null && w > 0) {
                l.add(new AbstractMap.SimpleEntry<>(catalog.op(app, op), w));
            }
        });
        return l;
    }

    private void runClock() {
        try {
            // Platform thread'de: ExperimentService.start synchronized; orada bloklayan HTTP çağrısı sanal thread'i
            // taşıyıcıya sabitler ve 1 CPU'lu container'da HttpClient yanıtı işlenemez (tüm istekler kilitlenir).
            rollHotFlights();
            pools.refresh();
            log.info("deney {} başladı; veri havuzları: {}", id, pools.summary());
            event("start", planSummary());
            event("data", "Veri havuzları yenilendi: " + pools.summary());
            Instant t0 = Instant.now();
            Instant warmEnd = t0.plusSeconds(spec.warmupSec());
            Instant baseEnd = warmEnd.plusSeconds(spec.baselineSec());
            Instant loadEnd = baseEnd.plusSeconds(spec.loadSec());
            Instant end = loadEnd.plusSeconds(spec.recoverySec());
            enter(Phase.WARMUP, t0);
            background.forEach(Generator::start);
            if (!background.isEmpty()) {
                event("load", "Arka plan trafiği başladı: " + background.size() + " uygulama × "
                        + spec.background().rpsPerApp() + " RPS (tüm fazlarda)");
            }
            probes.forEach(Generator::start);
            if (!probes.isEmpty()) {
                event("load", "Tüm fazlarda aktif hedefler başladı: " + describe(probes));
            }
            long lastSample = 0;
            long lastTimeline = 0;
            prevSampleMs = System.currentTimeMillis();
            long lastPersist = System.currentTimeMillis();
            long lastRefresh = System.currentTimeMillis();
            while (!stopRequested) {
                Instant now = Instant.now();
                Phase want = now.isBefore(warmEnd) ? Phase.WARMUP : now.isBefore(baseEnd) ? Phase.BASELINE
                        : now.isBefore(loadEnd) ? Phase.LOAD : now.isBefore(end) ? Phase.RECOVERY : Phase.DONE;
                if (want != phase) {
                    Phase old = phase;
                    enter(want, now);
                    if (want == Phase.LOAD) {
                        stress.forEach(Generator::start);
                        if (!stress.isEmpty()) {
                            event("load", "Yük başladı: " + describe(stress));
                        }
                    }
                    if (old == Phase.LOAD) {
                        stress.forEach(Generator::stop);
                        if (!stress.isEmpty()) {
                            event("load", "Yük durduruldu (yeni istek üretilmiyor; uçuştakiler timeout ile sınırlı)");
                        }
                    }
                    if (want == Phase.DONE) {
                        break;
                    }
                }
                long ms = System.currentTimeMillis();
                if (ms - lastSample >= 1000) {
                    sampleProducer();
                    lastSample = ms;
                }
                if (ms - lastTimeline >= TIMELINE_STEP_MS) {
                    sampleTimeline(ms);
                    lastTimeline = ms;
                }
                if (ms - lastRefresh >= 20_000) {
                    executor.execute(pools::refresh);
                    lastRefresh = ms;
                }
                if (ms - lastPersist >= 10_000) {
                    persister.accept(record());
                    lastPersist = ms;
                }
                Thread.sleep(100);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (RuntimeException e) {
            log.error("deney {} saat hatası", id, e);
            stopReason = "hata: " + e;
            state = State.FAILED;
        } finally {
            finish();
        }
    }

    private void enter(Phase p, Instant at) {
        if (phaseStart.containsKey(phase) && !phaseEnd.containsKey(phase)) {
            phaseEnd.put(phase, at);
        }
        phase = p;
        if (p != Phase.DONE) {
            phaseStart.put(p, at);
        }
        log.info("deney {} faz={} utc={}", id, p, at);
        if (p == Phase.DONE) {
            event("phase", "Tüm fazlar tamamlandı");
        } else {
            event("phase", p.name() + " fazı başladı (planlanan " + plannedSeconds(p) + " sn)");
        }
    }

    private void finish() {
        if (state == State.RUNNING) {
            state = State.STOPPING;
        }
        Instant at = Instant.now();
        if (phase != Phase.DONE) {
            phaseEnd.putIfAbsent(phase, at);
        }
        background.forEach(Generator::stop);
        stress.forEach(Generator::stop);
        probes.forEach(Generator::stop);
        event("drain", "Tüm üreticiler durduruldu; uçuştaki isteklerin bitmesi bekleniyor");
        // Uçuştaki istekler timeout ile sınırlı; en uzun timeout + 2 sn bekle
        int maxTimeout = spec.targets().stream().mapToInt(TargetSpec::timeoutMs).max().orElse(5000);
        executor.shutdown();
        try {
            if (!executor.awaitTermination(Math.max(maxTimeout, 5000) + 2000L, TimeUnit.MILLISECONDS)) {
                log.warn("deney {}: bazı istekler süresinde bitmedi", id);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        sampleTimeline(System.currentTimeMillis());
        flushErrorEvents(true);
        callCleanup();
        cleanup.forEach((app, res) -> event("cleanup", "Cleanup " + app + ": " + res));
        endedAt = Instant.now();
        if (state == State.STOPPING) {
            state = stopRequested ? State.STOPPED : State.COMPLETED;
        }
        event("end", "Deney bitti: " + state + (stopReason == null ? "" : " (" + stopReason + ")"));
        persister.accept(record());
        log.info("deney {} bitti: {} {}", id, state, stopReason == null ? "" : stopReason);
    }

    /** Sıcak uçuşların kalkışı geçmişte kalmasın (aksi halde kilit deneylerinin adayları tükenir). */
    private void rollHotFlights() {
        String base = baseUrls.get("flightops");
        if (labToken == null || labToken.isBlank() || base == null) {
            return;
        }
        try {
            var res = http.send(HttpRequest.newBuilder(URI.create(base + "/lab/flights/roll-hot"))
                    .timeout(Duration.ofSeconds(5)).header("X-Lab-Token", labToken)
                    .header("X-Experiment-Id", id).POST(HttpRequest.BodyPublishers.noBody()).build(),
                    HttpResponse.BodyHandlers.ofString());
            event("setup", "sıcak uçuş tarih kontrolü: " + res.statusCode() + " " + res.body());
        } catch (Exception e) {
            event("setup", "sıcak uçuş tarih kontrolü başarısız: " + e);
        }
    }

    /** Sunucu tarafı temizliği: her uygulamanın kendi lab oturumlarını iptal etmesi. */
    private void callCleanup() {
        if (labToken == null || labToken.isBlank()) {
            cleanup.put("*", "lab token yok; cleanup çağrılmadı");
            return;
        }
        for (String app : Catalog.APPS) {
            String base = baseUrls.get(app);
            try {
                var res = http.send(HttpRequest.newBuilder(URI.create(base + "/lab/cleanup"))
                        .timeout(Duration.ofSeconds(5)).header("X-Lab-Token", labToken)
                        .header("X-Experiment-Id", id).POST(HttpRequest.BodyPublishers.noBody()).build(),
                        HttpResponse.BodyHandlers.ofString());
                cleanup.put(app, res.statusCode() + " " + res.body());
            } catch (Exception e) {
                cleanup.put(app, "HATA " + e);
            }
        }
    }

    private void sampleProducer() {
        var os = (OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();
        ThreadMXBean th = ManagementFactory.getThreadMXBean();
        double cpu = Math.max(0, os.getProcessCpuLoad());
        long heap = ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed();
        synchronized (this) {
            cpuSum += cpu;
            cpuMax = Math.max(cpuMax, cpu);
            heapMax = Math.max(heapMax, heap);
            threadsMax = Math.max(threadsMax, th.getThreadCount());
            samples++;
        }
    }

    public void requestStop(String reason) {
        if (state == State.RUNNING) {
            stopReason = reason;
            stopRequested = true;
            state = State.STOPPING;
            event("stop", "Durdurma istendi: " + reason);
        }
    }

    public boolean isActive() {
        return state == State.RUNNING || state == State.STOPPING;
    }

    public void awaitEnd(long ms) throws InterruptedException {
        if (clock != null) {
            clock.join(ms);
        }
    }

    public Phase phase() {
        return phase;
    }

    public String id() {
        return id;
    }

    public ExperimentSpec spec() {
        return spec;
    }

    public String specHash() {
        return specHash;
    }

    public State state() {
        return state;
    }

    public Stats stats() {
        return stats;
    }

    /** Canlı panel: kaynak × uygulama, mevcut faz. */
    public List<Map<String, Object>> live() {
        List<Map<String, Object>> out = new ArrayList<>();
        Phase p = phase == Phase.DONE ? Phase.RECOVERY : phase;
        double secs = phaseSeconds().getOrDefault(p, 0.0);
        List<Generator> all = new ArrayList<>(background);
        all.addAll(probes);
        all.addAll(stress);
        for (Generator g : all) {
            long[] t = stats.totals(p, g.source(), g.app());
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("source", g.source());
            m.put("app", g.app());
            m.put("mode", g instanceof Generator.Rps r ? "RPS " + r.targetRps() : "CONC " + ((Generator.Concurrency) g).workers());
            m.put("active", g.running);
            m.put("inFlight", g.inFlight());
            m.put("intended", t[0]);
            m.put("sent", t[1]);
            m.put("completed", t[2]);
            m.put("errors", t[3]);
            m.put("dropped", t[4]);
            m.put("noData", t[5]);
            m.put("achievedRps", secs > 0 ? Math.round(t[2] / secs * 10) / 10.0 : 0);
            out.add(m);
        }
        return out;
    }

    Map<Phase, Double> phaseSeconds() {
        Map<Phase, Double> m = new EnumMap<>(Phase.class);
        Instant now = Instant.now();
        phaseStart.forEach((p, s) -> {
            Instant e = phaseEnd.getOrDefault(p, endedAt != null ? endedAt : now);
            m.put(p, Math.max(0.001, Duration.between(s, e).toMillis() / 1000.0));
        });
        return m;
    }

    public synchronized ExperimentRecord record() {
        Map<String, List<String>> phases = new LinkedHashMap<>();
        phaseStart.forEach((p, s) -> {
            Instant e = phaseEnd.get(p);
            phases.put(p.name(), List.of(s.toString(), e == null ? "" : e.toString()));
        });
        Map<String, Object> prod = new LinkedHashMap<>(producer);
        prod.put("samples", samples);
        prod.put("processCpuAvgPct", samples == 0 ? 0 : Math.round(cpuSum / samples * 1000) / 10.0);
        prod.put("processCpuMaxPct", Math.round(cpuMax * 1000) / 10.0);
        prod.put("heapMaxMb", heapMax / (1024 * 1024));
        prod.put("threadsMax", threadsMax);
        prod.put("availableProcessors", Runtime.getRuntime().availableProcessors());
        Map<String, String> notes = new LinkedHashMap<>();
        notes.put("dataPools", pools.summary());
        notes.put("causality", "scenario etiketi değerlendirme içindir; nedensellik kanıtı değildir");
        return new ExperimentRecord(id, spec.clientRequestId(), specHash, state.name(), createdAt.toString(),
                startedAt == null ? null : startedAt.toString(), endedAt == null ? null : endedAt.toString(),
                phase.name(), stopReason, spec, phases, stats.rows(phaseSeconds()), prod, new LinkedHashMap<>(cleanup),
                notes, new ArrayList<>(events), new ArrayList<>(timeline));
    }

    // ---------------------------------------------------------------- izleme: olay günlüğü + zaman serisi
    private synchronized void event(String kind, String message) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("t", Instant.now().toString());
        e.put("phase", phase.name());
        e.put("kind", kind);
        e.put("message", message);
        events.add(e);
        if (events.size() > MAX_EVENTS) {
            events.remove(0);
        }
    }

    private long plannedSeconds(Phase p) {
        return switch (p) {
            case WARMUP -> spec.warmupSec();
            case BASELINE -> spec.baselineSec();
            case LOAD -> spec.loadSec();
            case RECOVERY -> spec.recoverySec();
            default -> 0;
        };
    }

    private String planSummary() {
        StringBuilder b = new StringBuilder("Deney başladı. Plan: warm-up ").append(spec.warmupSec())
                .append(" sn, baseline ").append(spec.baselineSec()).append(" sn, yük ").append(spec.loadSec())
                .append(" sn, recovery ").append(spec.recoverySec()).append(" sn");
        if (spec.targets().isEmpty()) {
            b.append("; hedef yok (yalnız arka plan)");
        } else {
            List<String> ts = new ArrayList<>();
            for (TargetSpec t : spec.targets()) {
                ts.add(t.app() + " (" + t.role() + ", " + (t.mode() == Mode.RPS ? t.rps() + " RPS" : t.concurrency()
                        + " eşzamanlı") + (t.holdMs() > 0 ? ", hold " + t.holdMs() + " ms" : "")
                        + (t.hotRow() ? ", hot-row" : "") + ", " + t.activePhases() + ")");
            }
            b.append("; hedefler: ").append(String.join(", ", ts));
        }
        return b.toString();
    }

    private static String describe(List<Generator> gs) {
        List<String> out = new ArrayList<>();
        for (Generator g : gs) {
            out.add(g.app() + " " + (g instanceof Generator.Rps r ? r.targetRps() + " RPS"
                    : ((Generator.Concurrency) g).workers() + " eşzamanlı"));
        }
        return String.join(", ", out);
    }

    /** 5 sn'de bir: kaynak/uygulama başına gerçekleşen RPS, hata, düşen istek, uçuştaki istek. */
    private void sampleTimeline(long nowMs) {
        double dt = Math.max(0.001, (nowMs - prevSampleMs) / 1000.0);
        prevSampleMs = nowMs;
        Map<String, Map<String, Object>> series = new LinkedHashMap<>();
        Map<String, Integer> inflight = new LinkedHashMap<>();
        List<Generator> all = new ArrayList<>(background);
        all.addAll(probes);
        all.addAll(stress);
        for (Generator g : all) {
            inflight.merge(g.source() + "/" + g.app(), g.inFlight(), Integer::sum);
        }
        for (String key : inflight.keySet()) {
            String[] sa = key.split("/", 2);
            long[] cur = new long[3];
            for (Phase p : Phase.values()) {
                long[] t = stats.totals(p, sa[0], sa[1]);
                cur[0] += t[2];
                cur[1] += t[3];
                cur[2] += t[4];
            }
            long[] prev = prevTotals.getOrDefault(key, new long[3]);
            long dc = cur[0] - prev[0];
            long de = cur[1] - prev[1];
            long dd = cur[2] - prev[2];
            prevTotals.put(key, cur);
            Map<String, Object> v = new LinkedHashMap<>();
            v.put("rps", Math.round(dc / dt * 10) / 10.0);
            v.put("errors", de);
            v.put("dropped", dd);
            v.put("inFlight", inflight.get(key));
            series.put(key, v);
            long[] pe = pendingErr.computeIfAbsent(key, k -> new long[3]);
            pe[0] += de;
            pe[1] += dd;
        }
        Map<String, Object> point = new LinkedHashMap<>();
        point.put("t", Instant.ofEpochMilli(nowMs).toString());
        point.put("phase", phase.name());
        point.put("series", series);
        synchronized (this) {
            timeline.add(point);
            if (timeline.size() > MAX_TIMELINE) {
                timeline.remove(0);
            }
        }
        flushErrorEvents(false);
    }

    /** Hata/düşen istekleri olay günlüğüne özetler (anahtar başına en fazla 30 sn'de bir). */
    private void flushErrorEvents(boolean force) {
        long now = System.currentTimeMillis();
        pendingErr.forEach((key, pe) -> {
            if ((pe[0] > 0 || pe[1] > 0) && (force || now - pe[2] >= ERROR_EVENT_EVERY_MS)) {
                List<String> parts = new ArrayList<>();
                if (pe[0] > 0) {
                    parts.add(pe[0] + " hata (5xx/timeout/IO)");
                }
                if (pe[1] > 0) {
                    parts.add(pe[1] + " düşürülen istek (üretici yetişemedi / max in-flight)");
                }
                event("errors", key + ": " + String.join(", ", parts));
                pe[0] = 0;
                pe[1] = 0;
                pe[2] = now;
            }
        });
    }
}
