package com.lab.air.chaos.v2.engine;

import com.lab.air.chaos.ChaosProperties;
import com.lab.air.chaos.v2.V2Properties;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;

/**
 * Uygulamalara ve netem sidecar'larına giden tüm v2 çağrıları. Her istek Dynatrace korelasyon başlıklarını taşır:
 * X-Experiment-Id, X-Chaos-Scenario / -Initiator / -Target / -Intensity / -Phase, X-Chaos-Source, X-Request-Id.
 * (Uygulamalar bunları chaos.* MDC alanlarına koyar; SRE Agent bu alanları kanıt verisinden siler.)
 */
public final class ChaosHttp {

    /** Bir deneyin korelasyon bağlamı; phase dinamik (runner'ın mevcut durumu). */
    public record Correlation(String experimentId, String scenario, String initiator, String target, String intensity,
                              Supplier<String> phase) {
        public static Correlation none() {
            return new Correlation("none", "none", "none", "none", "none", () -> "NONE");
        }
    }

    public record Result(int status, String body, long micros, boolean timeout, String error) {
        public boolean ok() {
            return status >= 200 && status < 400;
        }
    }

    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2))
            .executor(Executors.newVirtualThreadPerTaskExecutor()).version(HttpClient.Version.HTTP_1_1).build();
    private final Map<String, String> apps;
    private final String labToken;
    private final int netemPort;
    private final AtomicLong seq = new AtomicLong();

    public ChaosHttp(ChaosProperties props, V2Properties v2) {
        this(props.apps(), props.labToken(), v2.netemPort());
    }

    public ChaosHttp(Map<String, String> apps, String labToken, int netemPort) {
        this.apps = Map.copyOf(apps);
        this.labToken = labToken;
        this.netemPort = netemPort;
    }

    public Map<String, String> apps() {
        return apps;
    }

    public HttpClient client() {
        return http;
    }

    public Result call(String app, String method, String pathAndQuery, String jsonBody, Correlation c, String source,
                       int timeoutMs, boolean lab) {
        return call(app, method, pathAndQuery, jsonBody, c, source, timeoutMs, lab, Map.of());
    }

    public Result call(String app, String method, String pathAndQuery, String jsonBody, Correlation c, String source,
                       int timeoutMs, boolean lab, Map<String, String> extraHeaders) {
        String base = apps.get(app);
        if (base == null) {
            return new Result(-1, null, 0, false, "bilinmeyen uygulama " + app);
        }
        return send(base + pathAndQuery, method, jsonBody, c, source, timeoutMs, lab, extraHeaders);
    }

    /** netem sidecar: uygulama ile aynı hostname, farklı port (aynı ağ namespace'i). */
    public String netemBase(String app) {
        URI u = URI.create(apps.get(app));
        return u.getScheme() + "://" + u.getHost() + ":" + netemPort;
    }

    public Result netemApply(String app, String target, int delayMs, int jitterMs, double lossPct, int ttlSec,
                             Correlation c) {
        String body = "{\"target\":\"" + target + "\",\"delayMs\":" + delayMs + ",\"jitterMs\":" + jitterMs
                + ",\"lossPct\":" + lossPct + ",\"ttlSec\":" + ttlSec + "}";
        return send(netemBase(app) + "/netem", "POST", body, c, "netem", 5000, true, Map.of());
    }

    public Result netemClear(String app, Correlation c) {
        return send(netemBase(app) + "/netem", "DELETE", null, c, "netem", 5000, true, Map.of());
    }

    public Result netemStatus(String app) {
        return send(netemBase(app) + "/netem", "GET", null, Correlation.none(), "netem", 3000, true, Map.of());
    }

    public Result cleanup(String app, Correlation c) {
        return call(app, "POST", "/lab/cleanup", null, c, "cleanup", 15_000, true);
    }

    private Result send(String url, String method, String body, Correlation c, String source, int timeoutMs,
                        boolean lab, Map<String, String> extraHeaders) {
        var b = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofMillis(Math.max(100, timeoutMs)))
                .header("Accept", "application/json")
                .header("X-Experiment-Id", c.experimentId())
                .header("X-Request-Id", c.experimentId() + "-" + seq.incrementAndGet())
                .header("X-Chaos-Source", source)
                .header("X-Chaos-Scenario", c.scenario())
                .header("X-Chaos-Initiator", c.initiator())
                .header("X-Chaos-Target", c.target())
                .header("X-Chaos-Intensity", c.intensity())
                .header("X-Chaos-Phase", c.phase().get());
        if (lab && labToken != null) {
            b.header("X-Lab-Token", labToken);
        }
        extraHeaders.forEach(b::header);
        switch (method) {
            case "POST" -> b.header("Content-Type", "application/json").POST(body == null
                    ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
            case "DELETE" -> b.DELETE();
            default -> b.GET();
        }
        long t0 = System.nanoTime();
        try {
            HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString());
            return new Result(r.statusCode(), r.body(), (System.nanoTime() - t0) / 1000, false, null);
        } catch (HttpTimeoutException e) {
            return new Result(-1, null, (System.nanoTime() - t0) / 1000, true, "timeout");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new Result(-1, null, (System.nanoTime() - t0) / 1000, false, "interrupted");
        } catch (Exception e) {
            return new Result(-1, null, (System.nanoTime() - t0) / 1000, false, e.getClass().getSimpleName());
        }
    }
}
