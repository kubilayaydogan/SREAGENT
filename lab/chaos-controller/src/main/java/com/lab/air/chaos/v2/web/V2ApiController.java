package com.lab.air.chaos.v2.web;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.v2.V2Properties;
import com.lab.air.chaos.v2.engine.ExperimentRunner;
import com.lab.air.chaos.v2.engine.Orchestrator;
import com.lab.air.chaos.v2.metrics.MetricsCollector;
import com.lab.air.chaos.v2.model.Model.AppSample;
import com.lab.air.chaos.v2.model.Model.Intensity;
import com.lab.air.chaos.v2.model.Model.RandomPlan;
import com.lab.air.chaos.v2.model.Model.Request;
import com.lab.air.chaos.v2.model.Model.Sample;
import com.lab.air.chaos.v2.model.Model.TargetMode;
import com.lab.air.chaos.v2.scenario.ScenarioDef;
import com.lab.air.chaos.v2.scenario.ScenarioRegistry;
import com.lab.air.chaos.v2.store.Experiment;
import com.lab.air.chaos.v2.store.V2Store;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Chaos v2 REST API (HTTP Basic, BasicAuthFilter). Ayrıntılı dokümantasyon: docs/lab/chaos-v2-architecture.md.
 */
@RestController
@RequestMapping("/api/v2")
public class V2ApiController {

    private final Orchestrator orch;
    private final MetricsCollector metrics;
    private final V2Store store;
    private final V2Properties props;
    private final String actor;

    public V2ApiController(Orchestrator orch, MetricsCollector metrics, V2Store store, V2Properties props,
                           ChaosProperties chaosProps) {
        this.orch = orch;
        this.metrics = metrics;
        this.store = store;
        this.props = props;
        this.actor = chaosProps.auth() == null ? "chaos" : chaosProps.auth().user();
    }

    /** Overview: uygulama kartları (status, CPU, RAM, pool, RPS, error, p95, aktif chaos) + DB + APP_VM. */
    @GetMapping("/overview")
    public Map<String, Object> overview() {
        Sample s = metrics.latest();
        Map<String, Object> m = new LinkedHashMap<>();
        var active = orch.active();
        List<Map<String, Object>> cards = new ArrayList<>();
        for (String app : props.allowedApps()) {
            AppSample a = s == null ? null : s.apps().get(app);
            Map<String, Object> c = new LinkedHashMap<>();
            c.put("app", app);
            c.put("sample", a);
            c.put("heapPct", a == null ? null : a.heapPct());
            c.put("heapLivePct", a == null || a.heapLiveMb() == null ? null : a.heapLivePct());
            c.put("poolUsagePct", a == null ? null : a.poolUsagePct());
            List<String> roles = new ArrayList<>();
            active.ifPresent(r -> {
                Experiment e = r.experiment();
                if (app.equals(e.initiator)) {
                    roles.add("initiator");
                }
                if (e.targets.contains(app)) {
                    roles.add("direct-target");
                }
                if (e.participants.contains(app) && roles.isEmpty()) {
                    roles.add("participant");
                }
            });
            c.put("activeChaos", active.isPresent() && !roles.isEmpty()
                    ? Map.of("experimentId", active.get().id(), "scenario", active.get().experiment().request.scenarioId(),
                    "roles", roles, "state", active.get().state()) : null);
            cards.add(c);
        }
        m.put("apps", cards);
        m.put("db", s == null ? null : s.db());
        m.put("dbConnectionsPct", s == null || s.db() == null ? null : s.db().connectionsPct());
        m.put("dbError", metrics.dbError());
        m.put("appVm", s == null ? null : s.appVm());
        m.put("sampledAt", s == null ? null : s.at());
        m.put("active", active.map(r -> r.experiment().summary()).orElse(null));
        m.put("queue", orch.queued());
        m.put("environment", props.environment());
        m.put("calibration", orch.calibration());
        return m;
    }

    @GetMapping("/metrics/history")
    public List<Sample> history(@RequestParam(defaultValue = "150") int max) {
        return metrics.history(Math.max(1, Math.min(450, max)));
    }

