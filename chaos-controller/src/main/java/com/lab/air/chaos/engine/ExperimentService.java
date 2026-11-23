package com.lab.air.chaos.engine;

import com.lab.air.chaos.RunGate;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.ops.DataPools;
import com.lab.air.chaos.ops.OpDef;
import com.lab.air.chaos.store.ExperimentRecord;
import com.lab.air.chaos.store.ExperimentStore;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;

import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Deney yönetimi. Aynı anda tek aktif deney (lab kontrol edilebilirliği için).
 * Başlatma idempotenttir: aynı clientRequestId + aynı tanım → mevcut deney döner;
 * aynı anahtar + farklı tanım → çakışma.
 */
@Service
public class ExperimentService {

    private static final Logger log = LoggerFactory.getLogger(ExperimentService.class);
    private static final DateTimeFormatter ID_TS = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss");

    public sealed interface StartResult {
        record Started(ExperimentRecord record) implements StartResult { }
        record Replayed(ExperimentRecord record) implements StartResult { }
        record Rejected(int status, List<String> errors) implements StartResult { }
    }

    private final ChaosProperties props;
    private final Catalog catalog = new Catalog();
    private final JsonMapper json = JsonMapper.builder().build();
    private final HttpClient http;
    private final ExperimentStore store;
    private final SpecValidator validator;
    private final MeterRegistry meters;
    private volatile ExperimentRun active;

    private RunGate gate;

    @org.springframework.beans.factory.annotation.Autowired(required = false)
    void setGate(RunGate gate) {
        this.gate = gate;
    }

    public ExperimentService(ChaosProperties props, MeterRegistry meters) {
        this.props = props;
        this.meters = meters;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .executor(Executors.newVirtualThreadPerTaskExecutor())
                .version(HttpClient.Version.HTTP_1_1)
                .build();
        this.store = new ExperimentStore(Path.of(props.dataDir()), json);
        this.validator = new SpecValidator(props.limits(), catalog,
                props.labToken() != null && props.labToken().length() >= 16);
        Gauge.builder("chaos.experiment.active", () -> active != null && active.isActive() ? 1 : 0).register(meters);
        Gauge.builder("chaos.experiment.phase", () -> active != null && active.isActive() ? active.phase().ordinal() : -1)
                .description("0=WARMUP 1=BASELINE 2=LOAD 3=RECOVERY 4=DONE, -1=yok").register(meters);
    }

    public Catalog catalog() {
        return catalog;
    }

    public synchronized StartResult start(ExperimentSpec spec) {
        List<String> errors = validator.validate(spec);
        if (!errors.isEmpty()) {
            return new StartResult.Rejected(400, errors);
        }
        String hash = hash(spec);
        ExperimentRun cur = active;
        if (cur != null && spec.clientRequestId().equals(cur.spec().clientRequestId())) {
            return cur.specHash().equals(hash) ? new StartResult.Replayed(cur.record())
                    : new StartResult.Rejected(409, List.of("clientRequestId farklı bir tanımla zaten kullanıldı"));
        }
        Optional<ExperimentRecord> prev = store.findByClientRequestId(spec.clientRequestId());
        if (prev.isPresent()) {
            return prev.get().specHash().equals(hash) ? new StartResult.Replayed(prev.get())
                    : new StartResult.Rejected(409, List.of("clientRequestId farklı bir tanımla zaten kullanıldı"));
        }
        if (cur != null && cur.isActive()) {
            return new StartResult.Rejected(409, List.of("aktif deney var: " + cur.id() + " (önce durdurun)"));
        }
        String v2 = gate == null ? null : gate.v2Busy();
        if (v2 != null) {
            return new StartResult.Rejected(409, List.of("chaos v2 deneyi aktif: " + v2));
        }
        String id = "exp-" + ZonedDateTime.now(ZoneOffset.UTC).format(ID_TS) + "-"
                + Integer.toHexString(ThreadLocalRandom.current().nextInt(0x1000, 0xFFFF));
        Stats stats = new Stats();
        DataPools pools = new DataPools(props.apps(), http, json);
        var ctx = new OpDef.OpContext(new OpDef.Params(1, false, 0), pools, json);
        Sender sender = new Sender(http, props.apps(), props.labToken(), id, stats, meters, ctx);
        ExperimentRun run = new ExperimentRun(id, spec, hash, catalog, pools, sender, stats, http, props.apps(),
                props.labToken(), store::save);
        active = run;
        run.start();
        ExperimentRecord r = run.record();
        store.save(r);
        log.info("deney başlatıldı id={} senaryo={} hedefler={}", id, spec.scenario(), spec.targets().size());
        return new StartResult.Started(r);
    }

    public Optional<ExperimentRecord> get(String id) {
        ExperimentRun cur = active;
        if (cur != null && cur.id().equals(id)) {
            return Optional.of(cur.record());
        }
        return store.load(id);
    }

    public List<ExperimentRecord> list() {
        return store.list();
    }

    public Optional<ExperimentRun> activeRun() {
        return Optional.ofNullable(active);
    }

    public boolean stop(String id, String reason) {
        ExperimentRun cur = active;
        if (cur != null && cur.id().equals(id) && cur.isActive()) {
            cur.requestStop(reason);
            return true;
        }
        return false;
    }

    /** Tüm yükü durdurur ve sunucu tarafı cleanup tamamlanana kadar (sınırlı) bekler. */
    public String stopAll(String reason) {
        ExperimentRun cur = active;
        if (cur == null || !cur.isActive()) {
            return "aktif deney yok";
        }
        cur.requestStop(reason);
        try {
            cur.awaitEnd(45_000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return cur.id() + " " + cur.state();
    }

    @PreDestroy
    void shutdown() {
        stopAll("uygulama kapanıyor");
    }

    String hash(ExperimentSpec spec) {
        try {
            byte[] b = json.writeValueAsString(spec).getBytes(StandardCharsets.UTF_8);
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(b)).substring(0, 16);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
