package com.lab.air.chaos.v2.engine;

import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.ops.DataPools;
import com.lab.air.chaos.v2.V2Properties;
import com.lab.air.chaos.v2.analysis.ImpactAnalyzer;
import com.lab.air.chaos.v2.metrics.MetricsCollector;
import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.Durations;
import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.State;
import com.lab.air.chaos.v2.model.Model.Traffic;
import com.lab.air.chaos.v2.model.Model.Transition;
import com.lab.air.chaos.v2.model.Model.WorkloadSample;
import com.lab.air.chaos.v2.scenario.Metric;
import com.lab.air.chaos.v2.scenario.ScenarioDef;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Action;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Kind;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Plan;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Scope;
import com.lab.air.chaos.v2.store.Experiment;
import com.lab.air.chaos.v2.store.V2Store;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import tools.jackson.databind.json.JsonMapper;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Tek bir v2 deneyinin state machine'i:
 * CREATED → VALIDATING → BASELINING → RAMPING_UP → RUNNING → RECOVERING → COMPLETED
 * (+ FAILED / ABORTED / TIMED_OUT). Her durum geçişi zaman damgasıyla kaydedilir ve chaos.* MDC alanlarıyla
 * loglanır (Dynatrace korelasyonu). Her örnekte: workload örnekleri eklenir, adaptive controller ve safety guard
 * çalışır. Durdurma / abort / timeout her zaman cleanup + recovery doğrulamasından geçer (emergency hariç).
 */
public final class ExperimentRunner {

    private static final Logger log = LoggerFactory.getLogger(ExperimentRunner.class);

    private final Experiment exp;
    private final ScenarioDef def;
    private final Plan plan;
    private final Set<String> involvedApps;
    private final List<String> expectedAffected;
    private final MetricsCollector metrics;
    private final ChaosHttp http;
    private final V2Store store;
    private final V2Properties props;
    private final SafetyGuard safety;
    private final Durations dur;
    private final Traffic traffic;
    private final DataPools pools;
    private final ChaosHttp.Correlation corr;
    private final List<Actuators.Actuator> actuators = new ArrayList<>();
    private final Set<Actuators.Actuator> started = new HashSet<>();
    private final AdaptiveController controller;
    private final Consumer<Sample> listener = this::onSample;
    private final CountDownLatch done = new CountDownLatch(1);
    private final Object wake = new Object();
    private final long t0 = System.nanoTime();
    private final ScheduledExecutorService poolRefresh = Executors.newSingleThreadScheduledExecutor(
            r -> Thread.ofPlatform().daemon().name("v2-pools").unstarted(r));

    private volatile State state = State.CREATED;
    private volatile String stopReason;
    private volatile State stopAs;              // ABORTED / TIMED_OUT
    private volatile boolean emergency;
    private volatile Double controlStart;
    private volatile long rampStartMs = -1;
    private int recoveredStreak;
    private Long recoveredAtMs;
    private long recoveryStartMs;
    private Map<String, Double> baselineP95 = Map.of();
    private Map<String, Double> baselineErr = Map.of();
    private Double baselineDbCpu;
    private Integer baselineDbConns;
    private Integer baselineDbActive;
    private List<Map<String, Object>> recoveryChecks = List.of();

