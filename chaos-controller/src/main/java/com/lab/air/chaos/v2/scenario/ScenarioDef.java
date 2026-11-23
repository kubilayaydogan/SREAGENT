package com.lab.air.chaos.v2.scenario;

import com.lab.air.chaos.v2.model.Model.Custom;
import com.lab.air.chaos.v2.model.Model.Intensity;

import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.function.Function;

/**
 * Bir chaos senaryosunun tanımı. {@link #plan} çalışma anında (initiator, hedefler, yoğunluk, kalibrasyon) ile
 * somut {@link Plan} üretir; aynı girdi + seed → aynı plan.
 */
public record ScenarioDef(
        String id,
        String name,
        String category,
        String description,
        List<String> prerequisites,
        String initiatorRole,
        String directTarget,
        int minApps,
        boolean defaultAllTargets,
        List<String> expectedImpact,
        List<String> abortConditions,
        String cleanup,
        Function<Ctx, Plan> plan) {

    /** Plan üretim bağlamı. */
    public record Ctx(String initiator, List<String> targets, Intensity intensity, Custom custom, Calibration cal,
                      Random rnd) {
        public int i() {
            return intensity.idx();
        }

        /** Yoğunluğa göre seçim: LIGHT, MEDIUM, HIGH, CRITICAL. */
        @SafeVarargs
        public final <T> T pick(T... v) {
            return v[Math.min(i(), v.length - 1)];
        }

        public int err() {
            return custom == null || custom.errorPct() == null ? 0 : Math.max(0, Math.min(50, custom.errorPct()));
        }

        /** initiator dışındaki ilk hedef; yoksa initiator dışındaki rastgele bir uygulama. */
        public String peer() {
            for (String t : targets) {
                if (!t.equals(initiator)) {
                    return t;
                }
            }
            List<String> others = cal.apps().stream().filter(a -> !a.equals(initiator)).toList();
            return others.get(rnd.nextInt(others.size()));
        }

        public List<String> othersThanInitiator() {
            return targets.stream().filter(t -> !t.equals(initiator)).toList();
        }
    }

    /** Ortamdan keşfedilen kapasite (kalibrasyon). */
    public record Calibration(List<String> apps, Map<String, Integer> poolMax, int maxConnections,
                              int roleConnectionLimit, int labMaxConcurrentOps, Integer dbCpus,
                              Map<String, Double> heapMaxMb, int labMaxHoldMs) {
        public int pool(String app) {
            return poolMax.getOrDefault(app, 8);
        }
    }

    public enum Kind { LOOP, NETEM, TRAFFIC }

    public enum Scope { INITIATOR, TARGETS, ANY_TARGET, ANY_NON_INITIATOR, DB, APPVM, PART }

    public enum Agg { MAX, AVG, P90 }

    /**
     * Somut eylem.
     * LOOP: workers adet sanal thread path'i çağırır ({hotFlight}, {pair}, {peer} yer tutucuları her çağrıda çözülür);
     * ratePerWorker > 0 ise worker başına saniyede o kadar çağrı (aksi halde ardışık).
     * NETEM: app'in sidecar'ına netemTarget için delay/loss. TRAFFIC: app'e iş trafiği (Catalog karışımı).
     */
    public record Action(String part, Kind kind, String app, String path, Map<String, String> params,
                         int minWorkers, int maxWorkers, double ratePerWorker, boolean controlled, int startOffsetSec,
                         String netemTarget, int delayMs, int jitterMs, double lossPct,
                         double trafficRps, int trafficTimeoutMs, int trafficRetries, boolean hotRows) {

        public static Action loop(String part, String app, String path, Map<String, String> params, int min, int max,
                                  double rate, boolean controlled) {
            return new Action(part, Kind.LOOP, app, path, params, min, max, rate, controlled, 0, null, 0, 0, 0,
                    0, 0, 0, false);
        }

        public static Action netem(String part, String app, String target, int delayMs, int jitterMs, double lossPct) {
            return new Action(part, Kind.NETEM, app, null, Map.of(), 0, 0, 0, false, 0, target, delayMs, jitterMs,
                    lossPct, 0, 0, 0, false);
        }

        public static Action traffic(String part, String app, double rps, int users, int timeoutMs, int retries,
                                     boolean hotRows, boolean controlled) {
            return new Action(part, Kind.TRAFFIC, app, null, Map.of(), Math.max(1, users / 4), users, 0, controlled, 0,
                    null, 0, 0, 0, rps, timeoutMs, retries, hotRows);
        }

        public Action offset(int sec) {
            return new Action(part, kind, app, path, params, minWorkers, maxWorkers, ratePerWorker, controlled, sec,
                    netemTarget, delayMs, jitterMs, lossPct, trafficRps, trafficTimeoutMs, trafficRetries, hotRows);
        }

        public Action renamed(String newPart) {
            return new Action(newPart, kind, app, path, params, minWorkers, maxWorkers, ratePerWorker, controlled,
                    startOffsetSec, netemTarget, delayMs, jitterMs, lossPct, trafficRps, trafficTimeoutMs,
                    trafficRetries, hotRows);
        }
    }

    /** Adaptive kontrol: metric (scope=INITIATOR → initiator uygulaması) hedef değerde tutulur. */
    public record Control(Metric metric, Scope scope, double target, double tolerancePct) {
    }

    /**
     * Başarı ölçütü. absolute != null → eşik; aksi halde baseline × factor + add. required=false → bilgi amaçlı
     * (raporda gösterilir, sonucu düşürmez). part: Scope.PART için workload adı.
     */
    public record Criterion(String description, Metric metric, Scope scope, Agg agg, boolean gte, Double absolute,
                            double factor, double add, boolean required, String part) {

        public static Criterion atLeast(String d, Metric m, Scope s, Agg a, double v) {
            return new Criterion(d, m, s, a, true, v, 0, 0, true, null);
        }

        public static Criterion rise(String d, Metric m, Scope s, Agg a, double factor, double add, boolean required) {
            return new Criterion(d, m, s, a, true, null, factor, add, required, null);
        }

        public static Criterion stays(String d, Metric m, Scope s, Agg a, double factor, double add) {
            return new Criterion(d, m, s, a, false, null, factor, add, false, null);
        }

        public static Criterion part(String d, Metric m, String part, Agg a, double v, boolean required) {
            return new Criterion(d, m, Scope.PART, a, true, v, 0, 0, required, part);
        }

        public Criterion optional() {
            return new Criterion(description, metric, scope, agg, gte, absolute, factor, add, false, part);
        }
    }

    public record Plan(List<Action> actions, Control control, List<Criterion> criteria, List<String> abortExtra,
                       Map<String, Object> parameters) {
    }
}