    @GetMapping("/scenarios")
    public List<Map<String, Object>> scenarios() {
        return orch.registry().all().stream().map(ScenarioRegistry::describe).toList();
    }

    @GetMapping("/scenarios/{id}")
    public ResponseEntity<Map<String, Object>> scenario(@PathVariable String id) {
        return orch.registry().get(id).map(d -> ResponseEntity.ok(ScenarioRegistry.describe(d)))
                .orElse(ResponseEntity.notFound().build());
    }

    /** Ortamdan kalibre edilmiş yoğunluk önizlemesi: her senaryo × yoğunluk için somut parametreler. */
    @GetMapping("/intensity-presets")
    public Map<String, Object> presets(@RequestParam(required = false) String initiator) {
        String init = initiator != null && props.allowedApps().contains(initiator) ? initiator : props.allowedApps().getFirst();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("calibration", orch.calibration());
        Map<String, Object> per = new LinkedHashMap<>();
        for (ScenarioDef d : orch.registry().all()) {
            Map<String, Object> byI = new LinkedHashMap<>();
            for (Intensity i : List.of(Intensity.LIGHT, Intensity.MEDIUM, Intensity.HIGH, Intensity.CRITICAL)) {
                try {
                    var r = orch.resolve(new Request(d.id(), init, TargetMode.AUTO, List.of(), i, null, null, null, 42L,
                            null, null, null, null, null));
                    Map<String, Object> x = new LinkedHashMap<>(r.plan().parameters());
                    x.put("control", r.plan().control());
                    x.put("maxWorkers", r.plan().actions().stream().mapToInt(ScenarioDef.Action::maxWorkers).sum());
                    byI.put(i.name(), x);
                } catch (RuntimeException e) {
                    byI.put(i.name(), Map.of("error", e.getMessage()));
                }
            }
            per.put(d.id(), byI);
        }
        out.put("scenarios", per);
        return out;
    }

