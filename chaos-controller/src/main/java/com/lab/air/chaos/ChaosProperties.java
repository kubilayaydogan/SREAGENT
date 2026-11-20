package com.lab.air.chaos;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.Map;

/**
 * @param apps     uygulama adı → temel URL (reservation, checkin, flightops, baggage)
 * @param labToken iş uygulamalarının /lab/** uçları için X-Lab-Token
 * @param dataDir  deney kayıtlarının (JSON) yazıldığı dizin (Docker volume)
 * @param limits   sunucu tarafı üst sınırlar (UI/API ne isterse istesin aşılamaz)
 * @param auth     panel/API için HTTP Basic kimlik bilgileri
 * @param background varsayılan normal arka plan trafiği (uygulama başına RPS)
 */
@ConfigurationProperties(prefix = "chaos")
public record ChaosProperties(Map<String, String> apps, String labToken, String dataDir, Limits limits, Auth auth,
                              double background) {

    public ChaosProperties {
        if (apps == null || apps.isEmpty()) {
            apps = Map.of("reservation", "http://localhost:8081", "checkin", "http://localhost:8082",
                    "flightops", "http://localhost:8083", "baggage", "http://localhost:8084");
        }
        if (dataDir == null || dataDir.isBlank()) {
            dataDir = "./data/experiments";
        }
        if (limits == null) {
            limits = new Limits(0, 0, 0, 0, 0, 0, 0, 0);
        }
        if (auth == null) {
            auth = new Auth(false, "chaos", "");
        }
        if (background <= 0) {
            background = 1.0;
        }
    }

    public record Limits(double maxTotalRps, double maxRpsPerTarget, int maxConcurrencyPerTarget,
                         int maxTotalConcurrency, int maxInFlightPerTarget, int maxDurationSec, long maxHoldMs,
                         int maxTimeoutMs) {
        public Limits {
            maxTotalRps = maxTotalRps <= 0 ? 250 : maxTotalRps;
            maxRpsPerTarget = maxRpsPerTarget <= 0 ? 150 : maxRpsPerTarget;
            maxConcurrencyPerTarget = maxConcurrencyPerTarget <= 0 ? 64 : maxConcurrencyPerTarget;
            maxTotalConcurrency = maxTotalConcurrency <= 0 ? 128 : maxTotalConcurrency;
            maxInFlightPerTarget = maxInFlightPerTarget <= 0 ? 256 : maxInFlightPerTarget;
            maxDurationSec = maxDurationSec <= 0 ? 3600 : maxDurationSec;
            maxHoldMs = maxHoldMs <= 0 ? 5000 : maxHoldMs;
            maxTimeoutMs = maxTimeoutMs <= 0 ? 30000 : maxTimeoutMs;
        }
    }

    public record Auth(boolean enabled, String user, String password) {
    }
}
