package com.lab.air.chaos.engine;

import org.HdrHistogram.ConcurrentHistogram;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.LongAdder;

/**
 * Faz × kaynak (target/background) × uygulama × operasyon bazında sayaçlar.
 * intended = hedef hıza göre üretilmesi gereken istek; sent = gerçekten gönderilen;
 * dropped = maxInFlight dolu olduğu için gönderilemeyen; noData = havuzda veri yok.
 * Gecikme yalnız yanıt alınan isteklerden hesaplanır (timeout'lar ayrı sayılır).
 */
public final class Stats {

    public enum Outcome { OK, CONFLICT, CLIENT_ERROR, SERVER_ERROR, TIMEOUT, IO_ERROR }

    public record Key(Phase phase, String source, String app, String op) {
    }

    public static final class Cell {
        final LongAdder intended = new LongAdder();
        final LongAdder sent = new LongAdder();
        final LongAdder dropped = new LongAdder();
        final LongAdder noData = new LongAdder();
        final LongAdder ok = new LongAdder();
        final LongAdder conflict = new LongAdder();
        final LongAdder clientError = new LongAdder();
        final LongAdder serverError = new LongAdder();
        final LongAdder timeout = new LongAdder();
        final LongAdder ioError = new LongAdder();
        /** mikro saniye, 1µs..300s, 2 anlamlı basamak */
        final ConcurrentHistogram latencyUs = new ConcurrentHistogram(1, 300_000_000L, 2);
    }

    /** Kalıcı sonuç satırı (JSON'a yazılır). */
    public record Row(Phase phase, String source, String app, String op, long intended, long sent, long completed,
                      long ok, long conflict, long clientError, long serverError, long timeout, long ioError,
                      long dropped, long noData, double p50Ms, double p95Ms, double p99Ms, double maxMs,
                      double meanMs, double achievedRps, double errorRate) {
    }

    private final Map<Key, Cell> cells = new ConcurrentHashMap<>();

    Cell cell(Phase p, String source, String app, String op) {
        return cells.computeIfAbsent(new Key(p, source, app, op), k -> new Cell());
    }

    public void intended(Phase p, String s, String a, String o) {
        cell(p, s, a, o).intended.increment();
    }

    public void dropped(Phase p, String s, String a, String o) {
        cell(p, s, a, o).dropped.increment();
    }

    public void noData(Phase p, String s, String a, String o) {
        cell(p, s, a, o).noData.increment();
    }

    public void sent(Phase p, String s, String a, String o) {
        cell(p, s, a, o).sent.increment();
    }

    public void completed(Phase p, String s, String a, String o, Outcome outcome, long latencyUs) {
        Cell c = cell(p, s, a, o);
        switch (outcome) {
            case OK -> c.ok.increment();
            case CONFLICT -> c.conflict.increment();
            case CLIENT_ERROR -> c.clientError.increment();
            case SERVER_ERROR -> c.serverError.increment();
            case TIMEOUT -> c.timeout.increment();
            case IO_ERROR -> c.ioError.increment();
        }
        if (outcome != Outcome.TIMEOUT && outcome != Outcome.IO_ERROR) {
            c.latencyUs.recordValue(Math.max(1, Math.min(latencyUs, 300_000_000L)));
        }
    }

    /** @param phaseSeconds faz başına gerçekleşen süre (achievedRps paydası) */
    public List<Row> rows(Map<Phase, Double> phaseSeconds) {
        List<Row> out = new ArrayList<>();
        cells.forEach((k, c) -> {
            long completedResp = c.ok.sum() + c.conflict.sum() + c.clientError.sum() + c.serverError.sum();
            long errors = c.serverError.sum() + c.timeout.sum() + c.ioError.sum();
            long finished = completedResp + c.timeout.sum() + c.ioError.sum();
            double secs = phaseSeconds.getOrDefault(k.phase(), 0.0);
            var h = c.latencyUs;
            boolean has = h.getTotalCount() > 0;
            out.add(new Row(k.phase(), k.source(), k.app(), k.op(), c.intended.sum(), c.sent.sum(), completedResp,
                    c.ok.sum(), c.conflict.sum(), c.clientError.sum(), c.serverError.sum(), c.timeout.sum(),
                    c.ioError.sum(), c.dropped.sum(), c.noData.sum(),
                    has ? h.getValueAtPercentile(50) / 1000.0 : 0, has ? h.getValueAtPercentile(95) / 1000.0 : 0,
                    has ? h.getValueAtPercentile(99) / 1000.0 : 0, has ? h.getMaxValue() / 1000.0 : 0,
                    has ? Math.round(h.getMean()) / 1000.0 : 0,
                    secs > 0 ? Math.round(completedResp / secs * 100) / 100.0 : 0,
                    finished > 0 ? Math.round(errors * 10000.0 / finished) / 10000.0 : 0));
        });
        out.sort(Comparator.comparing(Row::phase).thenComparing(Row::source).thenComparing(Row::app).thenComparing(Row::op));
        return out;
    }

    /** Canlı panel için: belirli faz + kaynak + uygulama toplamları. */
    public long[] totals(Phase p, String source, String app) {
        long[] t = new long[6]; // intended, sent, completed, errors, dropped, noData
        cells.forEach((k, c) -> {
            if (k.phase() == p && k.source().equals(source) && k.app().equals(app)) {
                t[0] += c.intended.sum();
                t[1] += c.sent.sum();
                t[2] += c.ok.sum() + c.conflict.sum() + c.clientError.sum() + c.serverError.sum();
                t[3] += c.serverError.sum() + c.timeout.sum() + c.ioError.sum();
                t[4] += c.dropped.sum();
                t[5] += c.noData.sum();
            }
        });
        return t;
    }
}
