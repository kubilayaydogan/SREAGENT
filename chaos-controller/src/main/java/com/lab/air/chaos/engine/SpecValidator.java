package com.lab.air.chaos.engine;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.engine.ExperimentSpec.Mode;
import com.lab.air.chaos.engine.ExperimentSpec.TargetSpec;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.ops.OpDef;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Sunucu tarafı üst sınırlar ve tutarlılık kontrolleri. UI'dan bağımsız olarak uygulanır. */
public final class SpecValidator {

    static final Pattern CLIENT_ID = Pattern.compile("[A-Za-z0-9._:-]{8,64}");

    private final ChaosProperties.Limits l;
    private final Catalog catalog;
    private final boolean labTokenConfigured;

    public SpecValidator(ChaosProperties.Limits limits, Catalog catalog, boolean labTokenConfigured) {
        this.l = limits;
        this.catalog = catalog;
        this.labTokenConfigured = labTokenConfigured;
    }

    public List<String> validate(ExperimentSpec s) {
        List<String> e = new ArrayList<>();
        if (s.clientRequestId() == null || !CLIENT_ID.matcher(s.clientRequestId()).matches()) {
            e.add("clientRequestId 8-64 karakter [A-Za-z0-9._:-] olmalı (idempotent başlatma anahtarı)");
        }
        if (s.warmupSec() < 0 || s.baselineSec() < 0 || s.loadSec() < 0 || s.recoverySec() < 0) {
            e.add("faz süreleri negatif olamaz");
        }
        if (s.totalSec() <= 0) {
            e.add("toplam süre > 0 olmalı");
        }
        if (s.totalSec() > l.maxDurationSec()) {
            e.add("toplam süre " + s.totalSec() + " sn > üst sınır " + l.maxDurationSec());
        }
        double totalRps = 0;
        int totalConc = 0;
        var bg = s.background();
        if (bg.enabled()) {
            if (bg.rpsPerApp() < 0 || bg.rpsPerApp() > 5) {
                e.add("arka plan RPS 0..5 olmalı (düşük seviyeli normal trafik)");
            }
            List<String> apps = bg.apps() == null || bg.apps().isEmpty() ? Catalog.APPS : bg.apps();
            for (String a : apps) {
                if (!Catalog.APPS.contains(a)) {
                    e.add("bilinmeyen arka plan uygulaması: " + a);
                }
            }
            totalRps += bg.rpsPerApp() * apps.size();
        }
        if (s.targets().size() > 4) {
            e.add("en fazla 4 hedef");
        }
        Set<String> seen = new HashSet<>();
        boolean anyLoadPhase = false;
        for (TargetSpec t : s.targets()) {
            String p = "hedef[" + t.app() + "]: ";
            if (!Catalog.APPS.contains(t.app())) {
                e.add(p + "bilinmeyen uygulama");
                continue;
            }
            if (!seen.add(t.app() + "/" + t.role())) {
                e.add(p + "aynı uygulama+rol iki kez seçilmiş");
            }
            if (!"stress".equals(t.role()) && !"probe".equals(t.role())) {
                e.add(p + "role stress|probe olmalı");
            }
            if (t.activePhases() == ExperimentSpec.ActivePhases.LOAD) {
                anyLoadPhase = true;
            }
            if (t.mode() == Mode.RPS) {
                if (t.rps() <= 0 || t.rps() > l.maxRpsPerTarget()) {
                    e.add(p + "rps 0 < rps <= " + l.maxRpsPerTarget());
                }
                if (t.maxInFlight() > l.maxInFlightPerTarget()) {
                    e.add(p + "maxInFlight <= " + l.maxInFlightPerTarget());
                }
                totalRps += t.rps();
            } else {
                if (t.concurrency() < 1 || t.concurrency() > l.maxConcurrencyPerTarget()) {
                    e.add(p + "concurrency 1.." + l.maxConcurrencyPerTarget());
                }
                totalConc += t.concurrency();
            }
            if (t.rampUpSec() < 0 || t.rampUpSec() > Math.max(0, s.loadSec())) {
                if (t.activePhases() == ExperimentSpec.ActivePhases.LOAD) {
                    e.add(p + "rampUpSec 0..loadSec olmalı");
                }
            }
            if (t.timeoutMs() < 100 || t.timeoutMs() > l.maxTimeoutMs()) {
                e.add(p + "timeoutMs 100.." + l.maxTimeoutMs());
            }
            if (t.dataSize() < 1 || t.dataSize() > 100) {
                e.add(p + "dataSize 1..100");
            }
            if (t.readWriteRatio() != null && (t.readWriteRatio() < 0 || t.readWriteRatio() > 1)) {
                e.add(p + "readWriteRatio 0..1");
            }
            if (t.holdMs() < 0 || t.holdMs() > l.maxHoldMs()) {
                e.add(p + "holdMs 0.." + l.maxHoldMs());
            }
            var mix = catalog.mixFor(t.app(), t.mix(), t.readWriteRatio());
            if (mix.isEmpty() || mix.values().stream().mapToInt(Integer::intValue).sum() <= 0) {
                e.add(p + "operasyon karışımı boş");
            }
            for (var op : mix.keySet()) {
                OpDef d = catalog.op(t.app(), op);
                if (d == null) {
                    e.add(p + "bilinmeyen operasyon " + op);
                } else if (d.kind() == OpDef.Kind.LAB && !labTokenConfigured) {
                    e.add(p + op + " lab operasyonu; chaos.lab-token (LAB_TOKEN) tanımlı değil");
                }
            }
        }
        if (anyLoadPhase && s.loadSec() <= 0) {
            e.add("LOAD fazında çalışan hedef var ama loadSec = 0");
        }
        if (totalRps > l.maxTotalRps()) {
            e.add("toplam RPS " + totalRps + " > üst sınır " + l.maxTotalRps());
        }
        if (totalConc > l.maxTotalConcurrency()) {
            e.add("toplam concurrency " + totalConc + " > üst sınır " + l.maxTotalConcurrency());
        }
        return e;
    }
}
