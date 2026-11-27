package com.lab.air.common.lab;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.Map;

/**
 * Chaos v2 workload uçlarının sunucu tarafı sert sınırları. İstemci (chaos controller) ne isterse istesin bu
 * değerler aşılamaz. Ortam değişkeni: LAB_CHAOS_MAX_LONG_TX_MS, LAB_CHAOS_PEERS_RESERVATION=http://reservation:8081 ...
 *
 * @param maxConcurrentOps     uygulama başına eşzamanlı chaos operasyonu (Tomcat thread'lerini tüketmesin)
 * @param maxLongTxMs          long transaction süresi üst sınırı
 * @param maxCpuBurnMs         tek CPU burn çağrısının süresi
 * @param maxCpuThreads        eşzamanlı CPU burn thread'i (0 → availableProcessors)
 * @param maxMemoryFraction    tutulabilecek ballast / max heap oranı
 * @param maxMemoryHoldMs      ballast'ın tutulma süresi üst sınırı
 * @param maxStormConnections  connection storm çağrısı başına yeni fiziksel bağlantı
 * @param maxWriteRows         write burst çağrısı başına satır
 * @param writeSinkKeep        write_sink'te tutulan en fazla satır (aşılınca budanır)
 * @param maxDependencyAttempts dependency call başına toplam deneme (retry dahil)
 * @param peers                uygulama adı → temel URL (dependency call hedefleri; allowlist)
 */
@ConfigurationProperties(prefix = "lab.chaos")
public record ChaosLabProperties(int maxConcurrentOps, long maxLongTxMs, long maxCpuBurnMs, int maxCpuThreads,
                                 double maxMemoryFraction, long maxMemoryHoldMs, int maxStormConnections,
                                 int maxWriteRows, int writeSinkKeep, int maxDependencyAttempts,
                                 Map<String, String> peers) {

    public ChaosLabProperties {
        maxConcurrentOps = maxConcurrentOps <= 0 ? 32 : Math.min(maxConcurrentOps, 45);
        maxLongTxMs = maxLongTxMs <= 0 ? 60_000 : Math.min(maxLongTxMs, 120_000);
        maxCpuBurnMs = maxCpuBurnMs <= 0 ? 30_000 : Math.min(maxCpuBurnMs, 60_000);
        int cpus = Runtime.getRuntime().availableProcessors();
        maxCpuThreads = maxCpuThreads <= 0 ? cpus : Math.min(maxCpuThreads, cpus * 2);
        maxMemoryFraction = maxMemoryFraction <= 0 ? 0.5 : Math.min(maxMemoryFraction, 0.7);
        maxMemoryHoldMs = maxMemoryHoldMs <= 0 ? 120_000 : Math.min(maxMemoryHoldMs, 300_000);
        maxStormConnections = maxStormConnections <= 0 ? 20 : Math.min(maxStormConnections, 30);
        maxWriteRows = maxWriteRows <= 0 ? 5_000 : Math.min(maxWriteRows, 20_000);
        writeSinkKeep = writeSinkKeep <= 0 ? 200_000 : Math.min(writeSinkKeep, 2_000_000);
        maxDependencyAttempts = maxDependencyAttempts <= 0 ? 8 : Math.min(maxDependencyAttempts, 16);
        peers = peers == null ? Map.of() : Map.copyOf(peers);
    }
}
