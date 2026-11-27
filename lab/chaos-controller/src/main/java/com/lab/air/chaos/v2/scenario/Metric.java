package com.lab.air.chaos.v2.scenario;

import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.DbSample;
import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.WorkloadSample;

import java.util.function.BiFunction;
import java.util.function.Function;

/** Kontrol ve başarı ölçütlerinde kullanılan, örnekten (Sample) hesaplanabilen metrikler. */
public enum Metric {
    DB_CPU_PCT("DB_VM CPU %", db(DbSample::cpuPct)),
    DB_MEM_PCT("DB_VM memory %", db(DbSample::memUsedPct)),
    DB_CONNECTIONS_PCT("DB connections / max_connections %", db(DbSample::connectionsPct)),
    DB_ACTIVE("DB active sessions", db(d -> d.active() == null ? null : d.active().doubleValue())),
    LOCK_WAITERS("lock waiters (pg_locks NOT granted)", db(d -> d.lockWaiters() == null ? null : d.lockWaiters().doubleValue())),
    LONGEST_XACT_SEC("longest transaction age (s)", db(DbSample::longestXactSec)),
    IDLE_IN_TX("idle in transaction sessions", db(d -> d.idleInTx() == null ? null : d.idleInTx().doubleValue())),
    PARALLEL_WORKERS("parallel worker processes", db(d -> d.parallelWorkers() == null ? null : d.parallelWorkers().doubleValue())),
    DB_TPS("DB TPS (commit+rollback/s)", db(DbSample::tps)),
    DEADLOCKS_PER_MIN("deadlocks/min", db(DbSample::deadlocksPerMin)),
    ROLLBACKS_PER_SEC("rollbacks/s", db(DbSample::rollbacksPerSec)),
    TEMP_MB_PER_SEC("temp file MB/s", db(DbSample::tempMbPerSec)),
    IO_READ_MB_PER_SEC("I/O read MB/s (pg_stat_io)", db(DbSample::readMbPerSec)),
    IO_WRITE_MB_PER_SEC("I/O write MB/s (pg_stat_io)", db(DbSample::writeMbPerSec)),
    INSERTS_PER_SEC("tuples inserted/s", db(DbSample::insertsPerSec)),
    UPDATES_PER_SEC("tuples updated/s", db(DbSample::updatesPerSec)),
    DEAD_TUPLES("dead tuples (chaos schema)", db(DbSample::deadTuples)),
    QUERY_MEAN_MS("query mean latency ms (pg_stat_statements)", db(DbSample::queryMeanMs)),
    APP_CPU_PCT("app CPU %", app(AppSample::cpuPct)),
    APP_HEAP_PCT("app heap %", app(AppSample::heapPct)),
    APP_POOL_USAGE_PCT("app pool usage %", app(AppSample::poolUsagePct)),
    APP_POOL_DEMAND_PCT("app pool demand % ((active+pending)/max)", app(AppSample::poolDemandPct)),
    APP_POOL_PENDING("app pool pending", app(a -> a.poolPending() == null ? null : a.poolPending().doubleValue())),
    APP_P95_MS("app p95 ms", app(AppSample::p95Ms)),
    APP_P99_MS("app p99 ms", app(AppSample::p99Ms)),
    APP_ERROR_PCT("app error rate %", app(AppSample::errorPct)),
    APP_RPS("app RPS", app(AppSample::rps)),
    APPVM_CPU_PCT("APP_VM CPU %", (s, a) -> s.appVm() == null ? null : s.appVm().cpuPct()),
    APPVM_MEM_PCT("APP_VM memory %", (s, a) -> s.appVm() == null ? null : s.appVm().memUsedPct()),
    // Workload'ın istemci tarafı gözlemi (anahtar = part adı)
    WL_P95_MS("workload client p95 ms", wl(w -> w.p95Ms())),
    WL_ERROR_PCT("workload client error %", wl(w -> w.errorPct())),
    WL_AMPLIFICATION("retry amplification (attempts / calls)", wl(w -> w.amplification())),
    WL_CALLS_PER_SEC("workload calls/s", wl(w -> w.callsPerSec()));

    public final String label;
    private final BiFunction<Sample, String, Double> fn;

    Metric(String label, BiFunction<Sample, String, Double> fn) {
        this.label = label;
        this.fn = fn;
    }

    public boolean perApp() {
        return name().startsWith("APP_");
    }

    public boolean perPart() {
        return name().startsWith("WL_");
    }

    /** app yalnız perApp metriklerde kullanılır; değer yoksa null. */
    public Double value(Sample s, String app) {
        try {
            return fn.apply(s, app);
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static BiFunction<Sample, String, Double> db(Function<DbSample, Double> f) {
        return (s, a) -> s.db() == null || !s.db().up() ? null : f.apply(s.db());
    }

    private static BiFunction<Sample, String, Double> wl(Function<WorkloadSample, Double> f) {
        return (s, part) -> s.workloads() == null ? null : s.workloads().stream()
                .filter(w -> w.part().equals(part)).findFirst().map(f).orElse(null);
    }

    private static BiFunction<Sample, String, Double> app(Function<AppSample, Double> f) {
        return (s, a) -> {
            AppSample x = s.apps() == null ? null : s.apps().get(a);
            return x == null || !x.up() ? null : f.apply(x);
        };
    }
}