    public ExperimentRunner(Experiment exp, ScenarioDef def, Plan plan, Set<String> involvedApps,
                            List<String> expectedAffected, MetricsCollector metrics, ChaosHttp http, V2Store store,
                            V2Properties props, Catalog catalog, JsonMapper json) {
        this.exp = exp;
        this.def = def;
        this.plan = plan;
        this.involvedApps = involvedApps;
        this.expectedAffected = expectedAffected;
        this.metrics = metrics;
        this.http = http;
        this.store = store;
        this.props = props;
        this.dur = exp.request.durations();
        this.traffic = exp.request.traffic();
        var db = metrics.latest() == null ? null : metrics.latest().db();
        this.safety = new SafetyGuard(props, exp.request.custom() == null ? null : exp.request.custom().budget(),
                involvedApps, plan.abortExtra(), db == null ? null : db.dbSizeMb());
        this.corr = new ChaosHttp.Correlation(exp.id, exp.request.scenarioId(), exp.initiator,
                String.join(",", exp.targets), exp.intensity, () -> state.name());
        this.pools = new DataPools(http.apps(), http.client(), json);
        for (Action a : plan.actions()) {
            actuators.add(switch (a.kind()) {
                case LOOP -> new Actuators.Loop(a, http, corr, pools, this::nowMs);
                case NETEM -> new Actuators.Netem(a, http, corr, this::nowMs);
                case TRAFFIC -> new Actuators.Traffic(a, http, corr, pools, catalog, json, this::nowMs,
                        traffic.readWriteRatio(), traffic.thinkTimeMs(), traffic.retryBackoffMs());
            });
        }
        // deneye özgü arka plan trafiği (her uygulamada ölçülebilir p95 için)
        if (traffic.enabled()) {
            List<String> tApps = traffic.apps() == null || traffic.apps().isEmpty() ? props.allowedApps() : traffic.apps();
            for (String app : tApps) {
                if (!props.allowedApps().contains(app)) {
                    continue;
                }
                Action a = Action.traffic("background-" + app, app, traffic.rpsPerApp(), traffic.users(),
                        traffic.timeoutMs(), traffic.retries(), traffic.hotRows(), false);
                actuators.add(new Actuators.Traffic(a, http, corr, pools, catalog, json, this::nowMs,
                        traffic.readWriteRatio(), traffic.thinkTimeMs(), traffic.retryBackoffMs()));
            }
        }
        this.controller = plan.control() == null ? null
                : new AdaptiveController(plan.control().target(), plan.control().tolerancePct(), 0.15);
    }

    public String id() {
        return exp.id;
    }

    public Experiment experiment() {
        return exp;
    }

    public State state() {
        return state;
    }

    public long nowMs() {
        return (System.nanoTime() - t0) / 1_000_000;
    }

    public boolean await(long ms) throws InterruptedException {
        return done.await(ms, TimeUnit.MILLISECONDS);
    }

    // ------------------------------------------------------------------ dış kontrol

    public void requestStop(String reason, State as) {
        if (stopReason == null) {
            stopReason = reason;
            stopAs = as;
            exp.log("WARN", "durdurma istendi: " + reason);
        }
        synchronized (wake) {
            wake.notifyAll();
        }
    }

    /** Recovery beklemeden: workload durur, cleanup çalışır, ABORTED. */
    public void emergencyStop(String reason) {
        emergency = true;
        requestStop(reason, State.ABORTED);
    }

    // ------------------------------------------------------------------ ana akış

    public void run() {
        Thread.currentThread().setName("v2-runner-" + exp.id);
        metrics.addListener(listener);
        State terminal = State.COMPLETED;
        try {
            exp.startedAt = Instant.now().toString();
            transition(State.VALIDATING, "ön kontroller");
            String bad = preflight();
            if (bad != null) {
                stopReason = bad;
                transition(State.FAILED, bad);
                return;
            }
            poolRefresh.scheduleWithFixedDelay(() -> {
                try {
                    pools.refresh();
                } catch (RuntimeException e) {
                    log.debug("pool refresh: {}", e.toString());
                }
            }, 0, 30, TimeUnit.SECONDS);
            long hardDeadline = nowMs() + 1000L * maxDurationSec();

            transition(State.BASELINING, "baseline " + dur.baselineSec() + " s (yalnız arka plan trafiği)");
            for (var a : actuators) {
                if (a.action().kind() == Kind.TRAFFIC && !a.action().controlled()) {
                    startActuator(a);
                }
            }
            if (!sleepPhase(dur.baselineSec(), hardDeadline)) {
                terminal = endEarly();
            } else {
                computeBaseline();
                transition(State.RAMPING_UP, "ramp " + dur.rampSec() + " s");
                rampStartMs = nowMs();
                controlStart = controlValue(metrics.latest());
                tickActuators(0);
                if (!sleepPhase(dur.rampSec(), hardDeadline)) {
                    terminal = endEarly();
                } else {
                    transition(State.RUNNING, "hedef yoğunlukta " + dur.durationSec() + " s");
                    if (!sleepPhase(dur.durationSec(), hardDeadline)) {
                        terminal = endEarly();
                    }
                }
            }
        } catch (RuntimeException e) {
            log.error("v2 deney hatası {}", exp.id, e);
            exp.log("ERROR", "beklenmeyen hata: " + e);
            stopReason = "beklenmeyen hata: " + e.getMessage();
            terminal = State.FAILED;
        } finally {
            if (!state.terminal()) {
                finish(terminal);
            }
            metrics.removeListener(listener);
            poolRefresh.shutdownNow();
            done.countDown();
        }
    }

