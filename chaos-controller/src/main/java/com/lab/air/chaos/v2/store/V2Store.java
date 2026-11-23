package com.lab.air.chaos.v2.store;

import com.lab.air.chaos.ChaosProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/**
 * v2 deney kayıtları: {@code <data-dir>/../v2/<id>.json}. Test edilen PostgreSQL'e yazılmaz (deney DB'yi
 * bozsa da kayıt kaybolmaz). Yazma atomik (tmp + move). Audit: {@code audit.jsonl} (append-only).
 */
@Component
public class V2Store {

    private static final Logger log = LoggerFactory.getLogger(V2Store.class);
    private static final Pattern ID = Pattern.compile("^v2-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$");

    private final Path dir;
    private final JsonMapper json = JsonMapper.builder()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES).build();
    private final Map<String, Map<String, Object>> summaries = new ConcurrentHashMap<>();

    public V2Store(ChaosProperties props) {
        Path base = Path.of(props.dataDir()).toAbsolutePath().normalize();
        this.dir = (base.getParent() == null ? base : base.getParent()).resolve("v2");
        try {
            Files.createDirectories(dir);
        } catch (IOException e) {
            throw new IllegalStateException("v2 veri dizini oluşturulamadı: " + dir, e);
        }
        try (Stream<Path> s = Files.list(dir)) {
            s.filter(p -> p.getFileName().toString().endsWith(".json")).forEach(p -> {
                try {
                    Experiment e = json.readValue(p.toFile(), Experiment.class);
                    summaries.put(e.id, e.summary());
                } catch (RuntimeException ex) {
                    log.warn("v2 kaydı okunamadı {}: {}", p.getFileName(), ex.getMessage());
                }
            });
        } catch (IOException e) {
            log.warn("v2 dizini listelenemedi: {}", e.toString());
        }
    }

    public JsonMapper json() {
        return json;
    }

    public Path dir() {
        return dir;
    }

    public static boolean validId(String id) {
        return id != null && ID.matcher(id).matches();
    }

    public synchronized void save(Experiment e) {
        String body;
        synchronized (e) {
            body = json.writeValueAsString(e);
            summaries.put(e.id, e.summary());
        }
        try {
            Path tmp = dir.resolve(e.id + ".json.tmp");
            Files.writeString(tmp, body);
            Files.move(tmp, dir.resolve(e.id + ".json"), StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException ex) {
            log.error("v2 kaydı yazılamadı {}: {}", e.id, ex.toString());
        }
    }

    public Optional<Experiment> load(String id) {
        if (!validId(id)) {
            return Optional.empty();
        }
        Path p = dir.resolve(id + ".json");
        if (!Files.exists(p)) {
            return Optional.empty();
        }
        try {
            return Optional.of(json.readValue(p.toFile(), Experiment.class));
        } catch (RuntimeException ex) {
            log.warn("v2 kaydı okunamadı {}: {}", id, ex.getMessage());
            return Optional.empty();
        }
    }

    public List<Map<String, Object>> list() {
        List<Map<String, Object>> l = new ArrayList<>(summaries.values());
        l.sort(Comparator.comparing((Map<String, Object> m) -> String.valueOf(m.get("createdAt"))).reversed());
        return l;
    }

    public List<String> nonTerminalIds() {
        return summaries.values().stream()
                .filter(m -> m.get("state") != null && !((com.lab.air.chaos.v2.model.Model.State) m.get("state")).terminal())
                .map(m -> (String) m.get("id")).toList();
    }

    public synchronized void audit(String actor, String action, Map<String, Object> details) {
        Map<String, Object> m = new java.util.LinkedHashMap<>();
        m.put("at", java.time.Instant.now().toString());
        m.put("actor", actor);
        m.put("action", action);
        m.put("details", details);
        try {
            Files.writeString(dir.resolve("audit.jsonl"), json.writeValueAsString(m) + "\n",
                    java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
        } catch (IOException ex) {
            log.warn("audit yazılamadı: {}", ex.toString());
        }
    }

    public List<String> auditTail(int n) {
        Path p = dir.resolve("audit.jsonl");
        if (!Files.exists(p)) {
            return List.of();
        }
        try {
            List<String> all = Files.readAllLines(p);
            return all.subList(Math.max(0, all.size() - n), all.size());
        } catch (IOException ex) {
            return List.of();
        }
    }
}
