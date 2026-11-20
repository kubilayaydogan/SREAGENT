package com.lab.air.chaos;

import com.lab.air.chaos.engine.ExperimentService;
import com.lab.air.chaos.engine.ExperimentService.StartResult;
import com.lab.air.chaos.engine.ExperimentSpec;
import com.lab.air.chaos.engine.ExperimentSpec.ActivePhases;
import com.lab.air.chaos.engine.ExperimentSpec.Background;
import com.lab.air.chaos.engine.ExperimentSpec.Mode;
import com.lab.air.chaos.engine.ExperimentSpec.TargetSpec;
import com.lab.air.chaos.engine.SpecValidator;
import com.lab.air.chaos.engine.Stats;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.store.ExperimentRecord;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import tools.jackson.databind.json.JsonMapper;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EngineTest {

    static final String TOKEN = "chaos-test-lab-token-123";

    @TempDir
    Path dir;

    ExperimentService service(StubApps stub) {
        var props = new ChaosProperties(stub.apps(), TOKEN, dir.toString(), null, null, 1.0);
        return new ExperimentService(props, new SimpleMeterRegistry());
    }

    static TargetSpec target(String app, Mode mode, double rps, int conc, int maxInFlight, Map<String, Integer> mix) {
        return new TargetSpec(app, "stress", mode, rps, conc, 0, maxInFlight, 3000, mix, 1, null, false, 0, ActivePhases.LOAD);
    }

    static ExperimentSpec spec(String crid, int loadSec, List<TargetSpec> targets) {
        return new ExperimentSpec(crid, "test", "T", 0, 1, loadSec, 1, new Background(false, 0, null), targets);
    }

    static long sum(List<Stats.Row> rows, Predicate<Stats.Row> f, java.util.function.ToLongFunction<Stats.Row> v) {
        return rows.stream().filter(f).mapToLong(v).sum();
    }

    ExperimentRecord runToEnd(ExperimentService s, ExperimentSpec spec) throws Exception {
        var r = s.start(spec);
        assertInstanceOf(StartResult.Started.class, r, () -> r.toString());
        var run = s.activeRun().orElseThrow();
        run.awaitEnd(60_000);
        return s.get(run.id()).orElseThrow();
    }

    @Test
    void rpsModeIsOpenLoopAndDropsWhenInFlightCapReached() throws Exception {
        try (var stub = new StubApps(250)) {
            var s = service(stub);
            // 40 rps hedef, sunucu 250 ms, maxInFlight=4 → en fazla ~16 rps gönderilebilir
            var rec = runToEnd(s, spec("test-rps-0001", 3,
                    List.of(target("baggage", Mode.RPS, 40, 0, 4, Map.of("bag_status", 1)))));
            Predicate<Stats.Row> load = x -> x.phase().name().equals("LOAD") && x.source().equals("target");
            long intended = sum(rec.results(), load, Stats.Row::intended);
            long sent = sum(rec.results(), load, Stats.Row::sent);
            long dropped = sum(rec.results(), load, Stats.Row::dropped);
            assertTrue(intended >= 100 && intended <= 130, "intended=" + intended);
            assertEquals(intended, sent + dropped + sum(rec.results(), load, Stats.Row::noData));
            assertTrue(dropped > 30, "dropped=" + dropped);
            assertTrue(stub.maxConcurrent.get() <= 4, "maxConcurrent=" + stub.maxConcurrent.get());
            assertEquals("COMPLETED", rec.state());
            assertTrue(rec.phases().containsKey("BASELINE") && rec.phases().containsKey("RECOVERY"));
            assertEquals(4, stub.cleanupCalls.get(), "dört uygulamaya cleanup çağrılmalı");
            assertTrue(stub.experimentHeaderSeen.get() > 0);
        }
    }

    @Test
    void concurrencyModeIsClosedLoopWithoutDrops() throws Exception {
        try (var stub = new StubApps(200)) {
            var s = service(stub);
            var rec = runToEnd(s, spec("test-conc-0001", 3,
                    List.of(target("flightops", Mode.CONCURRENCY, 0, 2, 0, Map.of("list_flights", 1)))));
            Predicate<Stats.Row> load = x -> x.phase().name().equals("LOAD") && x.source().equals("target");
            long completed = sum(rec.results(), load, Stats.Row::completed);
            assertEquals(0, sum(rec.results(), load, Stats.Row::dropped));
            // 2 işçi × (3 sn / 0.2 sn) ≈ 30
            assertTrue(completed >= 20 && completed <= 34, "completed=" + completed);
            assertTrue(stub.maxConcurrent.get() <= 2);
        }
    }

    @Test
    void startIsIdempotentAndSingleActive() throws Exception {
        try (var stub = new StubApps(5)) {
            var s = service(stub);
            var sp = spec("test-idem-0001", 30, List.of(target("reservation", Mode.RPS, 5, 0, 8, Map.of("search_flights", 1))));
            var a = s.start(sp);
            assertInstanceOf(StartResult.Started.class, a);
            var b = s.start(sp);
            assertInstanceOf(StartResult.Replayed.class, b);
            assertEquals(((StartResult.Started) a).record().experimentId(), ((StartResult.Replayed) b).record().experimentId());

            var changed = spec("test-idem-0001", 31, sp.targets());
            assertEquals(409, ((StartResult.Rejected) s.start(changed)).status());
            var other = spec("test-idem-0002", 30, sp.targets());
            assertEquals(409, ((StartResult.Rejected) s.start(other)).status(), "aynı anda tek aktif deney");

            String res = s.stopAll("test");
            assertTrue(res.endsWith("STOPPED"), res);
            var rec = s.get(((StartResult.Started) a).record().experimentId()).orElseThrow();
            assertEquals("STOPPED", rec.state());
            assertEquals(4, stub.cleanupCalls.get());
            // Bitmiş deneyin anahtarı tekrar gönderilirse yine aynı kayıt döner (dosyadan)
            assertInstanceOf(StartResult.Replayed.class, s.start(sp));
        }
    }

    @Test
    void labOperationsCarryTokenAndBackgroundRunsOnAllApps() throws Exception {
        try (var stub = new StubApps(5)) {
            var s = service(stub);
            var t = new TargetSpec("reservation", "stress", Mode.CONCURRENCY, 0, 1, 0, 0, 3000,
                    Map.of("lab_hold_connection", 1), 1, null, false, 100, ActivePhases.LOAD);
            var sp = new ExperimentSpec("test-lab-00001", "t", "T", 0, 1, 2, 1, new Background(true, 2.0, null), List.of(t));
            var rec = runToEnd(s, sp);
            assertTrue(stub.labTokenSeen.get() > 0);
            for (String app : Catalog.APPS) {
                long bg = sum(rec.results(), x -> x.source().equals("background") && x.app().equals(app), Stats.Row::sent);
                assertTrue(bg > 0, "arka plan trafiği yok: " + app);
            }
            assertNotNull(rec.producer().get("processCpuMaxPct"));
        }
    }

    @Test
    void validatorEnforcesLimitsAndAllPresetsAreValid() throws Exception {
        var limits = new ChaosProperties.Limits(0, 0, 0, 0, 0, 0, 0, 0);
        var v = new SpecValidator(limits, new Catalog(), true);
        var noToken = new SpecValidator(limits, new Catalog(), false);

        var bad = spec("short", 0, List.of(
                target("reservation", Mode.RPS, 500, 0, 9999, Map.of("drop_table", 1)),
                target("nope", Mode.RPS, 1, 0, 1, Map.of())));
        List<String> errors = v.validate(bad);
        assertTrue(errors.stream().anyMatch(e -> e.contains("clientRequestId")), errors.toString());
        assertTrue(errors.stream().anyMatch(e -> e.contains("rps")), errors.toString());
        assertTrue(errors.stream().anyMatch(e -> e.contains("bilinmeyen operasyon")), errors.toString());
        assertTrue(errors.stream().anyMatch(e -> e.contains("bilinmeyen uygulama")), errors.toString());
        assertTrue(errors.stream().anyMatch(e -> e.contains("maxInFlight")), errors.toString());

        var lab = spec("lab-check-0001", 10, List.of(new TargetSpec("reservation", "stress", Mode.CONCURRENCY, 0, 2, 0, 0,
                3000, Map.of("lab_hold_booking", 1), 1, null, true, 99_000, ActivePhases.LOAD)));
        assertTrue(v.validate(lab).stream().anyMatch(e -> e.contains("holdMs")));
        assertTrue(noToken.validate(lab).stream().anyMatch(e -> e.contains("lab-token")));

        JsonMapper json = JsonMapper.builder().build();
        var res = new PathMatchingResourcePatternResolver().getResources("classpath:presets/*.json");
        assertTrue(res.length >= 9);
        for (var r : res) {
            var raw = json.readTree(r.getContentAsString(StandardCharsets.UTF_8));
            ((tools.jackson.databind.node.ObjectNode) raw).put("clientRequestId", "preset-check-" + r.getFilename().hashCode());
            var sp = json.treeToValue(raw, ExperimentSpec.class);
            assertEquals(List.of(), v.validate(sp), r.getFilename());
        }
    }

    @Test
    void readWriteRatioReweightsNormalMix() {
        var c = new Catalog();
        var mix = c.mixFor("reservation", null, 0.9);
        int reads = mix.getOrDefault("search_flights", 0) + mix.getOrDefault("get_booking", 0);
        int writes = mix.getOrDefault("create_booking", 0) + mix.getOrDefault("cancel_booking", 0);
        assertEquals(900, reads, 2);
        assertEquals(100, writes, 2);
        assertEquals(Map.of("x", 1), c.mixFor("reservation", Map.of("x", 1), 0.5));
    }
}
