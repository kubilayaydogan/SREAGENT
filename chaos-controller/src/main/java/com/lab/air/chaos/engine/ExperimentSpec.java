package com.lab.air.chaos.engine;

import java.util.List;
import java.util.Map;

/**
 * Deney tanımı. Zaman pencereleri: warm-up → baseline → load → recovery.
 * Arka plan (normal) trafik TÜM fazlarda, seçili uygulamalarda sabit düşük RPS ile sürer;
 * stres hedefleri yalnız LOAD fazında (activePhases=LOAD) veya tüm fazlarda (ALL, "probe") çalışır.
 *
 * @param clientRequestId idempotent başlatma anahtarı (aynı anahtar + aynı tanım → aynı deney)
 * @param scenario        senaryo etiketi (ör. E2-hot-row-lock). DEĞERLENDİRME içindir; nedensellik kanıtı değildir.
 */
public record ExperimentSpec(String clientRequestId, String name, String scenario,
                             int warmupSec, int baselineSec, int loadSec, int recoverySec,
                             Background background, List<TargetSpec> targets) {

    public ExperimentSpec {
        if (background == null) {
            background = new Background(true, 1.0, null);
        }
        if (targets == null) {
            targets = List.of();
        }
        if (name == null || name.isBlank()) {
            name = scenario == null ? "adhoc" : scenario;
        }
    }

    public int totalSec() {
        return warmupSec + baselineSec + loadSec + recoverySec;
    }

    /**
     * @param apps null → dört uygulamanın tamamı
     */
    public record Background(boolean enabled, double rpsPerApp, List<String> apps) {
    }

    public enum Mode { RPS, CONCURRENCY }

    public enum ActivePhases { LOAD, ALL }

    /**
     * @param role           "stress" (kaynak) veya "probe" (etkiyi ölçen düşük hacimli iş akışı)
     * @param mode           RPS: açık döngü (hedef hıza göre gönderir, maxInFlight dolunca DÜŞÜRÜR);
     *                       CONCURRENCY: kapalı döngü (N işçi, her biri yanıtı bekleyip yeniden gönderir)
     * @param maxInFlight    RPS modunda eşzamanlı istek tavanı (kuyruk yok → fazlası "dropped")
     * @param mix            operasyon → ağırlık; boşsa normal karışım (+ readWriteRatio)
     * @param dataSize       1..100; operasyona göre gün penceresi / limit / tarama büyüklüğü çarpanı
     * @param readWriteRatio 0..1 okuma payı (mix boşsa uygulanır)
     * @param hotRow         ortak sıcak uçuş satırını hedefle
     * @param holdMs         lab operasyonlarında kilit/bağlantı tutma süresi (sunucuda da sınırlı)
     */
    public record TargetSpec(String app, String role, Mode mode, double rps, int concurrency, int rampUpSec,
                             int maxInFlight, int timeoutMs, Map<String, Integer> mix, int dataSize,
                             Double readWriteRatio, boolean hotRow, long holdMs, ActivePhases activePhases) {
        public TargetSpec {
            if (role == null || role.isBlank()) {
                role = "stress";
            }
            if (mode == null) {
                mode = Mode.RPS;
            }
            if (maxInFlight <= 0) {
                maxInFlight = 64;
            }
            if (timeoutMs <= 0) {
                timeoutMs = 5000;
            }
            if (dataSize <= 0) {
                dataSize = 1;
            }
            if (activePhases == null) {
                activePhases = ActivePhases.LOAD;
            }
        }
    }
}
