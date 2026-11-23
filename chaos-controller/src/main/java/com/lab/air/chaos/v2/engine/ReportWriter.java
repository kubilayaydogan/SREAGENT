package com.lab.air.chaos.v2.engine;

import com.lab.air.chaos.v2.store.Experiment;

import java.util.List;
import java.util.Map;

/** Deney raporu (markdown). Açıklamalar Türkçe, teknik terimler İngilizce; tüm değerler ölçülmüş örneklerden. */
public final class ReportWriter {

    private ReportWriter() {
    }

    @SuppressWarnings("unchecked")
    public static String markdown(Experiment e) {
        Map<String, Object> r = e.report;
        StringBuilder b = new StringBuilder();
        b.append("# Chaos experiment ").append(e.id).append("\n\n");
        b.append("| Alan | Değer |\n|---|---|\n");
        row(b, "Scenario", e.request.scenarioId() + " — " + e.scenarioName);
        row(b, "Initiator", e.initiator);
        row(b, "Direct target(s)", String.join(", ", e.targets));
        row(b, "Intensity", e.intensity);
        row(b, "Seed", String.valueOf(e.seed));
        row(b, "Final state", String.valueOf(r.get("finalState")));
        row(b, "Verdict", String.valueOf(r.get("verdict")));
        row(b, "Başlangıç / bitiş", e.startedAt + " / " + (e.endedAt == null ? "-" : e.endedAt));
        if (r.get("stopReason") != null && !String.valueOf(r.get("stopReason")).isEmpty()) {
            row(b, "Durdurma sebebi", String.valueOf(r.get("stopReason")));
        }
        b.append("\n## Success criteria\n\n| Ölçüt | Metric | Beklenen | Gözlenen | Baseline | Sonuç |\n|---|---|---|---|---|---|\n");
        for (Map<String, Object> c : (List<Map<String, Object>>) r.get("criteria")) {
            b.append("| ").append(c.get("description")).append(Boolean.TRUE.equals(c.get("required")) ? "" : " _(bilgi)_")
                    .append(" | ").append(c.get("metric")).append(c.get("key") == null ? "" : " [" + c.get("key") + "]")
                    .append(" | ").append(c.get("expected")).append(" | ").append(c.get("observed"))
                    .append(" | ").append(c.get("baseline")).append(" | ").append(c.get("result")).append(" |\n");
        }
        Map<String, Object> evo = (Map<String, Object>) r.get("expectedVsObserved");
        b.append("\n## Expected vs observed impact\n\n");
        b.append("- Beklenen etkilenen uygulamalar: ").append(evo.get("expectedAffected")).append('\n');
        b.append("- Gözlenen etkilenen uygulamalar: ").append(evo.get("observedAffected")).append('\n');
        b.append("- Beklenmeyen etki: ").append(evo.get("unexpectedlyAffected")).append('\n');
        b.append("- Beklenip gözlenmeyen: ").append(evo.get("expectedButNotObserved")).append("\n\n");
        b.append("Etkilenme ölçütü: p95 ≥ max(1.5 × baseline, baseline + 20 ms) veya error % ≥ baseline + 2 veya pool pending ≥ 1.\n\n");

        b.append("## Uygulamalar (baseline avg → run p90 / max → recovery avg)\n\n");
        b.append("| App | Role | p95 ms | p99 ms | error % | RPS | pool usage % | CPU % | heap % |\n|---|---|---|---|---|---|---|---|---|\n");
        for (var en : ((Map<String, Object>) r.get("apps")).entrySet()) {
            Map<String, Object> a = (Map<String, Object>) en.getValue();
            Map<String, Object> m = (Map<String, Object>) a.get("metrics");
            b.append("| ").append(en.getKey()).append(Boolean.TRUE.equals(a.get("observedAffected")) ? " ⚠" : "")
                    .append(" | ").append(a.get("role"));
            for (String k : List.of("APP_P95_MS", "APP_P99_MS", "APP_ERROR_PCT", "APP_RPS", "APP_POOL_USAGE_PCT",
                    "APP_CPU_PCT", "APP_HEAP_PCT")) {
                b.append(" | ").append(cell((Map<String, Object>) m.get(k)));
            }
            b.append(" |\n");
        }
        b.append("\n## PostgreSQL / DB_VM\n\n| Metric | baseline avg | run p90 | run max | recovery avg | değişim % |\n|---|---|---|---|---|---|\n");
        for (var en : ((Map<String, Object>) r.get("db")).entrySet()) {
            Map<String, Object> m = (Map<String, Object>) en.getValue();
            b.append("| ").append(en.getKey()).append(" | ").append(m.get("baselineAvg")).append(" | ").append(m.get("runP90"))
                    .append(" | ").append(m.get("runMax")).append(" | ").append(m.get("recoveryAvg")).append(" | ")
                    .append(m.get("changePct")).append(" |\n");
        }
        Map<String, Object> ctl = (Map<String, Object>) r.get("controller");
        if (ctl != null && !ctl.isEmpty()) {
            b.append("\n## Adaptive control\n\n- Metric: ").append(ctl.get("metric")).append(" (").append(ctl.get("scope"))
                    .append("), target ").append(ctl.get("target")).append("\n- Final level: ").append(ctl.get("finalLevel"))
                    .append(", saturated: ").append(ctl.get("saturated")).append('\n');
            if (ctl.get("note") != null && !String.valueOf(ctl.get("note")).isEmpty()) {
                b.append("- Not: ").append(ctl.get("note")).append('\n');
            }
        }
        Map<String, Object> rec = (Map<String, Object>) r.get("recovery");
        b.append("\n## Recovery\n\n- Recovered: ").append(rec.get("recovered")).append("\n- Recovery time (s): ")
                .append(rec.get("recoveryTimeSec")).append("\n- Gözlem penceresi (s): ").append(rec.get("windowSec")).append('\n');
        b.append("\n## Workload özeti (istemci tarafı)\n\n");
        for (Map<String, Object> w : e.workloadSummary) {
            b.append("- ").append(w).append('\n');
        }
        b.append("\n## Cleanup\n\n- OK: ").append(e.cleanupOk).append('\n');
        e.cleanupErrors.forEach(x -> b.append("- ").append(x).append('\n'));
        if (e.dynatrace != null) {
            b.append("\n## Dynatrace correlation\n\n");
            e.dynatrace.forEach((k, v) -> b.append("- ").append(k).append(": `").append(v).append("`\n"));
        }
        return b.toString();
    }

    private static void row(StringBuilder b, String k, String v) {
        b.append("| ").append(k).append(" | ").append(v == null ? "-" : v.replace("|", "\\|")).append(" |\n");
    }

    private static String cell(Map<String, Object> m) {
        if (m == null) {
            return "-";
        }
        return m.get("baselineAvg") + " → " + m.get("runP90") + " / " + m.get("runMax") + " → " + m.get("recoveryAvg");
    }
}
