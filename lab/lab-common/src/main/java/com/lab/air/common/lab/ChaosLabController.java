package com.lab.air.common.lab;

import com.lab.air.common.Op;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.sql.SQLException;
import java.util.Map;

/**
 * Chaos v2 workload uçları (yalnız lab profili + X-Lab-Token + CIDR allowlist; {@link LabAccessInterceptor}).
 * Keyfi SQL veya keyfi URL almaz; her uç sabit, sınırlı bir workload çalıştırır. Ayrıntı: {@link ChaosWorkloads}.
 */
@RestController
@RequestMapping("/lab/chaos")
public class ChaosLabController {

    private final ChaosWorkloads w;

    public ChaosLabController(ChaosWorkloads w) {
        this.w = w;
    }

    @Op("lab_chaos_contend")
    @PostMapping("/contend")
    public Map<String, Object> contend(@RequestParam(defaultValue = "10") int records,
                                       @RequestParam(defaultValue = "500") long holdMs,
                                       @RequestParam(defaultValue = "0") int errorPct) throws SQLException {
        return w.contend(records, holdMs, errorPct);
    }

    @Op("lab_chaos_long_tx")
    @PostMapping("/long-tx")
    public Map<String, Object> longTx(@RequestParam(defaultValue = "chaos") String scope,
                                      @RequestParam(required = false) Long flightId,
                                      @RequestParam(defaultValue = "20") int rows,
                                      @RequestParam(defaultValue = "10000") long durationMs,
                                      @RequestParam(defaultValue = "0") int errorPct) throws SQLException {
        return w.longTransaction(scope, flightId, rows, durationMs, errorPct);
    }

    @Op("lab_chaos_cpu_query")
    @PostMapping("/cpu-query")
    public Map<String, Object> cpuQuery(@RequestParam(defaultValue = "200") int work,
                                        @RequestParam(defaultValue = "0") int errorPct) throws SQLException {
        return w.cpuQuery(work, errorPct);
    }

    @Op("lab_chaos_heavy_read")
    @PostMapping("/heavy-read")
    public Map<String, Object> heavyRead(@RequestParam(defaultValue = "50") int scanPct,
                                         @RequestParam(defaultValue = "0") int errorPct) throws SQLException {
        return w.heavyRead(scanPct, errorPct);
    }

    @Op("lab_chaos_temp_spill")
    @PostMapping("/temp-spill")
    public Map<String, Object> tempSpill(@RequestParam(defaultValue = "200") int kRows,
                                         @RequestParam(defaultValue = "0") int errorPct) throws SQLException {
        return w.tempSpill(kRows, errorPct);
    }

    @Op("lab_chaos_write_burst")
    @PostMapping("/write-burst")
    public Map<String, Object> writeBurst(@RequestParam(defaultValue = "500") int rows,
                                          @RequestParam(defaultValue = "256") int payloadBytes,
                                          @RequestParam(defaultValue = "0") int errorPct) throws SQLException {
        return w.writeBurst(rows, payloadBytes, errorPct);
    }

    @Op("lab_chaos_deadlock")
    @PostMapping("/deadlock")
    public Map<String, Object> deadlock(@RequestParam int pair, @RequestParam(defaultValue = "ab") String order,
                                        @RequestParam(defaultValue = "300") long holdMs,
                                        @RequestParam(defaultValue = "1") int retries) throws SQLException {
        return w.deadlock(pair, order, holdMs, retries);
    }

    @Op("lab_chaos_connection_storm")
    @PostMapping("/connection-storm")
    public Map<String, Object> storm(@RequestParam(defaultValue = "10") int count,
                                     @RequestParam(defaultValue = "1000") long holdMs) {
        return w.connectionStorm(count, holdMs);
    }

