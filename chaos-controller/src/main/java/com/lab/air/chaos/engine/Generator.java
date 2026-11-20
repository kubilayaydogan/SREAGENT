package com.lab.air.chaos.engine;

import com.lab.air.chaos.ops.HttpCall;
import com.lab.air.chaos.ops.OpDef;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.LockSupport;
import java.util.function.Supplier;

/**
 * Yük üreticisi. İki mod BİLİNÇLİ olarak ayrıdır:
 * <ul>
 *   <li><b>RPS (açık döngü)</b>: hedef hıza göre istek üretir; sunucu yavaşlarsa üretim yavaşlamaz.
 *       Eşzamanlı istek maxInFlight'a ulaşınca yeni istek KUYRUĞA ALINMAZ, "dropped" sayılır.
 *       Böylece hedef / gönderilen / tamamlanan farkı görünür kalır ve üretici belleği sınırlıdır.</li>
 *   <li><b>CONCURRENCY (kapalı döngü)</b>: N işçi, her biri yanıtı bekleyip yeniden gönderir.
 *       Sunucu yavaşladıkça gerçekleşen RPS düşer (coordinated omission'a açıktır; bilinçli).</li>
 * </ul>
 */
public abstract class Generator {

    protected final String app;
    protected final String source;
    protected final List<OpDef> ops = new ArrayList<>();
    protected final int[] cumulative;
    protected final int totalWeight;
    protected final OpDef.OpContext ctx;
    protected final Sender sender;
    protected final Stats stats;
    protected final Supplier<Phase> phase;
    protected final int timeoutMs;
    protected final int rampUpSec;
    protected final AtomicInteger inFlight = new AtomicInteger();
    protected volatile boolean running;
    protected final List<Thread> threads = new ArrayList<>();

    protected Generator(String app, String source, List<Map.Entry<OpDef, Integer>> weighted, OpDef.OpContext ctx,
                        Sender sender, Stats stats, Supplier<Phase> phase, int timeoutMs, int rampUpSec) {
        this.app = app;
        this.source = source;
        this.ctx = ctx;
        this.sender = sender;
        this.stats = stats;
        this.phase = phase;
        this.timeoutMs = timeoutMs;
        this.rampUpSec = rampUpSec;
        this.cumulative = new int[weighted.size()];
        int sum = 0;
        for (int i = 0; i < weighted.size(); i++) {
            ops.add(weighted.get(i).getKey());
            sum += weighted.get(i).getValue();
            cumulative[i] = sum;
        }
        this.totalWeight = sum;
    }

    public abstract void start();

    /**
     * Yeni istek üretimini durdurur. Uçuştaki istekler kesilmez (yarıda kesilen HTTP isteği
     * sunucudaki DB işini durdurmaz); her biri timeoutMs ile sınırlıdır ve sonuçları sayılır.
     */
    public void stop() {
        running = false;
    }

    public int inFlight() {
        return inFlight.get();
    }

    public String app() {
        return app;
    }

    public String source() {
        return source;
    }

    protected OpDef pick() {
        int r = ThreadLocalRandom.current().nextInt(totalWeight);
        for (int i = 0; i < cumulative.length; i++) {
            if (r < cumulative[i]) {
                return ops.get(i);
            }
        }
        return ops.getLast();
    }

    /** Açık döngü, hedef RPS + doğrusal ramp-up. */
    public static final class Rps extends Generator {
        private final double rps;
        private final int maxInFlight;
        private final ExecutorService executor;

        public Rps(String app, String source, List<Map.Entry<OpDef, Integer>> weighted, OpDef.OpContext ctx,
                   Sender sender, Stats stats, Supplier<Phase> phase, int timeoutMs, int rampUpSec, double rps,
                   int maxInFlight, ExecutorService executor) {
            super(app, source, weighted, ctx, sender, stats, phase, timeoutMs, rampUpSec);
            this.rps = rps;
            this.maxInFlight = maxInFlight;
            this.executor = executor;
        }

        public double targetRps() {
            return rps;
        }

        @Override
        public void start() {
            running = true;
            Thread t = Thread.ofVirtual().name("rps-" + source + "-" + app).start(this::loop);
            threads.add(t);
        }

        private void loop() {
            long t0 = System.nanoTime();
            long last = t0;
            double credits = 0;
            while (running) {
                long now = System.nanoTime();
                double elapsed = (now - t0) / 1e9;
                double rate = rampUpSec > 0 ? rps * Math.min(1.0, elapsed / rampUpSec) : rps;
                credits += rate * (now - last) / 1e9;
                credits = Math.min(credits, Math.max(1.0, rate));   // duraklama sonrası patlamayı engelle
                last = now;
                while (credits >= 1.0 && running) {
                    credits -= 1.0;
                    fire();
                }
                LockSupport.parkNanos(2_000_000);
            }
        }

        private void fire() {
            Phase p = phase.get();
            OpDef op = pick();
            stats.intended(p, source, app, op.name());
            HttpCall call = op.builder().build(ctx);
            if (call == null) {
                stats.noData(p, source, app, op.name());
                return;
            }
            if (inFlight.incrementAndGet() > maxInFlight) {
                inFlight.decrementAndGet();
                stats.dropped(p, source, app, op.name());
                return;
            }
            try {
                executor.execute(() -> {
                    try {
                        sender.send(op, call, ctx, p, source, timeoutMs);
                    } finally {
                        inFlight.decrementAndGet();
                    }
                });
            } catch (RuntimeException rejected) {
                inFlight.decrementAndGet();
                stats.dropped(p, source, app, op.name());
            }
        }
    }

    /** Kapalı döngü, N işçi; ramp-up süresince işçiler kademeli başlar. */
    public static final class Concurrency extends Generator {
        private final int workers;

        public Concurrency(String app, String source, List<Map.Entry<OpDef, Integer>> weighted, OpDef.OpContext ctx,
                           Sender sender, Stats stats, Supplier<Phase> phase, int timeoutMs, int rampUpSec, int workers) {
            super(app, source, weighted, ctx, sender, stats, phase, timeoutMs, rampUpSec);
            this.workers = workers;
        }

        public int workers() {
            return workers;
        }

        @Override
        public void start() {
            running = true;
            long t0 = System.nanoTime();
            for (int i = 0; i < workers; i++) {
                long delayNs = rampUpSec > 0 ? (long) (rampUpSec * 1e9 * i / workers) : 0;
                threads.add(Thread.ofVirtual().name("conc-" + source + "-" + app + "-" + i).start(() -> worker(t0 + delayNs)));
            }
        }

        private void worker(long startAt) {
            while (running && System.nanoTime() < startAt) {
                LockSupport.parkNanos(10_000_000);
            }
            while (running) {
                Phase p = phase.get();
                OpDef op = pick();
                stats.intended(p, source, app, op.name());
                HttpCall call = op.builder().build(ctx);
                if (call == null) {
                    stats.noData(p, source, app, op.name());
                    LockSupport.parkNanos(100_000_000);
                    continue;
                }
                inFlight.incrementAndGet();
                try {
                    sender.send(op, call, ctx, p, source, timeoutMs);
                } finally {
                    inFlight.decrementAndGet();
                }
            }
        }
    }
}
