package com.lab.air.chaos.v2.model;

import java.util.List;
import java.util.Map;

/** Chaos v2 veri modeli (JSON API + kalıcı kayıt). */
public final class Model {

    private Model() {
    }

    public enum Intensity {
        LIGHT, MEDIUM, HIGH, CRITICAL, CUSTOM;

        /** Kalibrasyon tablolarındaki indeks (CUSTOM → MEDIUM tabanı + kullanıcı değerleri). */
        public int idx() {
            return this == CUSTOM ? 1 : ordinal();
        }
    }

    public enum State {
        CREATED, VALIDATING, BASELINING, RAMPING_UP, RUNNING, RECOVERING, COMPLETED, FAILED, ABORTED, TIMED_OUT;

        public boolean terminal() {
            return this == COMPLETED || this == FAILED || this == ABORTED || this == TIMED_OUT;
        }

        /** Workload'ın aktif olduğu (ya da olabileceği) durumlar: build/restart yapılmamalı. */
        public boolean loadActive() {
            return this == BASELINING || this == RAMPING_UP || this == RUNNING || this == RECOVERING
                    || this == VALIDATING;
        }
    }

    /** Hedef seçimi. AUTO: senaryonun doğal hedefleri (ör. paylaşılan DB'yi kullanan diğer tüm uygulamalar). */
    public enum TargetMode { SINGLE, MULTI, ALL, SHARED_DB, RANDOM, AUTO }

    /**
     * Custom yoğunluk parametreleri. Null alan → kalibre edilmiş varsayılan. Hepsi sunucu tarafında güvenlik
     * limitleriyle tekrar kırpılır.
     */
    public record Custom(Integer workers, Double rps, Integer dbConnections, Integer txDurationMs,
                         Double queryFrequency, Double targetValue, Integer errorPct, Integer networkLatencyMs,
                         Double packetLossPct, Integer maxAffectedServices, Budget budget) {
        public static Custom none() {
            return new Custom(null, null, null, null, null, null, null, null, null, null, null);
        }
    }

    /** Resource budget: controller bu değerleri hedeflemez; aşılırsa (3 örnek üst üste) deney ABORTED olur. */
    public record Budget(Double maxDbCpuPct, Double maxDbConnectionsPct, Double maxAppCpuPct, Double maxAppHeapPct,
                         Double maxDbMemPct) {
        public static Budget defaults() {
            return new Budget(92.0, 90.0, 100.0, 92.0, 95.0);   // app CPU %100 = kapalı (yük DB'dedir; uygulama CPU'su sınırlanmaz)
        }
    }

    /** Arka plan kullanıcı trafiği (ContinuousTraffic'e EK, deneye özgü). */
    public record Traffic(boolean enabled, List<String> apps, double rpsPerApp, int users, double readWriteRatio,
                          int timeoutMs, int retries, int retryBackoffMs, int thinkTimeMs, double maxTotalRps,
                          boolean hotRows) {
        public static Traffic defaults() {
            return new Traffic(true, List.of(), 6, 8, 0.8, 3000, 1, 100, 50, 200, false);
        }
    }

    public record Durations(int baselineSec, int rampSec, int durationSec, int recoverySec, int maxDurationSec) {
        public static Durations defaults() {
            return new Durations(180, 30, 120, 90, 0);  // baseline ≥ 3 dk: Dynatrace 1 dk çözünürlükte agent baseline penceresi çıkabilsin
        }

        public int plannedTotal() {
            return baselineSec + rampSec + durationSec + recoverySec;
        }
    }

    public record Request(String scenarioId, String initiator, TargetMode targetMode, List<String> targets,
                          Intensity intensity, Custom custom, Durations durations, Traffic traffic, Long seed,
                          String label, String clientRequestId, String randomPlanId, List<String> subScenarios,
                          Integer cooldownSec) {

        /** Paralel rastgele mod: birden fazla senaryo tek deneyde aynı anda. */
        public static final String COMPOSITE = "COMPOSITE";

        public Request withClientRequestId(String id) {
            return new Request(scenarioId, initiator, targetMode, targets, intensity, custom, durations, traffic, seed,
                    label, id, randomPlanId, subScenarios, cooldownSec);
        }
    }