    private State endEarly() {
        return stopAs == null ? State.ABORTED : stopAs;
    }

    private int maxDurationSec() {
        int planned = dur.plannedTotal() + 60;
        int req = dur.maxDurationSec() > 0 ? dur.maxDurationSec() : (int) (planned * 1.5);
        return Math.min(Math.max(req, planned), props.maxDurationSec());
    }

    /** Workload durdur → cleanup → recovery doğrula → terminal durum. */
    private void finish(State terminal) {
        stopWorkloads();
        cleanup();
        // recovery yalnız yük gerçekten uygulandıysa ve emergency stop değilse doğrulanır
        if (!emergency && state != State.VALIDATING && state != State.CREATED) {
            transition(State.RECOVERING, "cleanup tamam; recovery doğrulaması " + dur.recoverySec() + " s");
            recoveryStartMs = nowMs();
            waitRecovery();
        }
        for (var a : actuators) {
            if (a.action().kind() == Kind.TRAFFIC) {
                a.stop();
            }
        }
        exp.workloadSummary = actuators.stream().map(a -> a.summary(nowMs())).toList();
        if (terminal == State.COMPLETED && !exp.cleanupOk) {
            terminal = State.FAILED;
            stopReason = "cleanup başarısız: " + exp.cleanupErrors;
        }
        analyze(terminal);
        exp.endedAt = Instant.now().toString();
        transition(terminal, stopReason == null ? "plan tamamlandı" : stopReason);
    }

    private String preflight() {
        if (!props.isLab()) {
            return "ortam 'lab' değil (CHAOS_V2_ENVIRONMENT) — production koruması";
        }
        Sample s = metrics.latest();
        if (s == null) {
            return "henüz ölçüm yok";
        }
        if (s.db() == null || !s.db().up()) {
            return "PostgreSQL ölçülemiyor (lab_monitor): " + metrics.dbError() + " — sahte değerle çalışılmaz";
        }
        for (String app : involvedApps) {
            AppSample a = s.apps().get(app);
            if (a == null || !a.up()) {
                return app + " erişilemiyor (actuator)";
            }
        }
        if (s.appVm() != null && s.appVm().diskFreeGb() != null && s.appVm().diskFreeGb() < props.minAppVmDiskFreeGb()) {
            return "APP_VM boş disk yetersiz";
        }
        for (var a : actuators) {
            if (a.action().kind() == Kind.NETEM) {
                var r = http.netemStatus(a.action().app());
                if (!r.ok()) {
                    return "netem sidecar erişilemiyor: " + a.action().app() + " (" + r.status() + ")";
                }
            }
            if (a.action().kind() == Kind.LOOP && a.action().path().startsWith("/lab/chaos")) {
                var r = http.call(a.action().app(), "GET", "/lab/chaos/status", null, corr, "preflight", 3000, true);
                if (!r.ok()) {
                    return a.action().app() + " /lab/chaos uçları yok veya kapalı (" + r.status() + ")";
                }
            }
        }
        // önceki deneyden kalmış olabilecek kalıntıları temizle
        for (String app : involvedApps) {
            http.cleanup(app, corr);
        }
        return null;
    }

    private boolean sleepPhase(int sec, long hardDeadline) {
        long end = nowMs() + sec * 1000L;
        while (nowMs() < end) {
            if (stopReason != null) {
                return false;
            }
            if (nowMs() > hardDeadline) {
                stopReason = "maksimum süre aşıldı (" + maxDurationSec() + " s)";
                stopAs = State.TIMED_OUT;
                return false;
            }
            synchronized (wake) {
                try {
                    wake.wait(Math.min(500, Math.max(1, end - nowMs())));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    stopReason = "kesildi";
                    return false;
                }
            }
        }
        return stopReason == null;
    }

    // ------------------------------------------------------------------ örnek işleme

