package com.lab.air.chaos.v2.engine;

import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.ops.DataPools;
import com.lab.air.chaos.ops.HttpCall;
import com.lab.air.chaos.ops.OpDef;
import com.lab.air.chaos.v2.model.Model.WorkloadSample;
import com.lab.air.chaos.v2.scenario.ScenarioDef.Action;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.LongSupplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Senaryo eylemlerinin çalışan karşılıkları. Hepsi gerçek HTTP çağrısı yapar; level ∈ [0,1] yoğunluğu ölçekler. */
public final class Actuators {

    private static final Logger log = LoggerFactory.getLogger(Actuators.class);
    private static final Pattern ATTEMPTS = Pattern.compile("\"attempts\"\\s*:\\s*(\\d+)|(\\d+) denemede");
    private static final Pattern PAIR = Pattern.compile("\\{pair:(\\d+)}");

    private Actuators() {
    }

    public interface Actuator {
        Action action();

        void start();

        /** 0..1; LOOP/TRAFFIC için worker sayısı, NETEM için (ramp) delay/loss oranı. */
        void setLevel(double level);

        double level();

        void stop();

        WorkloadSample sample(long nowMs, Double controlValue, Double controlTarget);

        /** Deney sonu raporu için kümülatif özet. */
        Map<String, Object> summary(long nowMs);
    }

    static int workersFor(Action a, double level) {
        return (int) Math.round(a.minWorkers() + Math.max(0, Math.min(1, level)) * (a.maxWorkers() - a.minWorkers()));
    }

    // ------------------------------------------------------------------ LOOP

    /** N sanal thread aynı lab ucunu çağırır; worker sayısı level ile ayarlanır. */
    public static final class Loop implements Actuator {
        private final Action a;
        private final ChaosHttp http;
        private final ChaosHttp.Correlation corr;
        private final DataPools pools;
        private final LongSupplier clock;
        private final WindowStats stats = new WindowStats(10_000);
        private final List<AtomicBoolean> workers = new ArrayList<>();
        private final AtomicBoolean running = new AtomicBoolean();
        private volatile double level;

        public Loop(Action a, ChaosHttp http, ChaosHttp.Correlation corr, DataPools pools, LongSupplier clock) {
            this.a = a;
            this.http = http;
            this.corr = corr;
            this.pools = pools;
            this.clock = clock;
        }

        @Override
        public Action action() {
            return a;
        }

        @Override
        public void start() {
            running.set(true);
            setLevel(level);
        }

        @Override
        public synchronized void setLevel(double lv) {
            level = Math.max(0, Math.min(1, lv));
            if (!running.get()) {
                return;
            }
            int want = workersFor(a, level);
            while (workers.size() < want) {
                AtomicBoolean stop = new AtomicBoolean();
                workers.add(stop);
                int idx = workers.size();
                Thread.ofVirtual().name("v2-" + a.part() + "-" + idx).start(() -> loop(stop));
            }
            while (workers.size() > want) {
                workers.removeLast().set(true);
            }
        }

        @Override
        public double level() {
            return level;
        }

        public synchronized int workers() {
            return workers.size();
        }

        @Override
        public synchronized void stop() {
            running.set(false);
            workers.forEach(w -> w.set(true));
            workers.clear();
        }

        String resolvePath() {
            StringBuilder q = new StringBuilder(a.path()).append('?');
            boolean first = true;
            for (var e : a.params().entrySet()) {
                String v = e.getValue();
                if (v.equals("{hotFlight}")) {
                    Long f = pools == null ? null : pools.hotFlights.peek();
                    if (f == null) {
                        return null;
                    }
                    v = f.toString();
                } else {
                    Matcher m = PAIR.matcher(v);
                    if (m.matches()) {
                        v = "" + ThreadLocalRandom.current().nextInt(Integer.parseInt(m.group(1)));
                    }
                }
                if (!first) {
                    q.append('&');
                }
                q.append(e.getKey()).append('=').append(java.net.URLEncoder.encode(v, java.nio.charset.StandardCharsets.UTF_8));
                first = false;
            }
            return q.toString();
        }

