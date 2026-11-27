package com.lab.air.chaos.engine;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.ops.Catalog;
import com.lab.air.chaos.ops.DataPools;
import com.lab.air.chaos.ops.OpDef;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

import java.net.http.HttpClient;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Deneylerden BAĞIMSIZ, sürekli "normal gün" trafiği. Amaç: sistem hiçbir zaman tamamen boş kalmasın; Dashboard'da
 * her an veri olsun ve SRE Agent'ın baseline penceresi gerçek trafik içersin (aksi halde "baseline'da gecikme verisi
 * yok" olur ve risk küçümsenir).
 *
 * <ul>
 *   <li>Her uygulamaya katalogdaki normal karışım (okuma + yazma) ile sabit RPS; sıcak satırlara dokunmaz.</li>
 *   <li>Deney sırasında da çalışmaya devam eder (baseline ile yük fazı aynı taban trafiği görür).</li>
 *   <li>İstekler {@code X-Experiment-Id: continuous}, {@code X-Chaos-Source: continuous} başlıklarıyla gider.</li>
 *   <li>Açma/kapama ve RPS {@code /api/continuous} ile; üst sınır {@link #MAX_RPS_PER_APP}.</li>
 * </ul>
 */
@Component
public class ContinuousTraffic {

    public static final double MAX_RPS_PER_APP = 20.0;
    private static final Logger log = LoggerFactory.getLogger(ContinuousTraffic.class);

    private final ChaosProperties props;
    private final MeterRegistry meters;
    private final Catalog catalog = new Catalog();
    private final JsonMapper json = JsonMapper.builder().build();
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(2))
            .executor(Executors.newVirtualThreadPerTaskExecutor())
            .version(HttpClient.Version.HTTP_1_1)
            .build();
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
    private final ScheduledExecutorService refresher = Executors.newSingleThreadScheduledExecutor(
            r -> Thread.ofPlatform().daemon().name("continuous-refresh").unstarted(r));
    private final List<Generator> generators = new ArrayList<>();
    private DataPools pools;
    private Stats stats;
    private boolean enabled;
    private double rpsPerApp;
    private Instant startedAt;

    public ContinuousTraffic(ChaosProperties props, MeterRegistry meters,
                             @Value("${chaos.continuous.enabled:true}") boolean enabled,
                             @Value("${chaos.continuous.rps-per-app:4}") double rpsPerApp) {
        this.props = props;
        this.meters = meters;
        this.enabled = enabled;
        this.rpsPerApp = clamp(rpsPerApp);
    }

    @PostConstruct
    void init() {
        pools = new DataPools(props.apps(), http, json);
        refresher.scheduleWithFixedDelay(() -> {
            try {
                pools.refresh();
            } catch (RuntimeException e) {
                log.debug("sürekli trafik havuz tazeleme hatası: {}", e.toString());
            }
        }, 0, 30, TimeUnit.SECONDS);
        if (enabled) {
            startGenerators();
        }
    }

    /** enabled/rps değişikliğini uygular; RPS değişirse üreticiler yeniden kurulur. */
    public synchronized Map<String, Object> configure(Boolean enable, Double rps) {
        if (rps != null) {
            rpsPerApp = clamp(rps);
        }
        if (enable != null) {
            enabled = enable;
        }
        stopGenerators();
        if (enabled && rpsPerApp > 0) {
            startGenerators();
        }
        log.info("sürekli trafik: enabled={} rpsPerApp={}", enabled, rpsPerApp);
        return status();
    }

    public synchronized Map<String, Object> status() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("enabled", enabled && !generators.isEmpty());
        m.put("rpsPerApp", rpsPerApp);
        m.put("maxRpsPerApp", MAX_RPS_PER_APP);
        m.put("apps", Catalog.APPS);
        m.put("startedAt", startedAt);
        if (stats != null) {
            Map<String, Object> per = new LinkedHashMap<>();
            for (String app : Catalog.APPS) {
                long[] t = stats.totals(Phase.LOAD, "continuous", app);
                per.put(app, Map.of("intended", t[0], "sent", t[1], "completed", t[2], "errors", t[3],
                        "dropped", t[4], "noData", t[5]));
            }
            m.put("totals", per);
        }
        return m;
    }

    private void startGenerators() {
        stats = new Stats();
        var ctx = new OpDef.OpContext(new OpDef.Params(1, false, 0), pools, json);
        Sender sender = new Sender(http, props.apps(), props.labToken(), "continuous", stats, meters, ctx);
        for (String app : Catalog.APPS) {
            var appCtx = new OpDef.OpContext(new OpDef.Params(1, false, 0), pools, json);
            var weighted = new ArrayList<Map.Entry<OpDef, Integer>>();
            catalog.normalMix(app).forEach((op, w) -> weighted.add(Map.entry(catalog.op(app, op), w)));
            Generator g = new Generator.Rps(app, "continuous", weighted, appCtx, sender, stats, () -> Phase.LOAD,
                    5000, 30, rpsPerApp, 32, executor);
            g.start();
            generators.add(g);
        }
        startedAt = Instant.now();
    }

    private void stopGenerators() {
        generators.forEach(Generator::stop);
        generators.clear();
        startedAt = null;
    }

    private static double clamp(double v) {
        return Math.max(0, Math.min(MAX_RPS_PER_APP, v));
    }

    @PreDestroy
    synchronized void shutdown() {
        stopGenerators();
        refresher.shutdownNow();
        executor.shutdown();
    }
}