    private void onSample(Sample base) {
        State st = state;
        if (st == State.CREATED || st == State.VALIDATING || st.terminal()) {
            return;
        }
        Double cv = controlValue(base);
        double progress = rampProgress();
        if (st == State.RAMPING_UP || st == State.RUNNING) {
            tickActuators(progress);
            if (controller != null && st == State.RUNNING || controller != null && st == State.RAMPING_UP) {
                double lv = controller.update(cv, progress, controlStart == null ? 0 : controlStart);
                for (var a : actuators) {
                    if (a.action().controlled() && started.contains(a)) {
                        a.setLevel(lv);
                    }
                }
            }
        }
        Double ct = controller == null ? null : controller.lastTarget();
        List<WorkloadSample> wl = actuators.stream().filter(started::contains)
                .map(a -> a.sample(nowMs(), a.action().controlled() ? cv : null, a.action().controlled() ? ct : null))
                .toList();
        Map<String, Object> tr = new LinkedHashMap<>();
        if (controller != null) {
            tr.put("controlLevel", round(controller.level()));
            tr.put("controlSaturated", controller.saturated());
        }
        Sample s = new Sample(base.at(), base.tMs(), st, base.apps(), base.db(), base.appVm(), wl, tr);
        synchronized (exp) {
            exp.samples.add(s);
        }
        if (st == State.RAMPING_UP || st == State.RUNNING) {
            String v = safety.check(s);
            if (v != null && stopReason == null) {
                exp.log("ERROR", "SAFETY ABORT: " + v);
                requestStop("güvenlik sınırı: " + v, State.ABORTED);
            }
        }
        if (st == State.RECOVERING) {
            checkRecovery(s);
        }
        if (exp.samples.size() % 5 == 0) {
            store.save(exp);
        }
    }

    private double rampProgress() {
        if (rampStartMs < 0) {
            return 0;
        }
        return dur.rampSec() <= 0 ? 1 : Math.min(1, (nowMs() - rampStartMs) / (dur.rampSec() * 1000.0));
    }

    /** Ramp boyunca: offset'i gelen eylemleri başlat; kontrolsüz LOOP/NETEM level = progress. */
    private synchronized void tickActuators(double progress) {
        if (rampStartMs < 0) {
            return;
        }
        long sinceRamp = nowMs() - rampStartMs;
        for (var a : actuators) {
            Action ac = a.action();
            if (ac.kind() == Kind.TRAFFIC && !ac.controlled()) {
                continue;               // baseline'dan beri çalışıyor
            }
            if (!started.contains(a) && sinceRamp >= ac.startOffsetSec() * 1000L) {
                double lv = ac.controlled() ? (controller == null ? progress : controller.level()) : Math.max(progress, 0.1);
                a.setLevel(lv);
                startActuator(a);
                exp.log("INFO", "workload başladı: " + ac.part() + " (" + ac.kind() + " → " + ac.app() + ")");
            } else if (started.contains(a) && !ac.controlled()) {
                a.setLevel(Math.max(progress, 0.1));
            } else if (started.contains(a) && controller == null) {
                a.setLevel(Math.max(progress, 0.1));
            }
        }
    }

    private void startActuator(Actuators.Actuator a) {
        a.start();
        started.add(a);
    }

    private Double controlValue(Sample s) {
        if (plan.control() == null || s == null) {
            return null;
        }
        Metric m = plan.control().metric();
        return plan.control().scope() == Scope.INITIATOR ? m.value(s, exp.initiator) : m.value(s, null);
    }

    // ------------------------------------------------------------------ cleanup / recovery

    private void stopWorkloads() {
        for (var a : actuators) {
            if (a.action().kind() != Kind.TRAFFIC) {
                try {
                    a.stop();
                } catch (RuntimeException e) {
                    exp.cleanupErrors.add(a.action().part() + ": " + e.getMessage());
                }
            }
        }
        if (emergency) {
            actuators.forEach(Actuators.Actuator::stop);
        }
    }

