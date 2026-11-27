package com.lab.air.chaos.v2;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.RunGate;
import com.lab.air.chaos.engine.ExperimentService;
import com.lab.air.chaos.v2.analysis.ImpactAnalyzer;
import com.lab.air.chaos.v2.engine.AdaptiveController;
import com.lab.air.chaos.v2.engine.Orchestrator;
import com.lab.air.chaos.v2.engine.ReportWriter;
import com.lab.air.chaos.v2.engine.SafetyGuard;
import com.lab.air.chaos.v2.engine.WindowStats;
import com.lab.air.chaos.v2.metrics.MetricsCollector;
import com.lab.air.chaos.v2.metrics.ProcStats;
import com.lab.air.chaos.v2.metrics.PromText;
import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.Budget;
import com.lab.air.chaos.v2.model.Model.DbSample;
import com.lab.air.chaos.v2.model.Model.HostSample;
import com.lab.air.chaos.v2.model.Model.Intensity;
import com.lab.air.chaos.v2.model.Model.Request;
import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.State;
import com.lab.air.chaos.v2.model.Model.TargetMode;
import com.lab.air.chaos.v2.scenario.Metric;
import com.lab.air.chaos.v2.scenario.ScenarioDef;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Agg;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Calibration;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Criterion;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Ctx;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Scope;
import com.lab.air.chaos.v2.scenario.ScenarioRegistry;
import com.lab.air.chaos.v2.store.V2Store;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.TreeMap;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class V2UnitTest {

    static final List<String> APPS = List.of("reservation", "checkin", "flightops", "baggage");
    static final Calibration CAL = new Calibration(APPS, Map.of("reservation", 8, "checkin", 8, "flightops", 8,
            "baggage", 8), 100, 40, 32, 6, Map.of(), 5000);

    // ------------------------------------------------------------------ metrics

    @Test
    void prometheusHistogramQuantileMatchesPrometheusMethod() {
        String text = """
                # HELP x
                http_server_requests_seconds_bucket{uri="/api/a",status="200",le="0.01"} 50.0
                http_server_requests_seconds_bucket{uri="/api/a",status="200",le="0.1"} 90.0
                http_server_requests_seconds_bucket{uri="/api/a",status="200",le="1.0"} 100.0
                http_server_requests_seconds_bucket{uri="/api/a",status="200",le="+Inf"} 100.0
                http_server_requests_seconds_bucket{uri="/lab/hold",status="200",le="+Inf"} 999.0
                http_server_requests_seconds_count{uri="/api/a",status="200"} 100.0
                http_server_requests_seconds_count{uri="/api/a",status="503"} 5.0
                hikaricp_connections_active{pool="checkin-pool"} 7.0
                hikaricp_connections_active{pool="lab-pressure"} 3.0
                """;
        PromText p = PromText.parse(text);
        TreeMap<Double, Double> b = p.buckets("http_server_requests_seconds_bucket", l -> !l.label("uri").startsWith("/lab"));
        assertEquals(100.0, b.lastEntry().getValue());
        // p95: rank 95 → (0.1, 1.0] kovası, 5/10 → 0.1 + 0.9 × 0.5 = 0.55 s
        assertEquals(0.55, PromText.quantile(0.95, b), 1e-9);
        assertEquals(0.01, PromText.quantile(0.5, b), 1e-9);
        assertEquals(5.0, p.sum("http_server_requests_seconds_count", l -> l.label("status").startsWith("5")));
        assertEquals(7.0, p.first("hikaricp_connections_active", l -> "checkin-pool".equals(l.label("pool"))));
        assertNull(PromText.quantile(0.95, new TreeMap<>()));
    }

    @Test
    void appSampleFromPrometheusComputesWindowedRates() {
        var col = new MetricsCollector(new ChaosProperties(null, "t", "./data", null, null, 1), v2(null));
        String t1 = "process_cpu_usage 0.25\njvm_memory_used_bytes{area=\"heap\",id=\"a\"} 104857600\n"
                + "jvm_memory_max_bytes{area=\"heap\",id=\"a\"} 209715200\njvm_memory_max_bytes{area=\"heap\",id=\"b\"} -1\n"
                + "hikaricp_connections_max{pool=\"checkin-pool\"} 8\nhikaricp_connections_active{pool=\"checkin-pool\"} 4\n"
                + "hikaricp_connections_pending{pool=\"checkin-pool\"} 0\n"
                + "http_server_requests_seconds_count{uri=\"/api/x\",status=\"200\"} 10\n"
                + "http_server_requests_seconds_bucket{uri=\"/api/x\",status=\"200\",le=\"0.05\"} 10\n"
                + "http_server_requests_seconds_bucket{uri=\"/api/x\",status=\"200\",le=\"+Inf\"} 10\n";
        AppSample first = col.fromPrometheus("checkin", PromText.parse(t1), null);
        assertEquals(25.0, first.cpuPct(), 1e-9);
        assertEquals(50.0, first.heapPct(), 1e-9);
        assertEquals(50.0, first.poolUsagePct(), 1e-9);
        assertNull(first.p95Ms(), "tek okuma ile pencere hesaplanamaz — null, uydurma değil");
    }

    @Test
    void procStatsCpuAndMemory() {
        ProcStats p = new ProcStats();
        assertNull(p.cpuPct("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\ncpu1 1 1 1 1\n"));
        Double pct = p.cpuPct("cpu  200 0 200 900 0 0 0 0 0 0\ncpu0 1 1 1 1\ncpu1 1 1 1 1\n");
        assertEquals(200.0 / 300 * 100, pct, 1e-6);
        assertEquals(2, p.cpus());
        assertEquals(75.0, ProcStats.memUsedPct("MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 250 kB\n"), 1e-9);
        assertEquals(1.5, ProcStats.load1("1.50 1.0 0.5 1/100 42"));
    }

    // ------------------------------------------------------------------ control / safety

    @Test
    void adaptiveControllerConvergesOnSimulatedPlant() {
        // basit gecikmeli tesis: metric = 90 × level (bir adım gecikmeli)
        AdaptiveController c = new AdaptiveController(60, 5, 0.1);
        double measured = 0;
        double lastLevel = c.level();
        for (int i = 0; i < 60; i++) {
            double lv = c.update(measured, 1.0, 0);
            measured = 90 * lastLevel;
            lastLevel = lv;
        }
        assertEquals(60, measured, 60 * 0.1);
        assertFalse(c.saturated());
    }

    @Test
    void adaptiveControllerReportsSaturationWhenTargetUnreachable() {
        AdaptiveController c = new AdaptiveController(95, 5, 0.5);
        for (int i = 0; i < 30; i++) {
            c.update(40.0, 1.0, 0);
        }
        assertEquals(1.0, c.level(), 1e-9);
        assertTrue(c.saturated());
    }

    @Test
    void controllerRampsTargetFromBaseline() {
        AdaptiveController c = new AdaptiveController(80, 5, 0);
        assertEquals(20, c.effectiveTarget(0, 20), 1e-9);
        assertEquals(50, c.effectiveTarget(0.5, 20), 1e-9);
        assertEquals(80, c.effectiveTarget(1, 20), 1e-9);
    }

    static Sample sample(double dbCpu, int conns, Double heapUsed, boolean appUp) {
        AppSample a = new AppSample(appUp, 10.0, heapUsed, 100.0, 2, 6, 0, 8, 5.0, 0.0, 20.0, 40.0, 3, 0, null, null);
        DbSample d = new DbSample(true, dbCpu, 50.0, 1.0, 6, conns, 100, 3, 0, 0, 0.1, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                1.0, 60.0, 0.0, 0.0, 0, 0.0);
        return new Sample("t", 0, State.RUNNING, Map.of("checkin", a), d, new HostSample(10.0, 40.0, 40.0), List.of(),
                Map.of());
    }

    @Test
    void safetyGuardNeedsConsecutiveViolations() {
        SafetyGuard g = new SafetyGuard(v2(null), Budget.defaults(), Set.of("checkin"), List.of(), 60.0);
        assertNull(g.check(sample(95, 10, 10.0, true)));
        assertNull(g.check(sample(50, 10, 10.0, true)), "tek seferlik sıçrama sayaç sıfırlar");
        assertNull(g.check(sample(95, 10, 10.0, true)));
        assertNull(g.check(sample(95, 10, 10.0, true)));
        assertTrue(g.check(sample(95, 10, 10.0, true)).contains("DB CPU"));
    }

    @Test
    void safetyGuardAbortsOnConnectionsHeapAndUnreachableApp() {
        SafetyGuard g = new SafetyGuard(v2(null), Budget.defaults(), Set.of("checkin"), List.of("heap ≥ %97"), 60.0);
        String r = null;
        for (int i = 0; i < 3; i++) {
            r = g.check(sample(10, 95, 10.0, true));
        }
        assertTrue(r.contains("connections"), r);
        SafetyGuard h = new SafetyGuard(v2(null), Budget.defaults(), Set.of("checkin"), List.of(), 60.0);
        for (int i = 0; i < 3; i++) {
            r = h.check(sample(10, 10, 98.0, true));
        }
        assertTrue(r.contains("heap"), r);
        SafetyGuard d = new SafetyGuard(v2(null), Budget.defaults(), Set.of("checkin"), List.of(), 60.0);
        r = null;
        for (int i = 0; i < SafetyGuard.UNREACHABLE - 1; i++) {
            r = d.check(sample(10, 10, 10.0, false));
        }
        assertNull(r, "erişilemezlik 30 s dolmadan abort yok");
        assertTrue(d.check(sample(10, 10, 10.0, false)).contains("erişilemedi"));
    }

    @Test
    void windowStatsComputesAmplificationAndP95() {
        WindowStats w = new WindowStats(10_000);
        for (int i = 1; i <= 100; i++) {
            w.record(1000, i * 1000L, i % 10 != 0, i % 2 == 0 ? 3 : 1, "200");
        }
        var s = w.snapshot(1000);
        assertEquals(95.0, s.p95Ms(), 1e-9);
        assertEquals(10.0, s.errorPct(), 1e-9);
        assertEquals(2.0, s.amplification(), 1e-9);
        assertEquals(0, w.snapshot(20_000).callsPerSec(), "pencere dışı olaylar düşer");
    }

    // ------------------------------------------------------------------ senaryolar

    @Test
    void registryHasTwentyCompleteScenarios() {
        ScenarioRegistry reg = new ScenarioRegistry();
        assertEquals(20, reg.all().size());
        for (int i = 1; i <= 20; i++) {
            String id = String.format("S%02d", i);
            ScenarioDef d = reg.get(id).orElseThrow();
            assertFalse(d.prerequisites().isEmpty(), id);
            assertFalse(d.expectedImpact().isEmpty(), id);
            assertFalse(d.abortConditions().isEmpty(), id);
            assertFalse(d.cleanup().isBlank(), id);
            assertFalse(d.directTarget().isBlank(), id);
        }
    }

    @Test
    void everyScenarioProducesRealBoundedPlanAtEveryIntensity() {
        ScenarioRegistry reg = new ScenarioRegistry();
        for (ScenarioDef d : reg.all()) {
            for (Intensity in : List.of(Intensity.LIGHT, Intensity.MEDIUM, Intensity.HIGH, Intensity.CRITICAL)) {
                Ctx c = new Ctx("checkin", List.of("checkin", "reservation", "flightops"), in, null, CAL, new Random(7));
                var plan = d.plan().apply(c);
                assertFalse(plan.actions().isEmpty(), d.id() + " " + in);
                assertTrue(plan.criteria().stream().anyMatch(Criterion::required), d.id() + " zorunlu ölçüt yok");
                for (var a : plan.actions()) {
                    assertTrue(APPS.contains(a.app()), d.id() + " allowlist dışı app " + a.app());
                    switch (a.kind()) {
                        case LOOP -> {
                            assertTrue(a.path().startsWith("/lab/"), d.id() + " lab dışı uç " + a.path());
                            assertTrue(a.maxWorkers() <= CAL.labMaxConcurrentOps(), d.id() + " worker limiti");
                            assertTrue(a.maxWorkers() >= a.minWorkers(), d.id());
                        }
                        case NETEM -> {
                            assertTrue(a.delayMs() <= 2000 && a.lossPct() <= 50, d.id());
                            assertTrue(a.netemTarget().equals("db") || APPS.contains(a.netemTarget()), d.id());
                        }
                        case TRAFFIC -> assertTrue(a.trafficRps() > 0 && a.trafficRps() <= 100, d.id());
                    }
                }
                if (plan.control() != null) {
                    assertTrue(plan.control().target() > 0, d.id());
                }
            }
        }
    }

    @Test
    void intensityIncreasesControlTargets() {
        ScenarioRegistry reg = new ScenarioRegistry();
        for (String id : List.of("S01", "S02", "S04", "S05", "S06", "S07", "S08", "S09", "S10", "S11", "S12", "S13",
                "S14", "S15", "S16", "S17", "S19")) {
            double prev = -1;
            for (Intensity in : List.of(Intensity.LIGHT, Intensity.MEDIUM, Intensity.HIGH, Intensity.CRITICAL)) {
                var p = reg.get(id).orElseThrow().plan()
                        .apply(new Ctx("checkin", List.of("checkin", "baggage"), in, null, CAL, new Random(1)));
                assertTrue(p.control().target() >= prev, id + " " + in);
                prev = p.control().target();
            }
        }
    }

    @Test
    void safetyAbortsImmediatelyOnAppRestart() {
        SafetyGuard g = new SafetyGuard(v2(null), Budget.defaults(), Set.of("checkin"), List.of(), 60.0);
        assertNull(g.check(withStart(1000.0)));
        assertNull(g.check(withStart(1000.0)));
        String r = g.check(withStart(1300.0));
        assertNotNull(r);
        assertTrue(r.contains("yeniden başladı"), r);
    }

    static Sample withStart(double start) {
        Sample b = sample(10, 10, 50.0, true);
        AppSample a = b.apps().get("checkin");
        AppSample x = new AppSample(true, a.cpuPct(), a.heapUsedMb(), a.heapMaxMb(), a.poolActive(), a.poolIdle(),
                a.poolPending(), a.poolMax(), a.rps(), a.errorPct(), a.p95Ms(), a.p99Ms(), a.dbConnections(),
                a.dbLockWaits(), null, start);
        return new Sample(b.at(), b.tMs(), b.phase(), Map.of("checkin", x), b.db(), b.appVm(), b.workloads(), b.traffic());
    }

    @Test
    void memorySortIsOpenLoopAndBounded() {
        ScenarioRegistry reg = new ScenarioRegistry();
        double prev = -1;
        for (Intensity in : List.of(Intensity.LIGHT, Intensity.MEDIUM, Intensity.HIGH, Intensity.CRITICAL)) {
            for (List<String> targets : List.of(List.of("checkin"), APPS)) {
                var p = reg.get("S18").orElseThrow().plan().apply(new Ctx("checkin", targets, in, null, CAL, new Random(1)));
                assertNull(p.control(), "S18 open-loop olmalı (bellek sert kaynak)");
                double peak = ((Number) p.parameters().get("estimatedPeakMb")).doubleValue();
                assertTrue(peak <= 1400, in.name() + " tahmini tepe bellek " + peak + " MB");
                if (targets.size() == 1) {
                    assertTrue(peak > prev, in.name());
                    prev = peak;
                }
            }
        }
        // custom worker sayısı bellek üst sınırını delemez
        var custom = new com.lab.air.chaos.v2.model.Model.Custom(40, null, null, null, null, null, null, null, null, null, null);
        var p = reg.get("S18").orElseThrow().plan().apply(new Ctx("checkin", APPS, Intensity.CRITICAL, custom, CAL, new Random(1)));
        assertTrue(((Number) p.parameters().get("estimatedPeakMb")).doubleValue() <= 1400);
    }

    /** DB yükü üreten lab uçları; uygulama CPU / heap / bağımlılık / ağ uçları senaryolarda kullanılamaz. */
    static final Set<String> DB_LOAD_PATHS = Set.of("/lab/chaos/table-lock", "/lab/chaos/contend", "/lab/chaos/long-tx",
            "/lab/chaos/cpu-query", "/lab/chaos/write-burst", "/lab/chaos/heavy-read", "/lab/chaos/temp-spill",
            "/lab/chaos/serialization", "/lab/chaos/deadlock", "/lab/chaos/connection-storm", "/lab/chaos/commit-storm",
            "/lab/chaos/update-churn", "/lab/chaos/advisory-lock", "/lab/chaos/idle-in-tx", "/lab/chaos/parallel-query",
            "/lab/chaos/point-queries", "/lab/chaos/memory-sort");

    static Set<String> loadTypes(ScenarioRegistry reg, String id, List<String> targets) {
        var p = reg.get(id).orElseThrow().plan().apply(new Ctx("checkin", targets, Intensity.HIGH, null, CAL, new Random(3)));
        Set<String> types = new java.util.TreeSet<>();
        for (var a : p.actions()) {
            // S03 iki ayrı satır kümesinde aynı yük tipini (long transaction) kullanır
            types.add(a.path());
        }
        return types;
    }

    @Test
    void everyScenarioLoadsOnlyPostgres() {
        ScenarioRegistry reg = new ScenarioRegistry();
        assertEquals(20, reg.all().size());
        for (ScenarioDef d : reg.all()) {
            var p = d.plan().apply(new Ctx("checkin", APPS, Intensity.HIGH, null, CAL, new Random(3)));
            for (var a : p.actions()) {
                assertEquals(ScenarioDef.Kind.LOOP, a.kind(), d.id() + ": netem / iş trafiği eylemi olmamalı");
                assertTrue(DB_LOAD_PATHS.contains(a.path()), d.id() + " DB yükü olmayan uç: " + a.path());
            }
            if (p.control() != null) {
                assertEquals(Scope.DB, p.control().scope(), d.id() + " kontrol metriği DB metriği olmalı");
            }
        }
    }

    @Test
    void singleTypeScenariosAreAllDifferentAndSomeCombineTypes() {
        ScenarioRegistry reg = new ScenarioRegistry();
        Set<String> multi = Set.of("S17", "S19", "S20");
        Map<String, String> seen = new java.util.HashMap<>();
        for (ScenarioDef d : reg.all()) {
            Set<String> types = loadTypes(reg, d.id(), APPS);
            if (multi.contains(d.id())) {
                assertTrue(types.size() >= 2, d.id() + " birden fazla yük tipi üretmeli: " + types);
            } else {
                assertEquals(1, types.size(), d.id() + " tek yük tipi üretmeli: " + types);
                String type = types.iterator().next();
                String other = seen.put(type, d.id());
                assertNull(other, d.id() + " ile " + other + " aynı yük tipini üretiyor: " + type);
            }
        }
        assertEquals(17, seen.size());
        assertEquals(DB_LOAD_PATHS, Set.copyOf(seen.keySet()), "her DB yük tipi tek bir senaryonun");
        for (String id : ScenarioRegistry.CASCADE_POOL) {
            assertFalse(multi.contains(id), "birleşik senaryo alt senaryo olamaz: " + id);
        }
    }

    @Test
    void everySelectedAppStartsTheLoad() {
        ScenarioRegistry reg = new ScenarioRegistry();
        for (ScenarioDef d : reg.all()) {
            if ("S20".equals(d.id())) {
                continue;       // alt senaryolar initiator'ı kendi seçer; aşağıda ayrıca denetlenir
            }
            var p = d.plan().apply(new Ctx("checkin", APPS, Intensity.MEDIUM, null, CAL, new Random(3)));
            Set<String> starters = new java.util.HashSet<>();
            p.actions().forEach(a -> starters.add(a.app()));
            assertEquals(Set.copyOf(APPS), starters, d.id() + ": seçilen her uygulama yükü başlatmalı");
        }
        var s20 = reg.get("S20").orElseThrow().plan().apply(new Ctx("checkin", APPS, Intensity.HIGH, null, CAL, new Random(3)));
        Set<String> starters = new java.util.HashSet<>();
        s20.actions().forEach(a -> starters.add(a.app()));
        assertTrue(starters.size() >= 2, "S20: yükü en az 2 uygulama başlatmalı");
    }

    @Test
    void cascadeSelectionIsDeterministicForSeed() {
        ScenarioRegistry reg = new ScenarioRegistry();
        var a = reg.get("S20").orElseThrow().plan().apply(new Ctx("checkin", APPS, Intensity.HIGH, null, CAL, new Random(99)));
        var b = reg.get("S20").orElseThrow().plan().apply(new Ctx("checkin", APPS, Intensity.HIGH, null, CAL, new Random(99)));
        assertEquals(a.parameters().get("subScenarios"), b.parameters().get("subScenarios"));
        assertEquals(3, ((List<?>) a.parameters().get("subScenarios")).size());
        assertEquals(a.actions(), b.actions());
    }

    // ------------------------------------------------------------------ orchestrator (Spring'siz)

    static V2Properties v2(String env) {
        return new V2Properties("", "lab_monitor", "", 9190, "https://abc12345.live.dynatrace.com", 2000, APPS, env, 5,
                3600, 3, 1024);
    }

    Orchestrator orch(Path dir, String env) {
        var cp = new ChaosProperties(null, "t".repeat(20), dir.resolve("experiments").toString(), null, null, 1);
        var store = new V2Store(cp);
        return new Orchestrator(new MetricsCollector(cp, v2(env)), cp, v2(env), store,
                new ExperimentService(cp, new SimpleMeterRegistry()), new RunGate());
    }

    static Request req(String sc, String init, TargetMode m, List<String> t, Long seed) {
        return new Request(sc, init, m, t, Intensity.MEDIUM, null, null, null, seed, null, null, null, null, null);
    }

    @Test
    void resolveIsDeterministicAndHonoursTargetModes(@TempDir Path dir) {
        Orchestrator o = orch(dir, "lab");
        var r1 = o.resolve(req("S02", null, TargetMode.RANDOM, List.of(), 1234L));
        var r2 = o.resolve(req("S02", null, TargetMode.RANDOM, List.of(), 1234L));
        assertEquals(r1.ctx().initiator(), r2.ctx().initiator());
        assertEquals(r1.ctx().targets(), r2.ctx().targets());
        assertEquals(APPS, o.resolve(req("S04", "checkin", TargetMode.ALL, List.of(), 1L)).ctx().targets());
        assertEquals(List.of("checkin"), o.resolve(req("S04", "checkin", TargetMode.SINGLE, List.of(), 1L)).ctx().targets());
        var s09 = o.resolve(req("S09", "checkin", TargetMode.SINGLE, List.of(), 5L));
        assertTrue(s09.participants().size() >= 1);
        var s15 = o.resolve(req("S15", "checkin", TargetMode.SINGLE, List.of(), 5L));
        assertTrue(s15.participants().size() >= 2, "minApps=2 → seed'e bağlı peer eklenir");
        var s03 = o.resolve(req("S03", "flightops", TargetMode.AUTO, List.of(), 5L));
        assertEquals(APPS, s03.ctx().targets(), "AUTO: S03 tüm uygulamalarda uzun transaction açar");
        var s04 = o.resolve(req("S04", "checkin", TargetMode.MULTI, List.of("baggage", "flightops"), 5L));
        assertEquals(List.of("checkin", "baggage", "flightops"), s04.participants());
        assertEquals(3, s04.plan().actions().size(), "seçilen her uygulama yükü başlatır");
        assertEquals(s04.participants(), s04.expectedAffected());
        assertEquals(List.of(), o.resolve(req("S13", "checkin", TargetMode.ALL, List.of(), 5L)).expectedAffected(),
                "advisory lock iş endpoint'lerini etkilemez");
    }

    @Test
    void validationRejectsUnsafeRequests(@TempDir Path dir) {
        Orchestrator o = orch(dir, "lab");
        assertFalse(o.validate(req("S99", null, null, null, null)).isEmpty());
        assertFalse(o.validate(req("S01", "evil-app", null, null, null)).isEmpty());
        var bad = new Request("S13", "checkin", null, null, Intensity.CUSTOM,
                new com.lab.air.chaos.v2.model.Model.Custom(100, null, null, null, null, null, 90, 5000, 80.0, null, null),
                null, null, null, null, null, null, null, null);
        assertTrue(o.validate(bad).size() >= 4, o.validate(bad).toString());
        assertTrue(o.validate(req("S01", "checkin", null, null, null)).isEmpty());
        Orchestrator prod = orch(dir.resolve("p"), "production");
        assertTrue(prod.validate(req("S01", "checkin", null, null, null)).stream().anyMatch(e -> e.contains("lab")));
    }

    // ------------------------------------------------------------------ analyzer

    static Sample phaseSample(State st, double p95Checkin, double p95Baggage, double lockWaiters) {
        AppSample c = new AppSample(true, 10.0, 50.0, 100.0, 2, 6, 0, 8, 5.0, 0.0, p95Checkin, p95Checkin * 2, 3, 0, null, null);
        AppSample b = new AppSample(true, 10.0, 50.0, 100.0, 2, 6, 0, 8, 5.0, 0.0, p95Baggage, p95Baggage * 2, 3, 0, null, null);
        DbSample d = new DbSample(true, 20.0, 50.0, 1.0, 6, 20, 100, 3, 0, (int) lockWaiters, 0.1, 50.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 60.0, 0.0, 0.0, 0, 0.0);
        return new Sample("t", 0, st, Map.of("checkin", c, "baggage", b), d, null, List.of(), Map.of());
    }

    @Test
    void analyzerComparesBaselineRunRecoveryAndEvaluatesCriteria() {
        List<Sample> s = new ArrayList<>();
        for (int i = 0; i < 5; i++) {
            s.add(phaseSample(State.BASELINING, 20, 15, 0));
        }
        for (int i = 0; i < 10; i++) {
            s.add(phaseSample(State.RUNNING, 200, 16, 4));
        }
        for (int i = 0; i < 5; i++) {
            s.add(phaseSample(State.RECOVERING, 22, 15, 0));
        }
        List<Criterion> crit = List.of(
                Criterion.atLeast("lock waiters", Metric.LOCK_WAITERS, Scope.DB, Agg.MAX, 3),
                Criterion.rise("checkin p95", Metric.APP_P95_MS, Scope.INITIATOR, Agg.P90, 1.5, 20, true),
                Criterion.stays("baggage izole", Metric.APP_P95_MS, Scope.ANY_NON_INITIATOR, Agg.P90, 1.5, 50),
                Criterion.atLeast("deadlock (veri yok)", Metric.DEADLOCKS_PER_MIN, Scope.DB, Agg.MAX, 1).optional());
        var out = new ImpactAnalyzer().analyze(new ImpactAnalyzer.Input("checkin", List.of("checkin"), List.of("checkin"),
                List.of("checkin", "baggage"), crit, s, Map.of("recovered", true), Map.of(), State.COMPLETED));
        assertEquals("IMPACT_CONFIRMED", out.get("verdict"));
        @SuppressWarnings("unchecked")
        var c = (List<Map<String, Object>>) out.get("criteria");
        assertEquals(ImpactAnalyzer.Result.PASS, c.get(0).get("result"));
        assertEquals(ImpactAnalyzer.Result.PASS, c.get(1).get("result"));
        assertEquals(ImpactAnalyzer.Result.PASS, c.get(2).get("result"));
        assertEquals(ImpactAnalyzer.Result.FAIL, c.get(3).get("result"));
        @SuppressWarnings("unchecked")
        var evo = (Map<String, Object>) out.get("expectedVsObserved");
        assertEquals(List.of("checkin"), evo.get("observedAffected"));
        assertEquals(List.of(), evo.get("unexpectedlyAffected"));
    }

    @Test
    void analyzerReportsNotObservedAndNoDataHonestly() {
        List<Sample> s = new ArrayList<>();
        for (int i = 0; i < 5; i++) {
            s.add(phaseSample(State.BASELINING, 20, 15, 0));
            s.add(phaseSample(State.RUNNING, 21, 15, 0));
        }
        var notObs = new ImpactAnalyzer().analyze(new ImpactAnalyzer.Input("checkin", List.of("checkin"),
                List.of("checkin"), List.of("checkin", "baggage"),
                List.of(Criterion.atLeast("lock", Metric.LOCK_WAITERS, Scope.DB, Agg.MAX, 1)), s, Map.of(), Map.of(),
                State.COMPLETED));
        assertEquals("IMPACT_NOT_OBSERVED", notObs.get("verdict"));
        var noData = new ImpactAnalyzer().analyze(new ImpactAnalyzer.Input("checkin", List.of("checkin"),
                List.of("checkin"), List.of("checkin"),
                List.of(Criterion.part("wl", Metric.WL_P95_MS, "nope", Agg.MAX, 1, true)), s, Map.of(), Map.of(),
                State.COMPLETED));
        assertEquals("INCONCLUSIVE", noData.get("verdict"));
    }

    @Test
    void dynatraceLinksUseAppsDomainAndCarryExperimentId(@TempDir Path dir) {
        Orchestrator o = orch(dir, "lab");
        var sub = o.submit(req("S01", "checkin", TargetMode.SINGLE, List.of(), 3L), "test");
        assertEquals(202, sub.status(), String.valueOf(sub.errors()));
        var dt = sub.experiment().dynatrace;
        assertTrue(String.valueOf(dt.get("logsApp")).startsWith("https://abc12345.apps.dynatrace.com/"));
        assertTrue(String.valueOf(dt.get("logsDql")).contains(sub.experiment().id));
        assertNotNull(sub.experiment().plan.get("actions"));
        // idempotency: aynı clientRequestId → aynı deney
        var r = new Request("S01", "checkin", TargetMode.SINGLE, List.of(), Intensity.LIGHT, null, null, null, 1L, null,
                "cid-1", null, null, null);
        var a = o.submit(r, "test");
        var b = o.submit(r, "test");
        assertEquals(a.experiment().id, b.experiment().id);
        assertEquals(200, b.status());
    }

    @Test
    void reportShowsIstanbulTimeNextToUtc() {
        assertEquals("2026-11-25 00:22:37 İstanbul (2026-11-24T21:22:37.372105572Z)",
                ReportWriter.withLocal("2026-11-24T21:22:37.372105572Z"));
        assertEquals("-", ReportWriter.withLocal(null));
        assertEquals("bozuk", ReportWriter.withLocal("bozuk"));
    }
}
