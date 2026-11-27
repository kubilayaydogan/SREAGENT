package com.lab.air.chaos;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/** Dört uygulamayı taklit eden küçük HTTP sunucusu (yalnız chaos-controller birim testleri için). */
final class StubApps implements AutoCloseable {

    final HttpServer server;
    final AtomicInteger cleanupCalls = new AtomicInteger();
    final AtomicInteger businessCalls = new AtomicInteger();
    final AtomicInteger concurrent = new AtomicInteger();
    final AtomicInteger maxConcurrent = new AtomicInteger();
    final AtomicInteger labTokenSeen = new AtomicInteger();
    final AtomicInteger experimentHeaderSeen = new AtomicInteger();
    volatile long delayMs;

    StubApps(long delayMs) throws IOException {
        this.delayMs = delayMs;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        server.createContext("/", this::handle);
        server.start();
    }

    String base() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    Map<String, String> apps() {
        return Map.of("reservation", base(), "checkin", base(), "flightops", base(), "baggage", base());
    }

    private void handle(HttpExchange ex) throws IOException {
        String p = ex.getRequestURI().getPath();
        String body;
        if (p.equals("/api/flights/hot")) {
            body = "[{\"flightId\":9001}]";
        } else if (p.equals("/api/flights") && ex.getRequestURI().getQuery() != null
                && ex.getRequestURI().getQuery().contains("flexDays=14")) {
            body = "[{\"flightId\":1},{\"flightId\":2}]";
        } else if (p.equals("/api/bookings/sample")) {
            body = "[\"P00001\",\"P00002\"]";
        } else if (p.equals("/api/checkin/candidates")) {
            body = "[{\"pnr\":\"P00001\",\"flightId\":1},{\"pnr\":\"P00002\",\"flightId\":1}]";
        } else if (p.equals("/api/flights/sample")) {
            body = "[1,2]";
        } else if (p.equals("/api/bags/sample")) {
            body = "[\"T1\"]";
        } else if (p.equals("/api/bags/candidates")) {
            body = "[\"P00001\"]";
        } else if (p.equals("/lab/cleanup")) {
            cleanupCalls.incrementAndGet();
            body = "{\"cancelledBackends\":0}";
        } else {
            businessCalls.incrementAndGet();
            if (ex.getRequestHeaders().getFirst("X-Lab-Token") != null) {
                labTokenSeen.incrementAndGet();
            }
            if (ex.getRequestHeaders().getFirst("X-Experiment-Id") != null) {
                experimentHeaderSeen.incrementAndGet();
            }
            int c = concurrent.incrementAndGet();
            maxConcurrent.accumulateAndGet(c, Math::max);
            try {
                Thread.sleep(delayMs);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } finally {
                concurrent.decrementAndGet();
            }
            body = "{}";
        }
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(200, b.length);
        ex.getResponseBody().write(b);
        ex.close();
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