    private void cleanup() {
        boolean ok = true;
        Set<String> netemApps = new LinkedHashSet<>();
        for (var a : actuators) {
            if (a.action().kind() == Kind.NETEM) {
                netemApps.add(a.action().app());
            }
        }
        for (String app : netemApps) {
            var r = http.netemClear(app, corr);
            if (!r.ok()) {
                ok = false;
                exp.cleanupErrors.add("netem " + app + ": " + r.status() + " " + r.error() + " (TTL ile kalkar)");
            }
        }
        for (String app : involvedApps) {
            var r = http.cleanup(app, corr);
            if (!r.ok()) {
                ok = false;
                exp.cleanupErrors.add("cleanup " + app + ": " + r.status() + " " + r.error());
            } else {
                exp.log("INFO", "cleanup " + app + ": " + abbreviate(r.body()));
            }
        }
        exp.cleanupOk = ok;
    }

    private void computeBaseline() {
        List<Sample> b;
        synchronized (exp) {
            b = exp.samples.stream().filter(s -> s.phase() == State.BASELINING).toList();
        }
        Map<String, Double> p95 = new LinkedHashMap<>();
        Map<String, Double> err = new LinkedHashMap<>();
        for (String app : props.allowedApps()) {
            p95.put(app, avg(b.stream().map(s -> Metric.APP_P95_MS.value(s, app)).toList()));
            err.put(app, avg(b.stream().map(s -> Metric.APP_ERROR_PCT.value(s, app)).toList()));
        }
        baselineP95 = p95;
        baselineErr = err;
        baselineDbCpu = avg(b.stream().map(s -> Metric.DB_CPU_PCT.value(s, null)).toList());
        Double c = avg(b.stream().map(s -> s.db() == null || s.db().connections() == null ? null
                : s.db().connections().doubleValue()).toList());
        baselineDbConns = c == null ? null : (int) Math.round(c);
        Double act = avg(b.stream().map(s -> s.db() == null || s.db().active() == null ? null
                : s.db().active().doubleValue()).toList());
        baselineDbActive = act == null ? null : (int) Math.ceil(act);
        exp.log("INFO", "baseline: p95=" + p95 + " dbCpu=" + round(baselineDbCpu) + " dbConns=" + baselineDbConns);
    }

    /** Recovery: 3 ardışık örnekte tüm kontroller geçerse sistem toparlanmış sayılır. */
    private void checkRecovery(Sample s) {
        List<Map<String, Object>> checks = new ArrayList<>();
        boolean ok = true;
        for (String app : props.allowedApps()) {
            AppSample a = s.apps().get(app);
            boolean up = a != null && a.up();
            ok &= add(checks, app + " erişilebilir", up, up);
            if (!up) {
                continue;
            }
            Double bp = baselineP95.get(app);
            if (bp != null && a.p95Ms() != null) {
                double lim = Math.max(bp * 1.5, bp + 50);
                ok &= add(checks, app + " p95 ≤ " + round(lim), round(a.p95Ms()), a.p95Ms() <= lim);
            }
            Double be = baselineErr.get(app);
            if (a.errorPct() != null) {
                double lim = (be == null ? 0 : be) + 2;
                ok &= add(checks, app + " error % ≤ " + round(lim), round(a.errorPct()), a.errorPct() <= lim);
            }
            if (a.poolPending() != null) {
                ok &= add(checks, app + " pool pending = 0", a.poolPending(), a.poolPending() == 0);
            }
        }
        var db = s.db();
        if (db != null && db.up()) {
            ok &= add(checks, "lock waiters = 0", db.lockWaiters(), db.lockWaiters() != null && db.lockWaiters() == 0);
            // toplam bağlantı değil active session: yük sonrası Hikari idle bağlantıları idle-timeout'a kadar
            // açık kalır (normal pool davranışı, bozulma değil)
            if (baselineDbActive != null && db.active() != null) {
                ok &= add(checks, "DB active sessions ≤ baseline + 3", db.active(), db.active() <= baselineDbActive + 3);
            }
            if (baselineDbCpu != null && db.cpuPct() != null) {
                double lim = Math.max(baselineDbCpu * 1.5, baselineDbCpu + 15);
                ok &= add(checks, "DB CPU ≤ " + round(lim), round(db.cpuPct()), db.cpuPct() <= lim);
            }
            if (db.longestXactSec() != null) {
                ok &= add(checks, "longest xact < 30 s", round(db.longestXactSec()), db.longestXactSec() < 30);
            }
        } else {
            ok = false;
        }
        recoveryChecks = checks;
        recoveredStreak = ok ? recoveredStreak + 1 : 0;
        if (recoveredStreak >= 3 && recoveredAtMs == null) {
            recoveredAtMs = nowMs();
            exp.log("INFO", "recovery doğrulandı: " + round((recoveredAtMs - recoveryStartMs) / 1000.0) + " s");
            synchronized (wake) {
                wake.notifyAll();
            }
        }
    }