        private void loop(AtomicBoolean stop) {
            long intervalNs = a.ratePerWorker() > 0 ? (long) (1e9 / a.ratePerWorker()) : 0;
            while (!stop.get() && running.get()) {
                long t0 = System.nanoTime();
                String path = resolvePath();
                if (path == null) {
                    sleep(500);            // veri havuzu henüz dolmadı (ör. sıcak uçuş listesi)
                    continue;
                }
                ChaosHttp.Result r = http.call(a.app(), "POST", path, null, corr, "workload", 130_000, true);
                stats.record(clock.getAsLong(), r.micros(), r.ok(), attempts(r), key(r));
                if (r.status() == 429) {
                    sleep(250);            // uygulama tarafı chaos op limiti: geri çekil
                } else if (r.status() < 0 && !r.timeout()) {
                    sleep(300);            // bağlantı hatası: sıcak döngüye girme
                }
                if (intervalNs > 0) {
                    long left = intervalNs - (System.nanoTime() - t0);
                    if (left > 0) {
                        sleep(left / 1_000_000);
                    }
                }
            }
        }

        @Override
        public WorkloadSample sample(long nowMs, Double cv, Double ct) {
            var s = stats.snapshot(nowMs);
            return new WorkloadSample(a.part(), workers(), s.callsPerSec(), s.errorPct(), s.p95Ms(), s.amplification(),
                    cv, ct);
        }

        @Override
        public Map<String, Object> summary(long nowMs) {
            var s = stats.snapshot(nowMs);
            return Map.of("part", a.part(), "kind", "LOOP", "app", a.app(), "path", a.path(), "totalCalls", s.totalCalls(),
                    "totalErrors", s.totalErrors(), "totalAttempts", s.totalAttempts(), "status", s.statusCounts());
        }
    }

    static int attempts(ChaosHttp.Result r) {
        if (r.body() == null) {
            return 1;
        }
        Matcher m = ATTEMPTS.matcher(r.body());
        if (m.find()) {
            return Integer.parseInt(m.group(1) != null ? m.group(1) : m.group(2));
        }
        return 1;
    }

    static String key(ChaosHttp.Result r) {
        return r.status() > 0 ? String.valueOf(r.status()) : r.timeout() ? "timeout" : String.valueOf(r.error());
    }

    // ------------------------------------------------------------------ NETEM

    /** Sidecar'a tc netem kuralı. level < 1 iken (ramp) delay/loss orantılı uygulanır; TTL periyodik yenilenir. */
    public static final class Netem implements Actuator {
        private final Action a;
        private final ChaosHttp http;
        private final ChaosHttp.Correlation corr;
        private final LongSupplier clock;
        private volatile double level;
        private volatile boolean active;
        private volatile long appliedAt = -1;
        private volatile double appliedLevel = -1;
        private volatile String lastError;
        private int applies;

        public Netem(Action a, ChaosHttp http, ChaosHttp.Correlation corr, LongSupplier clock) {
            this.a = a;
            this.http = http;
            this.corr = corr;
            this.clock = clock;
        }

        @Override
        public Action action() {
            return a;
        }

        @Override
        public void start() {
            active = true;
            apply();
        }

        @Override
        public void setLevel(double lv) {
            level = Math.max(0, Math.min(1, lv));
            long now = clock.getAsLong();
            // seviye değiştiyse ya da TTL'nin yarısı geçtiyse yeniden uygula
            if (active && (Math.abs(level - appliedLevel) >= 0.2 || now - appliedAt > 240_000)) {
                apply();
            }
        }

        @Override
        public double level() {
            return level;
        }

