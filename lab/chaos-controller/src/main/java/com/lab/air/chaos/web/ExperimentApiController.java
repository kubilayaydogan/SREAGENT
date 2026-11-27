package com.lab.air.chaos.web;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.engine.ContinuousTraffic;
import com.lab.air.chaos.engine.ExperimentService;
import com.lab.air.chaos.engine.ExperimentService.StartResult;
import com.lab.air.chaos.engine.ExperimentSpec;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.store.ExperimentRecord;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

@RestController
@RequestMapping("/api")
public class ExperimentApiController {

    private final ExperimentService service;
    private final ChaosProperties props;
    private final ContinuousTraffic continuous;
    private final Map<String, String> presets = new TreeMap<>();

    public ExperimentApiController(ExperimentService service, ChaosProperties props, ContinuousTraffic continuous)
            throws IOException {
        this.service = service;
        this.props = props;
        this.continuous = continuous;
        for (Resource r : new PathMatchingResourcePatternResolver().getResources("classpath:presets/*.json")) {
            String n = r.getFilename();
            presets.put(n.substring(0, n.length() - 5), r.getContentAsString(StandardCharsets.UTF_8));
        }
    }

    @PostMapping("/experiments")
    public ResponseEntity<?> start(@RequestBody ExperimentSpec spec) {
        StartResult r = service.start(spec);
        return switch (r) {
            case StartResult.Started s -> ResponseEntity.status(HttpStatus.CREATED).body(s.record());
            case StartResult.Replayed s -> ResponseEntity.ok(s.record());
            case StartResult.Rejected s -> ResponseEntity.status(s.status()).body(Map.of("errors", s.errors()));
        };
    }

    @GetMapping("/experiments")
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (ExperimentRecord r : service.list()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("experimentId", r.experimentId());
            m.put("scenario", r.spec().scenario());
            m.put("name", r.spec().name());
            m.put("state", r.state());
            m.put("createdAt", r.createdAt());
            m.put("startedAt", r.startedAt());
            m.put("endedAt", r.endedAt());
            m.put("targets", r.spec().targets().stream().map(t -> t.app() + ":" + t.role()).toList());
            out.add(m);
        }
        return out;
    }

    @GetMapping("/experiments/{id}")
    public ResponseEntity<ExperimentRecord> get(@PathVariable String id) {
        return service.get(id).map(ResponseEntity::ok).orElse(ResponseEntity.notFound().build());
    }

    @PostMapping("/experiments/{id}/stop")
    public ResponseEntity<Map<String, Object>> stop(@PathVariable String id) {
        boolean ok = service.stop(id, "kullanıcı durdurdu");
        return ResponseEntity.status(ok ? 202 : 409).body(Map.of("stopRequested", ok));
    }

    @PostMapping("/experiments/stop-all")
    public Map<String, String> stopAll() {
        return Map.of("result", service.stopAll("stop-all"));
    }

    /** Sürekli "normal gün" trafiği (deneylerden bağımsız). */
    @GetMapping("/continuous")
    public Map<String, Object> continuousStatus() {
        return continuous.status();
    }

    /** Gövde: {"enabled": true|false, "rpsPerApp": 0..20} — alanlar isteğe bağlı. */
    @PostMapping("/continuous")
    public Map<String, Object> continuousConfigure(@RequestBody Map<String, Object> body) {
        Boolean en = body.get("enabled") instanceof Boolean b ? b : null;
        Double rps = body.get("rpsPerApp") instanceof Number n ? n.doubleValue() : null;
        return continuous.configure(en, rps);
    }

    @GetMapping("/status")
    public Map<String, Object> status() {
        Map<String, Object> m = new LinkedHashMap<>();
        service.activeRun().ifPresentOrElse(run -> {
            var rec = run.record();
            m.put("experimentId", run.id());
            m.put("scenario", run.spec().scenario());
            m.put("state", run.state().name());
            m.put("phase", run.phase().name());
            m.put("phases", rec.phases());
            m.put("live", run.live());
            m.put("cleanup", rec.cleanup());
        }, () -> m.put("experimentId", null));
        m.put("continuous", Map.of("enabled", continuous.status().get("enabled"),
                "rpsPerApp", continuous.status().get("rpsPerApp")));
        return m;
    }

    @GetMapping("/catalog")
    public Map<String, Object> catalog() {
        Catalog c = service.catalog();
        Map<String, Object> out = new LinkedHashMap<>();
        for (String app : Catalog.APPS) {
            List<Map<String, String>> ops = new ArrayList<>();
            c.all().get(app).values().forEach(o -> ops.add(Map.of("name", o.name(), "kind", o.kind().name(),
                    "description", o.description())));
            out.put(app, Map.of("operations", ops, "normalMix", c.normalMix(app)));
        }
        return out;
    }

    @GetMapping("/limits")
    public Map<String, Object> limits() {
        return Map.of("limits", props.limits(), "labTokenConfigured", props.labToken() != null && props.labToken().length() >= 16,
                "defaultBackgroundRps", props.background(), "apps", props.apps());
    }

    @GetMapping("/presets")
    public List<String> presets() {
        return List.copyOf(presets.keySet());
    }

    @GetMapping(value = "/presets/{name}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> preset(@PathVariable String name) {
        String p = presets.get(name);
        return p == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(p);
    }
}
