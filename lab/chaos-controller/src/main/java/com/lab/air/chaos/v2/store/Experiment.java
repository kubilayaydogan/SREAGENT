package com.lab.air.chaos.v2.store;

import com.lab.air.chaos.v2.model.Model.LogLine;
import com.lab.air.chaos.v2.model.Model.Request;
import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.State;
import com.lab.air.chaos.v2.model.Model.Transition;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Bir v2 deneyinin kalıcı kaydı (JSON). Backend tek doğruluk kaynağıdır: UI durumu bu kayıttan okur.
 * Alanlar public (Jackson); eşzamanlı erişim runner içinde {@code synchronized (exp)} ile korunur.
 */
public class Experiment {

    public static final int MAX_LOGS = 1500;

    public String id;
    public String createdAt;
    public Request request;
    public String scenarioName;
    public String initiator;
    public List<String> targets = new ArrayList<>();
    public List<String> participants = new ArrayList<>();
    public String intensity;
    public long seed;
    public State state = State.CREATED;
    public String stateReason;
    public List<Transition> transitions = new ArrayList<>();
    public List<LogLine> logs = new ArrayList<>();
    public Map<String, Object> plan;              // somut eylemler, kontrol, ölçütler, parametreler
    public Map<String, Object> calibration;
    public List<Sample> samples = new ArrayList<>();
    public List<Map<String, Object>> workloadSummary = new ArrayList<>();
    public Map<String, Object> report;            // ImpactAnalyzer çıktısı
    public Map<String, Object> dynatrace;
    public String startedAt;
    public String endedAt;
    public boolean cleanupOk;
    public List<String> cleanupErrors = new ArrayList<>();

    public synchronized void log(String level, String msg) {
        logs.add(new LogLine(Instant.now().toString(), level, msg));
        if (logs.size() > MAX_LOGS) {
            logs.subList(0, logs.size() - MAX_LOGS).clear();
        }
    }

    /** Liste görünümü için hafif özet. */
    public synchronized Map<String, Object> summary() {
        Map<String, Object> m = new java.util.LinkedHashMap<>();
        m.put("id", id);
        m.put("createdAt", createdAt);
        m.put("scenarioId", request == null ? null : request.scenarioId());
        m.put("scenarioName", scenarioName);
        m.put("initiator", initiator);
        m.put("targets", targets);
        m.put("intensity", intensity);
        m.put("state", state);
        m.put("stateReason", stateReason);
        m.put("startedAt", startedAt);
        m.put("endedAt", endedAt);
        m.put("label", request == null ? null : request.label());
        m.put("randomPlanId", request == null ? null : request.randomPlanId());
        m.put("verdict", report == null ? null : report.get("verdict"));
        m.put("recovered", report == null ? null : ((Map<?, ?>) report.getOrDefault("recovery", Map.of())).get("recovered"));
        return m;
    }
}