        private synchronized void apply() {
            double lv = Math.max(level, 0.5);       // ramp başında yarım şiddet; sıfır kural anlamsız
            int d = (int) Math.round(a.delayMs() * lv);
            int j = Math.min(d, (int) Math.round(a.jitterMs() * lv));
            double l = Math.round(a.lossPct() * lv * 10) / 10.0;
            if (d == 0 && l == 0) {
                return;
            }
            var r = http.netemApply(a.app(), a.netemTarget(), d, j, l, 600, corr);
            if (r.ok()) {
                appliedAt = clock.getAsLong();
                appliedLevel = level;
                applies++;
                lastError = null;
            } else {
                lastError = r.status() + " " + (r.body() == null ? r.error() : r.body());
                log.warn("netem uygulanamadı app={} target={}: {}", a.app(), a.netemTarget(), lastError);
            }
        }

        public String lastError() {
            return lastError;
        }

        @Override
        public synchronized void stop() {
            active = false;
            var r = http.netemClear(a.app(), corr);
            if (!r.ok()) {
                log.warn("netem temizlenemedi app={}: {} (TTL ile kalkacak)", a.app(), r.status());
            }
        }

        @Override
        public WorkloadSample sample(long nowMs, Double cv, Double ct) {
            return new WorkloadSample(a.part(), active ? 1 : 0, 0, lastError == null ? 0 : 100, null, 1, cv, ct);
        }

        @Override
        public Map<String, Object> summary(long nowMs) {
            return Map.of("part", a.part(), "kind", "NETEM", "app", a.app(), "target", a.netemTarget(),
                    "delayMs", a.delayMs(), "jitterMs", a.jitterMs(), "lossPct", a.lossPct(), "applies", applies,
                    "lastError", lastError == null ? "" : lastError);
        }
    }

    // ------------------------------------------------------------------ TRAFFIC

    /**
     * Gerçek iş trafiği (Catalog'daki normal operasyon karışımı): users sanal kullanıcı, toplam rps tavanı,
     * think time, timeout ve retry politikası. retry sayısı amplification olarak raporlanır.
     */
    public static final class Traffic implements Actuator {
        private final Action a;
        private final ChaosHttp http;
        private final ChaosHttp.Correlation corr;
        private final DataPools pools;
        private final Catalog catalog;
        private final JsonMapper json;
        private final LongSupplier clock;
        private final double readWriteRatio;
        private final int thinkTimeMs;
        private final int retryBackoffMs;
        private final WindowStats stats = new WindowStats(10_000);
        private final List<AtomicBoolean> users = new ArrayList<>();
        private final AtomicBoolean running = new AtomicBoolean();
        private final List<Map.Entry<String, Integer>> mix;
        private final int mixTotal;
        private volatile double level;

        public Traffic(Action a, ChaosHttp http, ChaosHttp.Correlation corr, DataPools pools, Catalog catalog,
                       JsonMapper json, LongSupplier clock, double readWriteRatio, int thinkTimeMs, int retryBackoffMs) {
            this.a = a;
            this.http = http;
            this.corr = corr;
            this.pools = pools;
            this.catalog = catalog;
            this.json = json;
            this.clock = clock;
            this.readWriteRatio = readWriteRatio;
            this.thinkTimeMs = Math.max(0, thinkTimeMs);
            this.retryBackoffMs = Math.max(0, retryBackoffMs);
            this.mix = List.copyOf(catalog.mixFor(a.app(), null, readWriteRatio).entrySet());
            this.mixTotal = mix.stream().mapToInt(Map.Entry::getValue).sum();
            this.level = a.controlled() ? 0 : 1;
        }

        @Override
        public Action action() {
            return a;
        }

        @Override
        public void start() {
            running.set(true);
            setLevel(level);
        }

        @Override
        public synchronized void setLevel(double lv) {
            level = Math.max(0, Math.min(1, lv));
            if (!running.get()) {
                return;
            }
            int want = Math.max(1, workersFor(a, level));
            while (users.size() < want) {
                AtomicBoolean stop = new AtomicBoolean();
                users.add(stop);
                Thread.ofVirtual().name("v2-traffic-" + a.app() + "-" + users.size()).start(() -> user(stop));
            }
            while (users.size() > want) {
                users.removeLast().set(true);
            }
        }

