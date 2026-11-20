package com.lab.air.common.lab;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

/**
 * Lab senaryo uçlarının güvenlik ve emniyet sınırları.
 *
 * @param enabled          yalnız "lab" profilinde true
 * @param token            X-Lab-Token başlığında beklenen paylaşılan sır (boşsa uçlar kapalı)
 * @param allowedCidrs     istemci IP allowlist'i (IPv4 CIDR)
 * @param maxHoldMs        tek bir lock/connection tutma süresinin üst sınırı
 * @param pressurePoolMax  global connection baskısı için ayrı "lab-pressure" havuzunun üst sınırı
 * @param maxConcurrentHolds aynı anda süren lab tutma işlemi üst sınırı (uygulama başına)
 */
@ConfigurationProperties(prefix = "lab")
public record LabProperties(boolean enabled, String token, List<String> allowedCidrs, long maxHoldMs,
                            int pressurePoolMax, int maxConcurrentHolds) {

    public LabProperties {
        if (allowedCidrs == null || allowedCidrs.isEmpty()) {
            allowedCidrs = List.of("127.0.0.1/32");
        }
        if (maxHoldMs <= 0) {
            maxHoldMs = 5000;
        }
        maxHoldMs = Math.min(maxHoldMs, 10_000);
        if (pressurePoolMax <= 0) {
            pressurePoolMax = 22;
        }
        pressurePoolMax = Math.min(pressurePoolMax, 30);
        if (maxConcurrentHolds <= 0) {
            maxConcurrentHolds = 40;
        }
    }

    public long capHold(long requested) {
        if (requested < 0) {
            throw new IllegalArgumentException("holdMs negatif olamaz");
        }
        return Math.min(requested, maxHoldMs);
    }
}