    private static boolean add(List<Map<String, Object>> l, String name, Object value, boolean pass) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("check", name);
        m.put("value", value);
        m.put("pass", pass);
        l.add(m);
        return pass;
    }

    private void waitRecovery() {
        long end = nowMs() + dur.recoverySec() * 1000L;
        while (nowMs() < end && !emergency) {
            if (recoveredAtMs != null && nowMs() - recoveredAtMs > 10_000) {
                break;                  // toparlandı + 10 s gözlem yeterli
            }
            synchronized (wake) {
                try {
                    wake.wait(500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        }
    }

    private void analyze(State terminal) {
        Map<String, Object> rec = new LinkedHashMap<>();
        rec.put("recovered", recoveredAtMs != null);
        rec.put("recoveryTimeSec", recoveredAtMs == null ? null : round((recoveredAtMs - recoveryStartMs) / 1000.0));
        rec.put("windowSec", dur.recoverySec());
        rec.put("lastChecks", recoveryChecks);
        Map<String, Object> ctl = new LinkedHashMap<>();
        if (controller != null) {
            ctl.put("metric", plan.control().metric().name());
            ctl.put("scope", plan.control().scope());
            ctl.put("target", plan.control().target());
            ctl.put("finalLevel", round(controller.level()));
            ctl.put("saturated", controller.saturated());
            ctl.put("note", controller.saturated()
                    ? "hedef, worker üst sınırı içinde ulaşılamadı (güvenlik limitleri) — dürüst sonuç" : "");
        }
        List<Sample> samples;
        synchronized (exp) {
            samples = List.copyOf(exp.samples);
        }
        Map<String, Object> report = new ImpactAnalyzer().analyze(new ImpactAnalyzer.Input(exp.initiator, exp.targets,
                expectedAffected, props.allowedApps(), plan.criteria(), samples, rec, ctl, terminal));
        report.put("scenario", Map.of("id", exp.request.scenarioId(), "name", def == null ? "" : def.name()));
        report.put("stopReason", stopReason == null ? "" : stopReason);
        exp.report = report;
        exp.report.put("markdown", ReportWriter.markdown(exp));
    }

    private void transition(State next, String reason) {
        State prev = state;
        state = next;
        synchronized (exp) {
            exp.state = next;
            exp.stateReason = reason;
            exp.transitions.add(new Transition(next, Instant.now().toString(), nowMs(), reason));
        }
        try (var c1 = MDC.putCloseable("chaos.experiment.id", exp.id);
             var c2 = MDC.putCloseable("chaos.scenario", exp.request.scenarioId());
             var c3 = MDC.putCloseable("chaos.initiator", exp.initiator);
             var c4 = MDC.putCloseable("chaos.target", String.join(",", exp.targets));
             var c5 = MDC.putCloseable("chaos.intensity", exp.intensity);
             var c6 = MDC.putCloseable("chaos.phase", next.name())) {
            log.info("chaos v2 {} {} → {}: {}", exp.id, prev, next, reason);
        }
        exp.log(next.terminal() && next != State.COMPLETED ? "WARN" : "INFO", prev + " → " + next + ": " + reason);
        store.save(exp);
    }

    private static Double avg(List<Double> l) {
        var v = l.stream().filter(java.util.Objects::nonNull).mapToDouble(Double::doubleValue).toArray();
        return v.length == 0 ? null : java.util.Arrays.stream(v).average().orElse(Double.NaN);
    }

    private static Double round(Double d) {
        return d == null || d.isNaN() ? null : Math.round(d * 10) / 10.0;
    }

    private static String abbreviate(String s) {
        return s == null ? "" : s.length() > 200 ? s.substring(0, 200) + "…" : s;
    }
}
