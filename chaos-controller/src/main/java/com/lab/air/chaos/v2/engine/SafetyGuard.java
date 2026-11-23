package com.lab.air.chaos.v2.engine;

import com.lab.air.chaos.v2.V2Properties;
import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.Budget;
import com.lab.air.chaos.v2.model.Model.Sample;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Deney sırasında her örnekte çalışan güvenlik kontrolleri. Tek seferlik dalgalanma deneyi durdurmasın diye
 * kurallar {@link #CONSECUTIVE} ardışık örnekte ihlal edilince tetiklenir. İlk tetiklenen kural abort sebebidir.
 */
public final class SafetyGuard {

    public static final int CONSECUTIVE = 3;
    /** Uygulama / DB ölçümü bu kadar ardışık örnekte yoksa (2 s × 15 = 30 s) abort. */
    public static final int UNREACHABLE = 15;

    private final V2Properties props;
    private final Budget budget;
    private final Set<String> apps;
    private final Set<String> extra;
    private final Double dbSizeStartMb;
    private final Map<String, Integer> streak = new HashMap<>();

    public SafetyGuard(V2Properties props, Budget budget, Set<String> apps, List<String> extraRules, Double dbSizeStartMb) {
        this.props = props;
        Budget d = Budget.defaults();
        this.budget = budget == null ? d : new Budget(
                nz(budget.maxDbCpuPct(), d.maxDbCpuPct()), nz(budget.maxDbConnectionsPct(), d.maxDbConnectionsPct()),
                nz(budget.maxAppCpuPct(), d.maxAppCpuPct()), nz(budget.maxAppHeapPct(), d.maxAppHeapPct()),
                nz(budget.maxDbMemPct(), d.maxDbMemPct()));
        this.apps = apps;
        this.extra = Set.copyOf(extraRules);
        this.dbSizeStartMb = dbSizeStartMb;
    }

    private static double nz(Double v, double d) {
        return v == null ? d : v;
    }

    public Budget budget() {
        return budget;
    }

    /** İhlal varsa abort sebebi; yoksa null. */
    public String check(Sample s) {
        List<String[]> viol = new ArrayList<>();     // [kural, açıklama]
        var db = s.db();
        if (db == null || !db.up()) {
            viol.add(new String[]{"db-unreachable", "PostgreSQL ölçümü 30 s boyunca alınamadı (lab_monitor)"});
        } else {
            if (db.cpuPct() != null && db.cpuPct() > budget.maxDbCpuPct()) {
                viol.add(new String[]{"db-cpu", fmt("DB CPU %.0f%% > budget %.0f%%", db.cpuPct(), budget.maxDbCpuPct())});
            }
            Double cp = db.connectionsPct();
            if (cp != null && cp > budget.maxDbConnectionsPct()) {
                viol.add(new String[]{"db-conn", fmt("DB connections %.0f%% > budget %.0f%%", cp, budget.maxDbConnectionsPct())});
            }
            if (cp != null && cp >= 95 && extra.contains("DB connections ≥ %95")) {
                viol.add(new String[]{"db-conn-95", fmt("DB connections %.0f%% ≥ %%95", cp, 0.0)});
            }
            if (db.memUsedPct() != null && db.memUsedPct() > budget.maxDbMemPct()) {
                viol.add(new String[]{"db-mem", fmt("DB_VM memory %.0f%% > %.0f%%", db.memUsedPct(), budget.maxDbMemPct())});
            }
            if (dbSizeStartMb != null && db.dbSizeMb() != null && db.dbSizeMb() - dbSizeStartMb > props.maxDbGrowthMb()) {
                viol.add(new String[]{"db-growth", fmt("DB boyutu %.0f MB arttı > %.0f MB", db.dbSizeMb() - dbSizeStartMb,
                        props.maxDbGrowthMb())});
            }
        }
        for (String app : apps) {
            AppSample a = s.apps() == null ? null : s.apps().get(app);
            if (a == null || !a.up()) {
                viol.add(new String[]{"app-down-" + app, app + " 30 s boyunca erişilemedi (actuator)"});
                continue;
            }
            if (a.cpuPct() != null && a.cpuPct() > budget.maxAppCpuPct()) {
                viol.add(new String[]{"app-cpu-" + app, fmt(app + " CPU %.0f%% > %.0f%%", a.cpuPct(), budget.maxAppCpuPct())});
            }
            Double h = a.heapPct();
            if (h != null && h > budget.maxAppHeapPct()) {
                viol.add(new String[]{"app-heap-" + app, fmt(app + " heap %.0f%% > %.0f%%", h, budget.maxAppHeapPct())});
            }
            if (h != null && h >= 97 && extra.contains("heap ≥ %97")) {
                viol.add(new String[]{"app-heap97-" + app, fmt(app + " heap %.0f%% ≥ %%97", h, 0.0)});
            }
        }
        if (s.appVm() != null) {
            if (s.appVm().diskFreeGb() != null && s.appVm().diskFreeGb() < props.minAppVmDiskFreeGb()) {
                viol.add(new String[]{"appvm-disk", fmt("APP_VM boş disk %.1f GB < %.1f GB", s.appVm().diskFreeGb(),
                        props.minAppVmDiskFreeGb())});
            }
            if (s.appVm().memUsedPct() != null && s.appVm().memUsedPct() > 95) {
                viol.add(new String[]{"appvm-mem", fmt("APP_VM memory %.0f%% > %%95", s.appVm().memUsedPct(), 0.0)});
            }
        }
        Set<String> now = new java.util.HashSet<>();
        String reason = null;
        for (String[] v : viol) {
            now.add(v[0]);
            int need = v[0].startsWith("db-unreachable") || v[0].startsWith("app-down") ? UNREACHABLE : CONSECUTIVE;
            int c = streak.merge(v[0], 1, Integer::sum);
            if (c >= need && reason == null) {
                reason = v[1];
            }
        }
        streak.keySet().retainAll(now);
        return reason;
    }

    private static String fmt(String f, double a, double b) {
        return String.format(java.util.Locale.ROOT, f, a, b);
    }
}
