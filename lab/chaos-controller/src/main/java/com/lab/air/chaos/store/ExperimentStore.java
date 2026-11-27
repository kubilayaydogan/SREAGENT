package com.lab.air.chaos.store;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/** Deney kayıtlarını dizinde JSON dosyası olarak saklar (atomik yazma). */
public final class ExperimentStore {

    private static final Logger log = LoggerFactory.getLogger(ExperimentStore.class);
    private static final Pattern SAFE_ID = Pattern.compile("[A-Za-z0-9-]{1,80}");

    private final Path dir;
    private final JsonMapper json;

    public ExperimentStore(Path dir, JsonMapper json) {
        this.dir = dir;
        this.json = json;
        try {
            Files.createDirectories(dir);
        } catch (IOException e) {
            throw new IllegalStateException("deney dizini oluşturulamadı: " + dir, e);
        }
    }

    public synchronized void save(ExperimentRecord r) {
        try {
            Path tmp = dir.resolve(r.experimentId() + ".json.tmp");
            Files.writeString(tmp, json.writerWithDefaultPrettyPrinter().writeValueAsString(r));
            Files.move(tmp, dir.resolve(r.experimentId() + ".json"), StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            log.error("deney kaydı yazılamadı {}", r.experimentId(), e);
        }
    }

    public Optional<ExperimentRecord> load(String id) {
        if (!SAFE_ID.matcher(id).matches()) {
            return Optional.empty();
        }
        Path p = dir.resolve(id + ".json");
        if (!Files.exists(p)) {
            return Optional.empty();
        }
        try {
            return Optional.of(json.readValue(Files.readString(p), ExperimentRecord.class));
        } catch (Exception e) {
            log.warn("deney kaydı okunamadı {}: {}", p, e.toString());
            return Optional.empty();
        }
    }

    public List<ExperimentRecord> list() {
        List<ExperimentRecord> out = new ArrayList<>();
        try (Stream<Path> s = Files.list(dir)) {
            s.filter(p -> p.toString().endsWith(".json")).forEach(p -> {
                String name = p.getFileName().toString();
                load(name.substring(0, name.length() - 5)).ifPresent(out::add);
            });
        } catch (IOException e) {
            log.warn("deney dizini listelenemedi: {}", e.toString());
        }
        out.sort(Comparator.comparing(ExperimentRecord::createdAt).reversed());
        return out;
    }

    public Optional<ExperimentRecord> findByClientRequestId(String clientRequestId) {
        return list().stream().filter(r -> clientRequestId.equals(r.clientRequestId())).findFirst();
    }
}
