package com.lab.air.chaos.v2.engine;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.RunGate;
import com.lab.air.chaos.engine.ExperimentService;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.v2.V2Properties;
import com.lab.air.chaos.v2.metrics.MetricsCollector;
import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.Custom;
import com.lab.air.chaos.v2.model.Model.Durations;
import com.lab.air.chaos.v2.model.Model.Intensity;
import com.lab.air.chaos.v2.model.Model.RandomPlan;
import com.lab.air.chaos.v2.model.Model.Request;
import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.State;
import com.lab.air.chaos.v2.model.Model.TargetMode;
import com.lab.air.chaos.v2.model.Model.Traffic;
import com.lab.air.chaos.v2.model.Model.Transition;
import com.lab.air.chaos.v2.scenario.ScenarioDef;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Calibration;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Ctx;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Plan;
import com.lab.air.chaos.v2.scenario.ScenarioRegistry;
import com.lab.air.chaos.v2.store.Experiment;
import com.lab.air.chaos.v2.store.V2Store;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Deney kuyruğu ve yaşam döngüsü. Aynı anda yalnız BİR deney yük üretir (klasik engine dahil, {@link RunGate}).
 * Backend tek doğruluk kaynağıdır; UI yalnız bu sınıfın durumunu okur.
 */
@Component
public class Orchestrator {

    private static final Logger log = LoggerFactory.getLogger(Orchestrator.class);
    private static final DateTimeFormatter ID_TS = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss");

    public record Submitted(Experiment experiment, List<String> errors, int status) {
    }

    public record Resolved(ScenarioDef def, Ctx ctx, Plan plan, List<String> participants, List<String> expectedAffected,
                           long seed) {
    }

    private final ScenarioRegistry registry = new ScenarioRegistry();
    private final MetricsCollector metrics;
    private final ChaosHttp http;
    private final V2Store store;
    private final V2Properties props;
    private final ExperimentService legacy;
    private final RunGate gate;
    private final Catalog catalog = new Catalog();
    private final JsonMapper json = JsonMapper.builder().build();
    private final LinkedBlockingDeque<Experiment> queue = new LinkedBlockingDeque<>();
    private volatile ExperimentRunner active;
    private volatile boolean shutdown;
    private Thread dispatcher;

    public Orchestrator(MetricsCollector metrics, ChaosProperties chaosProps, V2Properties props, V2Store store,
                        ExperimentService legacy, RunGate gate) {
        this.metrics = metrics;
        this.http = new ChaosHttp(chaosProps, props);
        this.store = store;
        this.props = props;
        this.legacy = legacy;
        this.gate = gate;
    }

    public ScenarioRegistry registry() {
        return registry;
    }

    public ChaosHttp http() {
        return http;
    }

    @PostConstruct
    void init() {
        gate.registerV2(() -> {
            ExperimentRunner a = active;
            if (a != null && !a.state().terminal()) {
                return a.id() + " " + a.state();
            }
            return queue.isEmpty() ? null : queue.size() + " deney kuyrukta";
        });
        recoverAfterCrash();
        dispatcher = Thread.ofPlatform().daemon().name("v2-dispatcher").start(this::dispatch);
    }

