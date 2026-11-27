package com.lab.air.chaos.v2;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

/**
 * Chaos v2 yapılandırması.
 *
 * @param monitorDbUrl       read-only lab_monitor JDBC URL'i (boşsa DB metrikleri null — uydurulmaz)
 * @param netemPort          netem sidecar portu (uygulama container'ının ağ namespace'inde)
 * @param dtUiUrl            Dynatrace ortam URL'i (link üretimi; boş olabilir)
 * @param sampleIntervalMs   ölçüm periyodu
 * @param allowedApps        chaos uygulanabilecek uygulamalar (allowlist)
 * @param environment        "lab" dışında bir değer → tüm deneyler reddedilir (production koruması)
 * @param maxQueue           kuyruk uzunluğu
 * @param maxDurationSec     tek deneyin mutlak üst süresi
 * @param minAppVmDiskFreeGb APP_VM (chaos-data volume) boş disk alt sınırı
 * @param maxDbGrowthMb      deney boyunca DB boyutu artış sınırı
 */
@ConfigurationProperties(prefix = "chaos.v2")
public record V2Properties(String monitorDbUrl, String monitorDbUser, String monitorDbPassword, int netemPort,
                           String dtUiUrl, long sampleIntervalMs, List<String> allowedApps, String environment,
                           int maxQueue, int maxDurationSec, double minAppVmDiskFreeGb, double maxDbGrowthMb) {

    public V2Properties {
        netemPort = netemPort <= 0 ? 9190 : netemPort;
        sampleIntervalMs = sampleIntervalMs <= 0 ? 2000 : Math.max(1000, sampleIntervalMs);
        allowedApps = allowedApps == null || allowedApps.isEmpty()
                ? List.of("reservation", "checkin", "flightops", "baggage") : List.copyOf(allowedApps);
        environment = environment == null || environment.isBlank() ? "lab" : environment;
        maxQueue = maxQueue <= 0 ? 20 : Math.min(maxQueue, 100);
        maxDurationSec = maxDurationSec <= 0 ? 3600 : Math.min(maxDurationSec, 4 * 3600);
        minAppVmDiskFreeGb = minAppVmDiskFreeGb <= 0 ? 3 : minAppVmDiskFreeGb;
        maxDbGrowthMb = maxDbGrowthMb <= 0 ? 1024 : maxDbGrowthMb;
    }

    public boolean isLab() {
        return "lab".equalsIgnoreCase(environment);
    }
}