        @Override
        public double level() {
            return level;
        }

        synchronized int userCount() {
            return users.size();
        }

        @Override
        public synchronized void stop() {
            running.set(false);
            users.forEach(u -> u.set(true));
            users.clear();
        }

        private OpDef pickOp(Random r) {
            if (mixTotal <= 0) {
                return null;
            }
            int x = r.nextInt(mixTotal);
            for (var e : mix) {
                x -= e.getValue();
                if (x < 0) {
                    return catalog.op(a.app(), e.getKey());
                }
            }
            return null;
        }

        private void user(AtomicBoolean stop) {
            var ctx = new OpDef.OpContext(new OpDef.Params(1, a.hotRows(), 0), pools, json);
            Random r = ThreadLocalRandom.current();
            while (!stop.get() && running.get()) {
                long t0 = System.nanoTime();
                OpDef op = pickOp(r);
                HttpCall call = op == null ? null : op.builder().build(ctx);
                if (call == null) {
                    sleep(100);
                    continue;
                }
                int attempts = 0;
                ChaosHttp.Result res;
                long start = System.nanoTime();
                do {
                    attempts++;
                    res = http.call(a.app(), call.method(), call.path(), call.jsonBody(), corr, "traffic",
                            a.trafficTimeoutMs(), call.lab(), call.headers());
                    boolean retryable = res.timeout() || res.status() >= 500 || res.status() < 0;
                    if (!retryable || attempts > a.trafficRetries()) {
                        break;
                    }
                    sleep((long) retryBackoffMs * attempts);
                } while (!stop.get());
                long micros = (System.nanoTime() - start) / 1000;
                boolean ok = res.status() > 0 && res.status() < 500;      // 4xx (ör. SOLD_OUT) iş kuralı, hata değil
                stats.record(clock.getAsLong(), micros, ok, attempts, key(res));
                if (op.onResult() != null && res.status() > 0) {
                    try {
                        op.onResult().accept(ctx, call, res.status(), res.body());
                    } catch (RuntimeException ignored) {
                        // havuz geri beslemesi en iyi çaba
                    }
                }
                // tempo: kullanıcı başına rps payı + think time
                int n = Math.max(1, userCount());
                long perUserNs = a.trafficRps() > 0 ? (long) (1e9 * n / a.trafficRps()) : 0;
                long elapsed = System.nanoTime() - t0;
                long wait = Math.max(perUserNs - elapsed, 0) / 1_000_000 + thinkTimeMs;
                if (wait > 0) {
                    sleep(wait);
                }
            }
        }

        @Override
        public WorkloadSample sample(long nowMs, Double cv, Double ct) {
            var s = stats.snapshot(nowMs);
            return new WorkloadSample(a.part(), userCount(), s.callsPerSec(), s.errorPct(), s.p95Ms(), s.amplification(),
                    cv, ct);
        }

        @Override
        public Map<String, Object> summary(long nowMs) {
            var s = stats.snapshot(nowMs);
            Map<String, Object> m = new java.util.LinkedHashMap<>();
            m.put("part", a.part());
            m.put("kind", "TRAFFIC");
            m.put("app", a.app());
            m.put("rps", a.trafficRps());
            m.put("readWriteRatio", readWriteRatio);
            m.put("hotRows", a.hotRows());
            m.put("retries", a.trafficRetries());
            m.put("timeoutMs", a.trafficTimeoutMs());
            m.put("totalCalls", s.totalCalls());
            m.put("totalErrors", s.totalErrors());
            m.put("totalAttempts", s.totalAttempts());
            m.put("status", s.statusCounts());
            return m;
        }
    }

    static void sleep(long ms) {
        if (ms <= 0) {
            return;
        }
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