    @PreDestroy
    void close() {
        shutdown = true;
        ExperimentRunner a = active;
        if (a != null) {
            a.emergencyStop("controller kapanıyor");
            try {
                a.await(30_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        if (dispatcher != null) {
            dispatcher.interrupt();
        }
    }

    /** Önceki çalışmada yarım kalan deneyler ABORTED işaretlenir ve tüm uygulamalarda cleanup çalışır. */
    void recoverAfterCrash() {
        List<String> ids = store.nonTerminalIds();
        if (ids.isEmpty()) {
            return;
        }
        for (String id : ids) {
            store.load(id).ifPresent(e -> {
                e.state = State.ABORTED;
                e.stateReason = "chaos controller yeniden başladı; deney yarıda kaldı (crash recovery)";
                e.transitions.add(new Transition(State.ABORTED, Instant.now().toString(), 0, e.stateReason));
                e.endedAt = Instant.now().toString();
                e.log("WARN", e.stateReason);
                store.save(e);
            });
        }
        cleanupEverything(ChaosHttp.Correlation.none(), "crash-recovery");
        log.warn("crash recovery: {} deney ABORTED işaretlendi, cleanup çalıştı", ids.size());
    }

    // ------------------------------------------------------------------ çözümleme / doğrulama

    public Calibration calibration() {
        Sample s = metrics.latest();
        Map<String, Integer> pools = new LinkedHashMap<>();
        Map<String, Double> heap = new LinkedHashMap<>();
        for (String app : props.allowedApps()) {
            AppSample a = s == null ? null : s.apps().get(app);
            pools.put(app, a != null && a.poolMax() != null ? a.poolMax() : 8);
            if (a != null && a.heapMaxMb() != null) {
                heap.put(app, a.heapMaxMb());
            }
        }
        int maxConn = s != null && s.db() != null && s.db().maxConnections() != null ? s.db().maxConnections() : 100;
        Integer cpus = s != null && s.db() != null ? s.db().cpus() : null;
        return new Calibration(props.allowedApps(), pools, maxConn, 40, 32, cpus, heap, 5000);
    }

    public List<String> validate(Request r) {
        List<String> e = new ArrayList<>();
        if (!props.isLab()) {
            e.add("ortam 'lab' değil: chaos v2 yalnız lab ortamında çalışır");
        }
        if (r == null) {
            e.add("istek gövdesi yok");
            return e;
        }
        if (Request.COMPOSITE.equals(r.scenarioId())) {
            if (r.subScenarios() == null || r.subScenarios().size() < 2) {
                e.add("COMPOSITE için en az 2 subScenarios gerekli");
            } else {
                r.subScenarios().stream().filter(id -> registry.get(id).isEmpty() || "S20".equals(id))
                        .forEach(id -> e.add("geçersiz alt senaryo: " + id));
            }
        } else if (r.scenarioId() == null || registry.get(r.scenarioId()).isEmpty()) {
            e.add("bilinmeyen senaryo: " + r.scenarioId());
        }
        if (r.initiator() != null && !props.allowedApps().contains(r.initiator())) {
            e.add("initiator allowlist'te değil: " + r.initiator());
        }
        if (r.targets() != null) {
            r.targets().stream().filter(t -> !props.allowedApps().contains(t)).forEach(t -> e.add("hedef allowlist'te değil: " + t));
        }
        if (r.targetMode() == TargetMode.MULTI && (r.targets() == null || r.targets().isEmpty())) {
            e.add("MULTI modda en az bir hedef seçilmeli");
        }
        Durations d = r.durations() == null ? Durations.defaults() : r.durations();
        range(e, "baselineSec", d.baselineSec(), 10, 600);
        range(e, "rampSec", d.rampSec(), 0, 600);
        range(e, "durationSec", d.durationSec(), 10, 3600);
        range(e, "recoverySec", d.recoverySec(), 15, 900);
        if (d.plannedTotal() > props.maxDurationSec()) {
            e.add("toplam süre " + d.plannedTotal() + " s > izin verilen " + props.maxDurationSec() + " s");
        }
        Traffic t = r.traffic() == null ? Traffic.defaults() : r.traffic();
        rangeD(e, "traffic.rpsPerApp", t.rpsPerApp(), 0, 50);
        range(e, "traffic.users", t.users(), 1, 40);
        range(e, "traffic.timeoutMs", t.timeoutMs(), 100, 30000);
        range(e, "traffic.retries", t.retries(), 0, 5);
        range(e, "traffic.thinkTimeMs", t.thinkTimeMs(), 0, 10000);
        rangeD(e, "traffic.readWriteRatio", t.readWriteRatio(), 0, 1);
        Custom c = r.custom();
        if (c != null) {
            if (c.workers() != null) {
                range(e, "custom.workers", c.workers(), 1, 40);
            }
            if (c.rps() != null) {
                rangeD(e, "custom.rps", c.rps(), 0.1, 100);
            }
            if (c.dbConnections() != null) {
                range(e, "custom.dbConnections", c.dbConnections(), 1, 30);
            }
            if (c.txDurationMs() != null) {
                range(e, "custom.txDurationMs", c.txDurationMs(), 0, 120000);
            }
            if (c.queryFrequency() != null) {
                rangeD(e, "custom.queryFrequency", c.queryFrequency(), 0, 50);
            }
            if (c.errorPct() != null) {
                range(e, "custom.errorPct", c.errorPct(), 0, 50);
            }
            if (c.networkLatencyMs() != null) {
                range(e, "custom.networkLatencyMs", c.networkLatencyMs(), 0, 2000);
            }
            if (c.packetLossPct() != null) {
                rangeD(e, "custom.packetLossPct", c.packetLossPct(), 0, 50);
            }
            if (c.maxAffectedServices() != null) {
                range(e, "custom.maxAffectedServices", c.maxAffectedServices(), 1, props.allowedApps().size());
            }
            if (c.targetValue() != null && c.targetValue() <= 0) {
                e.add("custom.targetValue > 0 olmalı");
            }
        }
        return e;
    }

    private static void range(List<String> e, String n, int v, int lo, int hi) {
        if (v < lo || v > hi) {
            e.add(n + " " + lo + ".." + hi + " aralığında olmalı (verilen " + v + ")");
        }
    }

    private static void rangeD(List<String> e, String n, double v, double lo, double hi) {
        if (v < lo || v > hi) {
            e.add(n + " " + lo + ".." + hi + " aralığında olmalı (verilen " + v + ")");
        }
    }

    /** İsteği somut plana çevirir (aynı seed → aynı initiator/hedef/plan). Deney başlatmaz. */
    public Resolved resolve(Request r) {
        long seed = r.seed() != null ? r.seed() : ThreadLocalRandom.current().nextLong(1, Long.MAX_VALUE);
        Random rnd = new Random(seed);
        List<String> apps = props.allowedApps();
        boolean composite = Request.COMPOSITE.equals(r.scenarioId());
        ScenarioDef def = composite ? null : registry.get(r.scenarioId()).orElseThrow();
        int minApps = composite ? 2 : def.minApps();
        String initiator = r.initiator() != null ? r.initiator() : apps.get(rnd.nextInt(apps.size()));
        TargetMode mode = r.targetMode() == null ? TargetMode.AUTO : r.targetMode();
        List<String> targets = switch (mode) {
            case SINGLE -> List.of(r.targets() != null && !r.targets().isEmpty() ? r.targets().getFirst() : initiator);
            case MULTI -> List.copyOf(r.targets());
            case ALL, SHARED_DB -> apps;
            case RANDOM -> {
                List<String> pool = new ArrayList<>(apps);
                java.util.Collections.shuffle(pool, rnd);
                yield List.copyOf(pool.subList(0, 1 + rnd.nextInt(pool.size())));
            }
            case AUTO -> composite || def.defaultAllTargets() ? apps : List.of(initiator);
        };
        Set<String> t = new LinkedHashSet<>(targets);
        // minApps: katılımcı (initiator + hedefler) sayısı yetmiyorsa seed'e bağlı ek hedef
        List<String> others = new ArrayList<>(apps.stream().filter(a -> !a.equals(initiator) && !t.contains(a)).toList());
        java.util.Collections.shuffle(others, rnd);
        while (participantsCount(initiator, t) < minApps && !others.isEmpty()) {
            t.add(others.removeFirst());
        }
        List<String> tl = new ArrayList<>(t);
        Integer cap = r.custom() == null ? null : r.custom().maxAffectedServices();
        if (cap != null && tl.size() > cap) {
            tl = new ArrayList<>(tl.subList(0, cap));
        }
        Intensity intensity = r.intensity() == null ? Intensity.MEDIUM : r.intensity();
        Ctx ctx = new Ctx(initiator, List.copyOf(tl), intensity, r.custom(), calibration(), rnd);
        Plan plan = composite ? registry.composite(ctx, r.subScenarios(), false) : def.plan().apply(ctx);
        List<String> participants = new ArrayList<>();
        participants.add(initiator);
        tl.stream().filter(x -> !participants.contains(x)).forEach(participants::add);
        plan.actions().stream().map(ScenarioDef.Action::app).filter(x -> !participants.contains(x)).forEach(participants::add);
        return new Resolved(def, ctx, plan, participants, expectedAffected(def, initiator, participants), seed);
    }

    private static int participantsCount(String initiator, Set<String> targets) {
        Set<String> s = new LinkedHashSet<>(targets);
        s.add(initiator);
        return s.size();
    }

    /** Senaryonun doğasına göre etkilenmesi BEKLENEN uygulamalar (gözlenenle raporda karşılaştırılır). */
    static List<String> expectedAffected(ScenarioDef def, String initiator, List<String> participants) {
        if (def == null) {
            return participants;
        }
        return switch (def.id()) {
            case "S01", "S11", "S12", "S13", "S14", "S15", "S19" -> List.of(initiator);
            case "S04", "S05", "S06", "S07" -> List.of(initiator);     // paylaşılan DB etkisi yoğunluğa bağlı: bilgi
            // contention yalnız chaos.* satırlarında: etki DB (lock) ve workload seviyesinde, iş endpoint'lerinde değil
            case "S02", "S08", "S09" -> List.of();
            default -> participants;
        };
    }

    public static Map<String, Object> describePlan(Resolved r) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("scenarioId", r.def() == null ? Request.COMPOSITE : r.def().id());
        m.put("initiator", r.ctx().initiator());
        m.put("targets", r.ctx().targets());
        m.put("participants", r.participants());
        m.put("expectedAffected", r.expectedAffected());
        m.put("intensity", r.ctx().intensity());
        m.put("seed", String.valueOf(r.seed()));
        m.put("actions", r.plan().actions());
        m.put("control", r.plan().control());
        m.put("criteria", r.plan().criteria().stream().map(c -> {
            Map<String, Object> x = new LinkedHashMap<>();
            x.put("description", c.description());
            x.put("metric", c.metric().name());
            x.put("scope", c.scope());
            x.put("agg", c.agg());
            x.put("expected", (c.gte() ? "≥ " : "≤ ") + (c.absolute() != null ? c.absolute()
                    : "baseline × " + c.factor() + (c.add() != 0 ? " + " + c.add() : "")));
            x.put("required", c.required());
            return x;
        }).toList());
        m.put("parameters", r.plan().parameters());
        m.put("calibration", r.ctx().cal());
        return m;
    }

    // ------------------------------------------------------------------ kuyruk

    public synchronized Submitted submit(Request req, String actor) {
        List<String> errors = validate(req);
        if (!errors.isEmpty()) {
            return new Submitted(null, errors, 400);
        }
        Request r = normalize(req);
        if (r.clientRequestId() != null) {
            for (var s : store.list()) {
                Optional<Experiment> e = store.load((String) s.get("id"));
                if (e.isPresent() && r.clientRequestId().equals(e.get().request.clientRequestId())) {
                    return new Submitted(e.get(), List.of(), 200);       // idempotent tekrar
                }
            }
        }
        if (queue.size() >= props.maxQueue()) {
            return new Submitted(null, List.of("kuyruk dolu (" + props.maxQueue() + ")"), 429);
        }
        Resolved res;
        try {
            res = resolve(r);
        } catch (RuntimeException ex) {
            return new Submitted(null, List.of("plan oluşturulamadı: " + ex.getMessage()), 400);
        }
        Experiment e = new Experiment();
        e.id = "v2-" + ZonedDateTime.now(ZoneOffset.UTC).format(ID_TS) + "-"
                + String.format("%04x", ThreadLocalRandom.current().nextInt(0x10000));
        e.createdAt = Instant.now().toString();
        e.request = new Request(r.scenarioId(), r.initiator(), r.targetMode(), r.targets(), r.intensity(), r.custom(),
                r.durations(), r.traffic(), res.seed(), r.label(), r.clientRequestId(), r.randomPlanId(),
                r.subScenarios(), r.cooldownSec());
        e.scenarioName = res.def() == null ? "Composite: " + r.subScenarios() : res.def().name();
        e.initiator = res.ctx().initiator();
        e.targets = res.ctx().targets();
        e.participants = res.participants();
        e.intensity = res.ctx().intensity().name();
        e.seed = res.seed();
        e.plan = describePlan(res);
        e.calibration = Map.of("calibration", res.ctx().cal());
        e.dynatrace = dynatrace(e.id);
        e.transitions.add(new Transition(State.CREATED, e.createdAt, 0, "kuyruğa alındı"));
        e.log("INFO", "oluşturuldu (" + actor + "): " + r.scenarioId() + " initiator=" + e.initiator + " targets="
                + e.targets + " intensity=" + e.intensity + " seed=" + e.seed);
        store.save(e);
        store.audit(actor, "submit", Map.of("id", e.id, "scenario", r.scenarioId(), "initiator", e.initiator,
                "targets", e.targets, "intensity", e.intensity));
        queue.addLast(e);
        return new Submitted(e, List.of(), 202);
    }

    private Request normalize(Request r) {
        return new Request(r.scenarioId(), r.initiator(), r.targetMode() == null ? TargetMode.AUTO : r.targetMode(),
                r.targets() == null ? List.of() : r.targets(), r.intensity() == null ? Intensity.MEDIUM : r.intensity(),
                r.custom() == null ? Custom.none() : r.custom(),
                r.durations() == null ? Durations.defaults() : r.durations(),
                r.traffic() == null ? Traffic.defaults() : r.traffic(), r.seed(), r.label(), r.clientRequestId(),
                r.randomPlanId(), r.subScenarios(), r.cooldownSec());
    }

    private void dispatch() {
        while (!shutdown) {
            try {
                Experiment e = queue.takeFirst();
                if (e.state.terminal()) {
                    continue;              // kuyruktayken iptal edildi
                }
                String legacyActive = legacy.activeRun().filter(x -> x.isActive()).map(x -> x.id()).orElse(null);
                if (legacyActive != null) {
                    abortQueued(e, "klasik engine'de aktif deney var: " + legacyActive);
                    continue;
                }
                Resolved res = resolve(e.request);
                ExperimentRunner runner = new ExperimentRunner(e, res.def(), res.plan(),
                        new LinkedHashSet<>(res.participants()), res.expectedAffected(), metrics, http, store, props,
                        catalog, json);
                active = runner;
                runner.run();             // bu thread'de, bitene kadar
                int cool = e.request.cooldownSec() == null ? 0 : Math.max(0, Math.min(600, e.request.cooldownSec()));
                if (cool > 0 && !queue.isEmpty()) {
                    log.info("cooldown {} s", cool);
                    Thread.sleep(cool * 1000L);
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                return;
            } catch (RuntimeException ex) {
                log.error("dispatcher hatası", ex);
            }
        }
    }

    private void abortQueued(Experiment e, String reason) {
        e.state = State.ABORTED;
        e.stateReason = reason;
        e.endedAt = Instant.now().toString();
        e.transitions.add(new Transition(State.ABORTED, e.endedAt, 0, reason));
        e.log("WARN", reason);
        store.save(e);
    }

    public Optional<ExperimentRunner> active() {
        ExperimentRunner a = active;
        return a == null || a.state().terminal() ? Optional.empty() : Optional.of(a);
    }

    public List<Map<String, Object>> queued() {
        return queue.stream().map(Experiment::summary).toList();
    }

    public Optional<Experiment> get(String id) {
        ExperimentRunner a = active;
        if (a != null && a.id().equals(id)) {
            return Optional.of(a.experiment());
        }
        for (Experiment e : queue) {
            if (e.id.equals(id)) {
                return Optional.of(e);
            }
        }
        return store.load(id);
    }

    public boolean stop(String id, String actor) {
        store.audit(actor, "stop", Map.of("id", id));
        ExperimentRunner a = active;
        if (a != null && a.id().equals(id) && !a.state().terminal()) {
            a.requestStop("kullanıcı durdurdu (" + actor + ")", State.ABORTED);
            return true;
        }
        for (Experiment e : queue) {
            if (e.id.equals(id)) {
                queue.remove(e);
                abortQueued(e, "kuyruktan çıkarıldı (" + actor + ")");
                return true;
            }
        }
        return false;
    }

    /** Acil durdurma: kuyruk boşalır, aktif v2 + klasik deney durur, TÜM uygulamalarda netem + cleanup. */
    public Map<String, Object> emergencyStop(String actor) {
        store.audit(actor, "emergency-stop", Map.of());
        Map<String, Object> out = new LinkedHashMap<>();
        List<String> dropped = new ArrayList<>();
        Experiment q;
        while ((q = queue.pollFirst()) != null) {
            abortQueued(q, "emergency stop (" + actor + ")");
            dropped.add(q.id);
        }
        out.put("dequeued", dropped);
        ExperimentRunner a = active;
        if (a != null && !a.state().terminal()) {
            a.emergencyStop("EMERGENCY STOP (" + actor + ")");
            out.put("activeStopped", a.id());
        }
        out.put("legacy", legacy.stopAll("emergency stop (v2)"));
        out.put("cleanup", cleanupEverything(new ChaosHttp.Correlation("emergency", "none", "all", "all", "none",
                () -> "EMERGENCY"), "emergency"));
        if (a != null) {
            try {
                a.await(20_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            out.put("activeState", a.state());
        }
        return out;
    }

    Map<String, Object> cleanupEverything(ChaosHttp.Correlation c, String why) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (String app : props.allowedApps()) {
            var n = http.netemClear(app, c);
            var r = http.cleanup(app, c);
            m.put(app, Map.of("netem", n.status(), "cleanup", r.status()));
        }
        log.warn("tüm uygulamalarda cleanup ({}): {}", why, m);
        return m;
    }

    public Submitted rerun(String id, String actor) {
        Optional<Experiment> e = get(id);
        if (e.isEmpty()) {
            return new Submitted(null, List.of("deney bulunamadı: " + id), 404);
        }
        Request r = e.get().request.withClientRequestId(null);
        store.audit(actor, "rerun", Map.of("of", id));
        return submit(new Request(r.scenarioId(), e.get().initiator, TargetMode.MULTI,
                e.get().targets, r.intensity(), r.custom(), r.durations(), r.traffic(), e.get().seed,
                "rerun of " + id, null, r.randomPlanId(), r.subScenarios(), r.cooldownSec()), actor);
    }

    /**
     * Rastgele mod: seed'e bağlı senaryo / initiator / yoğunluk dizisi. Sıralı modda her biri ayrı deney olarak
     * cooldown ile kuyruğa girer; paralel modda tek COMPOSITE deney olur. Tüm güvenlik kuralları aynıdır.
     */
    public Map<String, Object> randomPlan(RandomPlan p, String actor) {
        long seed = p.seed() != null ? p.seed() : ThreadLocalRandom.current().nextLong(1, Long.MAX_VALUE);
        Random rnd = new Random(seed);
        List<String> scen = registry.all().stream().map(ScenarioDef::id)
                .filter(id -> p.allowedScenarios() == null || p.allowedScenarios().isEmpty() || p.allowedScenarios().contains(id))
                .filter(id -> p.excludedScenarios() == null || !p.excludedScenarios().contains(id))
                .filter(id -> !p.parallel() || !"S20".equals(id))
                .toList();
        List<String> apps = props.allowedApps().stream()
                .filter(a -> p.allowedApps() == null || p.allowedApps().isEmpty() || p.allowedApps().contains(a))
                .filter(a -> p.excludedApps() == null || !p.excludedApps().contains(a)).toList();
        List<String> errors = new ArrayList<>();
        if (scen.isEmpty()) {
            errors.add("izinli senaryo yok");
        }
        if (apps.isEmpty()) {
            errors.add("izinli uygulama yok");
        }
        int count = Math.max(1, Math.min(p.count(), 10));
        if (p.parallel() && scen.size() < 2) {
            errors.add("paralel mod için en az 2 senaryo gerekli");
        }
        if (!errors.isEmpty()) {
            return Map.of("errors", errors);
        }
        Intensity max = p.maxIntensity() == null || p.maxIntensity() == Intensity.CUSTOM ? Intensity.MEDIUM : p.maxIntensity();
        String planId = "rp-" + Long.toHexString(seed);
        List<Object> created = new ArrayList<>();
        if (p.parallel()) {
            List<String> pool = new ArrayList<>(scen);
            java.util.Collections.shuffle(pool, rnd);
            List<String> subs = pool.subList(0, Math.min(Math.max(2, count), Math.min(3, pool.size())));
            Request r = new Request(Request.COMPOSITE, apps.get(rnd.nextInt(apps.size())), TargetMode.MULTI, apps,
                    Intensity.values()[rnd.nextInt(max.ordinal() + 1)], null, p.durations(), p.traffic(), rnd.nextLong(),
                    "random parallel " + planId, null, planId, List.copyOf(subs), 0);
            var s = submit(r, actor);
            created.add(s.experiment() == null ? Map.of("errors", s.errors()) : s.experiment().summary());
        } else {
            for (int i = 0; i < count; i++) {
                String sc = scen.get(rnd.nextInt(scen.size()));
                String init = apps.get(rnd.nextInt(apps.size()));
                ScenarioDef d = registry.get(sc).orElseThrow();
                List<String> tg = d.defaultAllTargets() ? apps : List.of(init);
                Request r = new Request(sc, init, TargetMode.MULTI, tg,
                        Intensity.values()[rnd.nextInt(max.ordinal() + 1)], null, p.durations(), p.traffic(),
                        rnd.nextLong(), "random " + planId + " #" + (i + 1), null, planId, null, p.cooldownSec());
                var res = submit(r, actor);
                created.add(res.experiment() == null ? Map.of("errors", res.errors()) : res.experiment().summary());
            }
        }
        store.audit(actor, "random-plan", Map.of("planId", planId, "seed", String.valueOf(seed), "count", created.size()));
        return Map.of("planId", planId, "seed", String.valueOf(seed), "experiments", created);
    }

    // ------------------------------------------------------------------ Dynatrace

    Map<String, Object> dynatrace(String id) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("experimentId", id);
        m.put("logsDql", "fetch logs | filter matchesValue(chaos.experiment.id, \"" + id + "\") or contains(content, \""
                + id + "\") | sort timestamp asc");
        m.put("phasesDql", "fetch logs | filter contains(content, \"chaos v2 " + id + "\") | fields timestamp, content");
        m.put("requestsDql", "fetch spans | filter matchesValue(request.headers.x-experiment-id, \"" + id + "\")");
        String base = props.dtUiUrl();
        if (base != null && !base.isBlank()) {
            String ui = base.replace(".live.dynatrace.com", ".apps.dynatrace.com").replaceAll("/+$", "");
            m.put("logsApp", ui + "/ui/apps/dynatrace.logs");
            m.put("notebooksApp", ui + "/ui/apps/dynatrace.notebooks");
            m.put("servicesApp", ui + "/ui/apps/dynatrace.services");
        }
        return m;
    }
}
