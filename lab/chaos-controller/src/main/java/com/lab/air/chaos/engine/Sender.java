package com.lab.air.chaos.engine;

import com.lab.air.chaos.engine.Stats.Outcome;
import com.lab.air.chaos.ops.HttpCall;
import com.lab.air.chaos.ops.OpDef;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Tek bir isteği gönderir, sonucu sınıflandırır, istatistik + Micrometer metriği üretir.
 * Metrik etiketleri düşük cardinality'lidir (app, operation, source, phase, outcome);
 * experiment_id/request_id YALNIZ HTTP başlığında taşınır (uygulama logları/SQL yorumu).
 */
public final class Sender {

    private final HttpClient http;
    private final Map<String, String> baseUrls;
    private final String labToken;
    private final String experimentId;
    private final Stats stats;
    private final MeterRegistry meters;
    private final OpDef.OpContext ctxTemplate;
    private final AtomicLong seq = new AtomicLong();

    public Sender(HttpClient http, Map<String, String> baseUrls, String labToken, String experimentId, Stats stats,
                  MeterRegistry meters, OpDef.OpContext ctxTemplate) {
        this.http = http;
        this.baseUrls = baseUrls;
        this.labToken = labToken;
        this.experimentId = experimentId;
        this.stats = stats;
        this.meters = meters;
        this.ctxTemplate = ctxTemplate;
    }

    public void send(OpDef op, HttpCall call, OpDef.OpContext ctx, Phase phase, String source, int timeoutMs) {
        String rid = experimentId + "-" + seq.incrementAndGet();
        var b = HttpRequest.newBuilder(URI.create(baseUrls.get(op.app()) + call.path()))
                .timeout(Duration.ofMillis(timeoutMs))
                .header("X-Experiment-Id", experimentId)
                .header("X-Request-Id", rid)
                .header("X-Chaos-Source", source)
                .header("Accept", "application/json");
        call.headers().forEach(b::header);
        if (call.lab() && labToken != null) {
            b.header("X-Lab-Token", labToken);
        }
        if ("POST".equals(call.method())) {
            b.header("Content-Type", "application/json")
                    .POST(call.jsonBody() == null ? HttpRequest.BodyPublishers.noBody()
                            : HttpRequest.BodyPublishers.ofString(call.jsonBody()));
        } else {
            b.GET();
        }
        stats.sent(phase, source, op.app(), op.name());
        long t0 = System.nanoTime();
        Outcome outcome;
        int status = -1;
        String body = null;
        try {
            HttpResponse<String> res = http.send(b.build(), HttpResponse.BodyHandlers.ofString());
            status = res.statusCode();
            body = res.body();
            outcome = status < 400 ? Outcome.OK : status == 409 ? Outcome.CONFLICT
                    : status < 500 ? Outcome.CLIENT_ERROR : Outcome.SERVER_ERROR;
        } catch (HttpTimeoutException e) {
            outcome = Outcome.TIMEOUT;
        } catch (IOException e) {
            outcome = Outcome.IO_ERROR;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            outcome = Outcome.IO_ERROR;
        }
        long us = (System.nanoTime() - t0) / 1000;
        stats.completed(phase, source, op.app(), op.name(), outcome, us);
        if (meters != null) {
            Counter.builder("chaos.requests").tag("app", op.app()).tag("operation", op.name()).tag("source", source)
                    .tag("phase", phase.name()).tag("outcome", outcome.name()).register(meters).increment();
            if (outcome != Outcome.TIMEOUT && outcome != Outcome.IO_ERROR) {
                Timer.builder("chaos.request.latency").tag("app", op.app()).tag("source", source)
                        .tag("phase", phase.name()).publishPercentileHistogram().register(meters)
                        .record(us, TimeUnit.MICROSECONDS);
            }
        }
        if (op.onResult() != null && status > 0) {
            try {
                op.onResult().accept(ctx, call, status, body);
            } catch (RuntimeException ignored) {
                // geri besleme en iyi çaba; ölçümü etkilemez
            }
        }
    }

    public OpDef.OpContext ctxTemplate() {
        return ctxTemplate;
    }
}