    /** Dry-run: doğrulama + hedef çözümleme + plan; deney başlatmaz. */
    @PostMapping("/experiments/preview")
    public ResponseEntity<Map<String, Object>> preview(@RequestBody Request r) {
        List<String> errors = orch.validate(r);
        if (!errors.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("errors", errors));
        }
        try {
            return ResponseEntity.ok(Orchestrator.describePlan(orch.resolve(normalizeForPreview(r))));
        } catch (RuntimeException e) {
            return ResponseEntity.badRequest().body(Map.of("errors", List.of(String.valueOf(e.getMessage()))));
        }
    }

    private static Request normalizeForPreview(Request r) {
        return new Request(r.scenarioId(), r.initiator(), r.targetMode(), r.targets() == null ? List.of() : r.targets(),
                r.intensity(), r.custom(), r.durations(), r.traffic(), r.seed(), r.label(), r.clientRequestId(),
                r.randomPlanId(), r.subScenarios(), r.cooldownSec());
    }

    @PostMapping("/experiments")
    public ResponseEntity<Object> submit(@RequestBody Request r) {
        var s = orch.submit(r, actor);
        if (s.experiment() == null) {
            return ResponseEntity.status(s.status()).body(Map.of("errors", s.errors()));
        }
        return ResponseEntity.status(s.status()).body(s.experiment().summary());
    }

    @GetMapping("/experiments")
    public Map<String, Object> list() {
        // Boştayken active null'dır; Map.of null kabul etmez (önceden IDLE'da 500 dönüyordu).
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("experiments", store.list());
        out.put("queue", orch.queued());
        out.put("active", orch.active().map(r -> r.experiment().summary()).orElse(null));
        return out;
    }

    @GetMapping("/experiments/{id}")
    public ResponseEntity<Experiment> get(@PathVariable String id, @RequestParam(defaultValue = "true") boolean samples) {
        return orch.get(id).map(e -> {
            if (samples) {
                return ResponseEntity.ok(e);
            }
            Experiment light = copyWithoutSamples(e);
            return ResponseEntity.ok(light);
        }).orElse(ResponseEntity.notFound().build());
    }

    private static Experiment copyWithoutSamples(Experiment e) {
        Experiment x = new Experiment();
        synchronized (e) {
            x.id = e.id;
            x.createdAt = e.createdAt;
            x.request = e.request;
            x.scenarioName = e.scenarioName;
            x.initiator = e.initiator;
            x.targets = e.targets;
            x.participants = e.participants;
            x.intensity = e.intensity;
            x.seed = e.seed;
            x.state = e.state;
            x.stateReason = e.stateReason;
            x.transitions = List.copyOf(e.transitions);
            x.logs = List.copyOf(e.logs.subList(Math.max(0, e.logs.size() - 200), e.logs.size()));
            x.plan = e.plan;
            x.calibration = e.calibration;
            x.workloadSummary = e.workloadSummary;
            x.report = e.report;
            x.dynatrace = e.dynatrace;
            x.startedAt = e.startedAt;
            x.endedAt = e.endedAt;
            x.cleanupOk = e.cleanupOk;
            x.cleanupErrors = e.cleanupErrors;
        }
        return x;
    }

    /** Canlı izleme: since (tMs) sonrası örnekler + durum. */
    @GetMapping("/experiments/{id}/live")
    public ResponseEntity<Map<String, Object>> live(@PathVariable String id, @RequestParam(defaultValue = "-1") long since) {
        return orch.get(id).map(e -> {
            Map<String, Object> m = new LinkedHashMap<>();
            synchronized (e) {
                m.put("state", e.state);
                m.put("stateReason", e.stateReason);
                m.put("transitions", List.copyOf(e.transitions));
                m.put("samples", e.samples.stream().filter(s -> s.tMs() > since).toList());
                m.put("logs", List.copyOf(e.logs.subList(Math.max(0, e.logs.size() - 50), e.logs.size())));
                m.put("report", e.report);
            }
            return ResponseEntity.ok(m);
        }).orElse(ResponseEntity.notFound().build());
    }

    @GetMapping(value = "/experiments/{id}/report.md", produces = "text/markdown;charset=UTF-8")
    public ResponseEntity<String> reportMd(@PathVariable String id) {
        return orch.get(id).filter(e -> e.report != null)
                .map(e -> ResponseEntity.ok(String.valueOf(e.report.get("markdown"))))
                .orElse(ResponseEntity.notFound().build());
    }

    @PostMapping("/experiments/{id}/stop")
    public ResponseEntity<Map<String, Object>> stop(@PathVariable String id) {
        boolean ok = orch.stop(id, actor);
        return ok ? ResponseEntity.accepted().body(Map.of("stopping", id))
                : ResponseEntity.status(404).body(Map.of("errors", List.of("aktif/kuyrukta deney yok: " + id)));
    }

    @PostMapping("/experiments/{id}/rerun")
    public ResponseEntity<Object> rerun(@PathVariable String id) {
        var s = orch.rerun(id, actor);
        return s.experiment() == null ? ResponseEntity.status(s.status()).body(Map.of("errors", s.errors()))
                : ResponseEntity.status(s.status()).body(s.experiment().summary());
    }

    @PostMapping("/emergency-stop")
    public Map<String, Object> emergency() {
        return orch.emergencyStop(actor);
    }

    @PostMapping("/random-plans")
    public ResponseEntity<Map<String, Object>> random(@RequestBody RandomPlan p) {
        var r = orch.randomPlan(p, actor);
        return r.containsKey("errors") ? ResponseEntity.badRequest().body(r) : ResponseEntity.accepted().body(r);
    }

    @GetMapping(value = "/audit", produces = MediaType.APPLICATION_JSON_VALUE)
    public List<String> audit(@RequestParam(defaultValue = "100") int n) {
        return store.auditTail(Math.max(1, Math.min(1000, n)));
    }

    /** Build/restart öncesi kontrol için: yük aktif mi? */
    @GetMapping("/status")
    public Map<String, Object> status() {
        var a = orch.active();
        return Map.of("busy", a.isPresent() || !orch.queued().isEmpty(),
                "active", a.map(ExperimentRunner::id).orElse(""),
                "state", a.map(r -> r.state().name()).orElse("IDLE"), "queued", orch.queued().size());
    }
}
