package com.lab.air.chaos.v2.engine;

import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * İstemci tarafı (workload / trafik) çağrı istatistikleri: 10 s kayan pencere + kümülatif toplamlar.
 * amplification = toplam deneme / çağrı (retry'lar dahil).
 */
public final class WindowStats {

    private record Ev(long tMs, long micros, boolean ok, int attempts) {
    }

    private final long windowMs;
    private final Deque<Ev> events = new ArrayDeque<>();
    private long totalCalls;
    private long totalErrors;
    private long totalAttempts;
    private final Map<String, Long> statusCounts = new LinkedHashMap<>();

    public WindowStats(long windowMs) {
        this.windowMs = windowMs;
    }

    public synchronized void record(long nowMs, long micros, boolean ok, int attempts, String statusKey) {
        events.addLast(new Ev(nowMs, micros, ok, Math.max(1, attempts)));
        totalCalls++;
        totalAttempts += Math.max(1, attempts);
        if (!ok) {
            totalErrors++;
        }
        statusCounts.merge(statusKey, 1L, Long::sum);
        trim(nowMs);
    }

    private void trim(long nowMs) {
        while (!events.isEmpty() && events.peekFirst().tMs < nowMs - windowMs) {
            events.removeFirst();
        }
        while (events.size() > 50_000) {
            events.removeFirst();
        }
    }

    public record Snapshot(double callsPerSec, double errorPct, Double p95Ms, double amplification, long totalCalls,
                           long totalErrors, long totalAttempts, Map<String, Long> statusCounts) {
    }

    public synchronized Snapshot snapshot(long nowMs) {
        trim(nowMs);
        int n = events.size();
        double sec = windowMs / 1000.0;
        if (n == 0) {
            return new Snapshot(0, 0, null, 1, totalCalls, totalErrors, totalAttempts, Map.copyOf(statusCounts));
        }
        long[] lat = new long[n];
        int i = 0;
        int err = 0;
        long attempts = 0;
        for (Ev e : events) {
            lat[i++] = e.micros;
            if (!e.ok) {
                err++;
            }
            attempts += e.attempts;
        }
        Arrays.sort(lat);
        double p95 = lat[Math.min(n - 1, (int) Math.ceil(0.95 * n) - 1)] / 1000.0;
        return new Snapshot(n / sec, 100.0 * err / n, p95, (double) attempts / n, totalCalls, totalErrors,
                totalAttempts, Map.copyOf(statusCounts));
    }
}