    @Op("lab_chaos_table_lock")
    @PostMapping("/table-lock")
    public Map<String, Object> tableLock(@RequestParam(defaultValue = "read") String mode,
                                         @RequestParam(defaultValue = "1000") long holdMs) throws SQLException {
        return w.tableLock(mode, holdMs);
    }

    @Op("lab_chaos_serialization")
    @PostMapping("/serialization")
    public Map<String, Object> serialization(@RequestParam(defaultValue = "10") int records,
                                             @RequestParam(defaultValue = "100") long holdMs,
                                             @RequestParam(defaultValue = "1") int retries) throws SQLException {
        return w.serialization(records, holdMs, retries);
    }

    @Op("lab_chaos_commit_storm")
    @PostMapping("/commit-storm")
    public Map<String, Object> commitStorm(@RequestParam(defaultValue = "200") int commits,
                                           @RequestParam(defaultValue = "64") int payloadBytes) throws SQLException {
        return w.commitStorm(commits, payloadBytes);
    }

    @Op("lab_chaos_update_churn")
    @PostMapping("/update-churn")
    public Map<String, Object> updateChurn(@RequestParam(defaultValue = "500") int rows,
                                           @RequestParam(defaultValue = "5") int batches) throws SQLException {
        return w.updateChurn(rows, batches);
    }

    @Op("lab_chaos_advisory_lock")
    @PostMapping("/advisory-lock")
    public Map<String, Object> advisoryLock(@RequestParam(defaultValue = "5") int keys,
                                            @RequestParam(defaultValue = "500") long holdMs) throws SQLException {
        return w.advisoryLock(keys, holdMs);
    }

    @Op("lab_chaos_idle_in_tx")
    @PostMapping("/idle-in-tx")
    public Map<String, Object> idleInTx(@RequestParam(defaultValue = "5") int sessions,
                                        @RequestParam(defaultValue = "10000") long holdMs) {
        return w.idleInTransaction(sessions, holdMs);
    }

    @Op("lab_chaos_parallel_query")
    @PostMapping("/parallel-query")
    public Map<String, Object> parallelQuery(@RequestParam(defaultValue = "2") int workers,
                                             @RequestParam(defaultValue = "50") int scanPct) throws SQLException {
        return w.parallelQuery(workers, scanPct);
    }

    @Op("lab_chaos_point_queries")
    @PostMapping("/point-queries")
    public Map<String, Object> pointQueries(@RequestParam(defaultValue = "500") int queries) throws SQLException {
        return w.pointQueries(queries);
    }

    @Op("lab_chaos_memory_sort")
    @PostMapping("/memory-sort")
    public Map<String, Object> memorySort(@RequestParam(defaultValue = "32") int workMemMb,
                                          @RequestParam(defaultValue = "200") int kRows) throws SQLException {
        return w.memorySort(workMemMb, kRows);
    }

    @Op("lab_chaos_cpu_burn")
    @PostMapping("/cpu-burn")
    public Map<String, Object> cpuBurn(@RequestParam(defaultValue = "2000") long ms,
                                       @RequestParam(defaultValue = "1") int threads) {
        return w.cpuBurn(ms, threads);
    }

    @Op("lab_chaos_memory")
    @PostMapping("/memory")
    public Map<String, Object> memory(@RequestParam(defaultValue = "32") int mb,
                                      @RequestParam(defaultValue = "30000") long holdMs) {
        return w.memory(mb, holdMs);
    }

    @Op("lab_chaos_dependency_call")
    @PostMapping("/dependency-call")
    public Map<String, Object> dependencyCall(@RequestParam String peer,
                                              @RequestParam(defaultValue = "1000") int timeoutMs,
                                              @RequestParam(defaultValue = "0") int retries,
                                              @RequestParam(defaultValue = "0") long backoffMs) {
        return w.dependencyCall(peer, timeoutMs, retries, backoffMs);
    }

    @Op("lab_chaos_status")
    @GetMapping("/status")
    public Map<String, Object> status() {
        return w.status();
    }
}
