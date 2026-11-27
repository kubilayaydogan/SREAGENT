package com.lab.air.chaos.v2.analysis;

import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.State;
import com.lab.air.chaos.v2.scenario.Metric;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Agg;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Criterion;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Scope;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Deney örneklerinden etki analizi. Üç pencere: baseline (BASELINING), run (RUNNING; yoksa RAMPING_UP+RUNNING),
 * recovery (RECOVERING). Değerler yalnız ölçülmüş örneklerden hesaplanır; veri yoksa sonuç NO_DATA olur.
 */
public final class ImpactAnalyzer {

    public enum Result { PASS, FAIL, NO_DATA }

    public record Input(String initiator, List<String> targets, List<String> expectedAffected, List<String> apps,
                        List<Criterion> criteria, List<Sample> samples, Map<String, Object> recovery,
                        Map<String, Object> controller, State finalState) {
    }

    private static final Metric[] APP_METRICS = {Metric.APP_P95_MS, Metric.APP_P99_MS, Metric.APP_ERROR_PCT,
            Metric.APP_RPS, Metric.APP_POOL_USAGE_PCT, Metric.APP_POOL_PENDING, Metric.APP_CPU_PCT, Metric.APP_HEAP_PCT};
    private static final Metric[] DB_METRICS = {Metric.DB_CPU_PCT, Metric.DB_MEM_PCT, Metric.DB_CONNECTIONS_PCT,
            Metric.DB_ACTIVE, Metric.LOCK_WAITERS, Metric.LONGEST_XACT_SEC, Metric.DB_TPS, Metric.DEADLOCKS_PER_MIN,
            Metric.TEMP_MB_PER_SEC, Metric.IO_READ_MB_PER_SEC, Metric.IO_WRITE_MB_PER_SEC, Metric.INSERTS_PER_SEC,
            Metric.QUERY_MEAN_MS};

    public Map<String, Object> analyze(Input in) {
        List<Sample> base = window(in.samples(), State.BASELINING);
        List<Sample> run = window(in.samples(), State.RUNNING);
        if (run.size() < 2) {
            run = new ArrayList<>(window(in.samples(), State.RAMPING_UP));
            run.addAll(window(in.samples(), State.RUNNING));
        }
        List<Sample> rec = window(in.samples(), State.RECOVERING);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("windows", Map.of("baselineSamples", base.size(), "runSamples", run.size(), "recoverySamples", rec.size()));

        // ---- success criteria
        List<Map<String, Object>> crit = new ArrayList<>();
        boolean anyRequiredFail = false;
        boolean anyRequiredNoData = false;
        for (Criterion c : in.criteria()) {
            Map<String, Object> r = evaluate(c, in, base, run);
            crit.add(r);
            if (c.required()) {
                Result res = (Result) r.get("result");
                anyRequiredFail |= res == Result.FAIL;
                anyRequiredNoData |= res == Result.NO_DATA;
            }
        }
        out.put("criteria", crit);

        // ---- observed impact
        Map<String, Object> apps = new LinkedHashMap<>();
        List<String> observedAffected = new ArrayList<>();
        for (String app : in.apps()) {
            Map<String, Object> metrics = new LinkedHashMap<>();
            for (Metric m : APP_METRICS) {
                metrics.put(m.name(), compare(m, app, base, run, rec));
            }
            boolean affected = affected(metrics);
            if (affected) {
                observedAffected.add(app);
            }
            String role = app.equals(in.initiator()) ? "initiator"
                    : in.targets().contains(app) ? "direct-target" : "not-targeted";
            apps.put(app, Map.of("role", role, "observedAffected", affected, "metrics", metrics));
        }
        Map<String, Object> db = new LinkedHashMap<>();
        for (Metric m : DB_METRICS) {
            db.put(m.name(), compare(m, null, base, run, rec));
        }
        out.put("apps", apps);
        out.put("db", db);
        List<String> unexpected = observedAffected.stream().filter(a -> !in.expectedAffected().contains(a)).toList();
        List<String> missing = in.expectedAffected().stream().filter(a -> !observedAffected.contains(a)).toList();
        out.put("expectedVsObserved", Map.of("expectedAffected", in.expectedAffected(), "observedAffected",
                observedAffected, "unexpectedlyAffected", unexpected, "expectedButNotObserved", missing));
        out.put("recovery", in.recovery() == null ? Map.of() : in.recovery());
        out.put("controller", in.controller() == null ? Map.of() : in.controller());

        String verdict;
        if (run.isEmpty()) {
            verdict = "INCONCLUSIVE";
        } else if (anyRequiredFail) {
            verdict = "IMPACT_NOT_OBSERVED";
        } else if (anyRequiredNoData) {
            verdict = "INCONCLUSIVE";
        } else {
            verdict = "IMPACT_CONFIRMED";
        }
        out.put("verdict", verdict);
        out.put("finalState", in.finalState());
        return out;
    }

    static List<Sample> window(List<Sample> all, State phase) {
        return all.stream().filter(s -> s.phase() == phase).toList();
    }