    /** Rastgele mod planı: aynı seed → aynı senaryo/uygulama/yoğunluk dizisi (tekrarlanabilir). */
    public record RandomPlan(Long seed, List<String> allowedScenarios, List<String> excludedScenarios,
                             List<String> allowedApps, List<String> excludedApps, Intensity maxIntensity,
                             int count, boolean parallel, int cooldownSec, Durations durations, Traffic traffic) {
    }

    public record Transition(State state, String at, long monoMs, String reason) {
    }

    public record LogLine(String at, String level, String message) {
    }

    // ----------------------------------------------------------------- canlı örnek (sample)

    /** Uygulama başına ölçüm (actuator/prometheus). Ölçülemeyen değer null kalır — asla uydurulmaz. */
    public record AppSample(boolean up, Double cpuPct, Double heapUsedMb, Double heapMaxMb, Integer poolActive,
                            Integer poolIdle, Integer poolPending, Integer poolMax, Double rps, Double errorPct,
                            Double p95Ms, Double p99Ms, Integer dbConnections, Integer dbLockWaits,
                            Double heapLiveMb, Double processStartSec) {
        /** used / max: toplanmamış garbage dahil (G1 bu değeri doğal olarak max'a yaklaştırır) → baskı göstergesi. */
        public Double heapPct() {
            return heapUsedMb == null || heapMaxMb == null || heapMaxMb <= 0 ? null : 100 * heapUsedMb / heapMaxMb;
        }

        /**
         * Son GC sonrası live set / max (jvm_gc_live_data_size_bytes). OOM riski bu değere bağlıdır; safety budget
         * bununla değerlendirilir. Ölçülemezse heapPct'e düşer.
         */
        public Double heapLivePct() {
            return heapLiveMb == null || heapLiveMb <= 0 || heapMaxMb == null || heapMaxMb <= 0 ? heapPct() : 100 * heapLiveMb / heapMaxMb;
        }

        public Double poolUsagePct() {
            return poolActive == null || poolMax == null || poolMax <= 0 ? null : 100.0 * poolActive / poolMax;
        }

        /** (active + pending) / max: havuz talebi; 100'ün üstü = bekleyen istek var. */
        public Double poolDemandPct() {
            return poolActive == null || poolMax == null || poolMax <= 0 ? null
                    : 100.0 * (poolActive + (poolPending == null ? 0 : poolPending)) / poolMax;
        }
    }

    public record DbSample(boolean up, Double cpuPct, Double memUsedPct, Double load1, Integer cpus,
                           Integer connections, Integer maxConnections, Integer active, Integer idleInTx,
                           Integer lockWaiters, Double longestXactSec, Double tps, Double deadlocksPerMin,
                           Double tempMbPerSec, Double readMbPerSec, Double writeMbPerSec, Double insertsPerSec,
                           Double queryMeanMs, Double dbSizeMb, Double updatesPerSec, Double deadTuples,
                           Integer parallelWorkers, Double rollbacksPerSec) {
        public Double connectionsPct() {
            return connections == null || maxConnections == null || maxConnections <= 0 ? null
                    : 100.0 * connections / maxConnections;
        }
    }

    public record HostSample(Double cpuPct, Double memUsedPct, Double diskFreeGb) {
    }

    /** Workload'ın kendi gözlemi (controller → uygulama çağrıları): istemci tarafı gecikme / hata / retry. */
    public record WorkloadSample(String part, int workers, double callsPerSec, double errorPct, Double p95Ms,
                                 double amplification, Double controlValue, Double controlTarget) {
    }

    public record Sample(String at, long tMs, State phase, Map<String, AppSample> apps, DbSample db,
                         HostSample appVm, List<WorkloadSample> workloads, Map<String, Object> traffic) {
    }
}