    static Double agg(Collection<Double> vals, Agg a) {
        List<Double> v = vals.stream().filter(Objects::nonNull).filter(d -> !d.isNaN()).sorted().toList();
        if (v.isEmpty()) {
            return null;
        }
        return switch (a) {
            case MAX -> v.getLast();
            case AVG -> v.stream().mapToDouble(Double::doubleValue).average().orElse(Double.NaN);
            case P90 -> v.get(Math.min(v.size() - 1, (int) Math.ceil(0.9 * v.size()) - 1));
        };
    }

    static List<Double> series(List<Sample> s, Metric m, String key) {
        return s.stream().map(x -> m.value(x, key)).toList();
    }

    private Map<String, Object> compare(Metric m, String app, List<Sample> base, List<Sample> run, List<Sample> rec) {
        Double b = agg(series(base, m, app), Agg.AVG);
        Double r = agg(series(run, m, app), Agg.P90);
        Double rMax = agg(series(run, m, app), Agg.MAX);
        Double rc = agg(series(rec, m, app), Agg.AVG);
        Map<String, Object> o = new LinkedHashMap<>();
        o.put("baselineAvg", round(b));
        o.put("runP90", round(r));
        o.put("runMax", round(rMax));
        o.put("recoveryAvg", round(rc));
        o.put("changePct", b == null || r == null || Math.abs(b) < 1e-9 ? null : round(100 * (r - b) / Math.abs(b)));
        return o;
    }

    @SuppressWarnings("unchecked")
    private static boolean affected(Map<String, Object> metrics) {
        Map<String, Object> p95 = (Map<String, Object>) metrics.get("APP_P95_MS");
        Map<String, Object> err = (Map<String, Object>) metrics.get("APP_ERROR_PCT");
        Map<String, Object> pend = (Map<String, Object>) metrics.get("APP_POOL_PENDING");
        Double b = (Double) p95.get("baselineAvg");
        Double r = (Double) p95.get("runP90");
        boolean lat = b != null && r != null && r >= Math.max(b * 1.5, b + 20);
        Double eb = (Double) err.get("baselineAvg");
        Double er = (Double) err.get("runP90");
        boolean errs = er != null && er >= (eb == null ? 0 : eb) + 2;
        Double pr = (Double) pend.get("runMax");
        boolean pending = pr != null && pr >= 1;
        return lat || errs || pending;
    }

    Map<String, Object> evaluate(Criterion c, Input in, List<Sample> base, List<Sample> run) {
        List<String> keys = switch (c.scope()) {
            case INITIATOR -> List.of(in.initiator());
            case TARGETS, ANY_TARGET -> in.targets().stream().filter(t -> !t.equals(in.initiator())).toList().isEmpty()
                    ? List.of(in.initiator()) : in.targets().stream().filter(t -> !t.equals(in.initiator())).toList();
            case ANY_NON_INITIATOR -> in.apps().stream().filter(a -> !a.equals(in.initiator())).toList();
            case PART -> List.of(c.part());
            case DB, APPVM -> java.util.Collections.singletonList((String) null);
        };
        boolean allMode = c.scope() == Scope.TARGETS || (!c.gte() && c.scope() == Scope.ANY_NON_INITIATOR);
        List<Map<String, Object>> per = new ArrayList<>();
        for (String k : keys) {
            Double observed = agg(series(run, c.metric(), k), c.agg());
            Double baseline = agg(series(base, c.metric(), k), Agg.AVG);
            Double threshold = c.absolute() != null ? c.absolute()
                    : baseline == null ? null : baseline * c.factor() + c.add();
            Result res = observed == null || threshold == null ? Result.NO_DATA
                    : (c.gte() ? observed >= threshold : observed <= threshold) ? Result.PASS : Result.FAIL;
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("key", k);
            r.put("observed", round(observed));
            r.put("baseline", round(baseline));
            r.put("threshold", round(threshold));
            r.put("result", res);
            per.add(r);
        }
        Result result;
        Map<String, Object> shown;
        if (allMode) {
            // tüm anahtarlar sağlamalı: herhangi FAIL → FAIL; veri olanların hepsi PASS → PASS
            shown = first(per, Result.FAIL);
            result = shown != null ? Result.FAIL : (shown = first(per, Result.PASS)) != null ? Result.PASS : Result.NO_DATA;
        } else {
            // en az bir anahtar sağlamalı
            shown = first(per, Result.PASS);
            result = shown != null ? Result.PASS : (shown = first(per, Result.FAIL)) != null ? Result.FAIL : Result.NO_DATA;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("description", c.description());
        out.put("metric", c.metric().name());
        out.put("metricLabel", c.metric().label);
        out.put("scope", c.scope());
        out.put("agg", c.agg());
        out.put("expected", (c.gte() ? "≥ " : "≤ ") + (c.absolute() != null ? round(c.absolute())
                : "baseline × " + c.factor() + (c.add() != 0 ? " + " + c.add() : "")));
        out.put("required", c.required());
        if (shown != null) {
            out.putAll(shown);
        } else if (!per.isEmpty()) {
            out.putAll(per.getFirst());
        }
        out.put("result", result);
        out.put("perKey", per);
        return out;
    }

    private static Map<String, Object> first(List<Map<String, Object>> per, Result r) {
        return per.stream().filter(m -> m.get("result") == r).findFirst().orElse(null);
    }

    static Double round(Double d) {
        return d == null || d.isNaN() || d.isInfinite() ? null : Math.round(d * 100) / 100.0;
    }
}
