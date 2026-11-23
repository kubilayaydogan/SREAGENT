package com.lab.air.flightops;

import com.lab.air.testsupport.LabPostgres;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Connection;
import java.sql.ResultSet;
import java.util.List;
import java.util.concurrent.CompletableFuture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Chaos v2 workload uçlarının PostgreSQL'de GERÇEK, ölçülebilir etki ürettiğini doğrular (Testcontainers PG17).
 * "Uç 200 döndü" yeterli sayılmaz: lock wait süresi, pg_stat_database deadlock/temp sayaçları, write_sink satırları,
 * başka bir oturumun bekletilmesi gibi sunucu tarafı kanıtlar ölçülür.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.DEFINED_PORT)
class ChaosLabIT {

    static final String LAB_TOKEN = "it-chaos-lab-token-0123";
    static final JsonMapper JSON = JsonMapper.builder().build();
    static final HttpClient HTTP = HttpClient.newHttpClient();
    static final int PORT = freePort();

    static int freePort() {
        try (ServerSocket s = new ServerSocket(0)) {
            return s.getLocalPort();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("server.port", () -> PORT);
        r.add("spring.datasource.url", () -> LabPostgres.jdbcUrl("flightops-service"));
        r.add("spring.datasource.username", () -> "svc_flightops");
        r.add("spring.datasource.password", () -> LabPostgres.SVC_PW);
        r.add("lab.enabled", () -> "true");
        r.add("lab.token", () -> LAB_TOKEN);
        r.add("lab.allowed-cidrs", () -> "127.0.0.1/32,0:0:0:0:0:0:0:1/128");
        r.add("lab.chaos.peers.flightops", () -> "http://localhost:" + PORT);
        r.add("lab.chaos.peers.baggage", () -> "http://127.0.0.1:1");          // erişilemez peer → dependency failure
        r.add("logging.structured.format.console", () -> "");
    }

    HttpResponse<String> post(String path) throws Exception {
        return HTTP.send(HttpRequest.newBuilder(URI.create("http://localhost:" + PORT + path))
                .POST(HttpRequest.BodyPublishers.noBody()).header("X-Lab-Token", LAB_TOKEN)
                .header("X-Experiment-Id", "it-exp").header("X-Chaos-Scenario", "S02").build(),
                HttpResponse.BodyHandlers.ofString());
    }

    CompletableFuture<HttpResponse<String>> postAsync(String path) {
        return CompletableFuture.supplyAsync(() -> {
            try {
                return post(path);
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        });
    }

    static long scalar(String sql) throws Exception {
        try (Connection c = LabPostgres.owner(); var st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    /** pg_stat_database sayaçları asenkron flush edilir; artışı en fazla 10 sn bekler. */
    static long waitForIncrease(String sql, long before) throws Exception {
        long v = before;
        for (int i = 0; i < 50 && v <= before; i++) {
            Thread.sleep(200);
            try (Connection c = LabPostgres.owner(); var st = c.createStatement()) {
                st.execute("SELECT pg_stat_clear_snapshot()");
                try (ResultSet rs = st.executeQuery(sql)) {
                    rs.next();
                    v = rs.getLong(1);
                }
            }
        }
        return v;
    }

    JsonNode json(HttpResponse<String> r) {
        return JSON.readTree(r.body());
    }

    @Test
    void endpointsRequireLabToken() throws Exception {
        var r = HTTP.send(HttpRequest.newBuilder(URI.create("http://localhost:" + PORT + "/lab/chaos/status")).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(401, r.statusCode());
    }

    @Test
    void rowContentionBlocksSecondTransactionForHoldDuration() throws Exception {
        var a = postAsync("/lab/chaos/contend?records=1&holdMs=1500");
        Thread.sleep(200);
        var b = post("/lab/chaos/contend?records=1&holdMs=0");
        assertEquals(200, a.get().statusCode(), a.get().body());
        assertEquals(200, b.statusCode(), b.body());
        assertTrue(json(b).get("lockWaitMs").asLong() >= 900, b.body());
    }

    @Test
    void businessLongTransactionBlocksOtherSessionsOnSameInventoryRow() throws Exception {
        long fid = scalar("SELECT flight_id FROM flightops.flight_inventory ORDER BY flight_id LIMIT 1");
        long before = scalar("SELECT extract(epoch FROM updated_at)*1000 FROM flightops.flight_inventory WHERE flight_id=" + fid);
        var hold = postAsync("/lab/chaos/long-tx?scope=business&flightId=" + fid + "&durationMs=2500");
        Thread.sleep(400);
        long t0 = System.nanoTime();
        try (Connection c = LabPostgres.owner(); var st = c.createStatement()) {
            st.execute("UPDATE flightops.flight_inventory SET version = version WHERE flight_id = " + fid);
        }
        long waitedMs = (System.nanoTime() - t0) / 1_000_000;
        assertEquals(200, hold.get().statusCode(), hold.get().body());
        assertTrue(waitedMs >= 1500, "diğer oturum " + waitedMs + " ms bekledi");
        long after = scalar("SELECT extract(epoch FROM updated_at)*1000 FROM flightops.flight_inventory WHERE flight_id=" + fid);
        assertEquals(before, after, "NO-OP update iş verisini değiştirmemeli");
    }

    @Test
    void opposingLockOrderProducesRealPostgresDeadlock() throws Exception {
        long before = scalar("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()");
        var ab = postAsync("/lab/chaos/deadlock?pair=7&order=ab&holdMs=700&retries=0");
        var ba = postAsync("/lab/chaos/deadlock?pair=7&order=ba&holdMs=700&retries=0");
        List<Integer> codes = List.of(ab.get().statusCode(), ba.get().statusCode());
        assertTrue(codes.contains(200), codes.toString());
        assertTrue(codes.stream().anyMatch(c -> c >= 400), "biri deadlock ile iptal edilmeli: " + codes);
        long after = waitForIncrease("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()", before);
        assertTrue(after > before, "pg_stat_database.deadlocks artmalı");
    }

    @Test
    void deadlockRetrySucceedsAfterAbort() throws Exception {
        var ab = postAsync("/lab/chaos/deadlock?pair=9&order=ab&holdMs=600&retries=3");
        var ba = postAsync("/lab/chaos/deadlock?pair=9&order=ba&holdMs=600&retries=3");
        assertEquals(200, ab.get().statusCode(), ab.get().body());
        assertEquals(200, ba.get().statusCode(), ba.get().body());
        long dl = json(ab.get()).get("deadlocks").asLong() + json(ba.get()).get("deadlocks").asLong();
        assertTrue(dl >= 1, "retry gözlemlenebilir olmalı: " + ab.get().body() + " " + ba.get().body());
    }

    @Test
    void tempSpillWritesRealTempFiles() throws Exception {
        long before = scalar("SELECT temp_files FROM pg_stat_database WHERE datname = current_database()");
        var r = post("/lab/chaos/temp-spill?kRows=60");
        assertEquals(200, r.statusCode(), r.body());
        assertTrue(waitForIncrease("SELECT temp_files FROM pg_stat_database WHERE datname = current_database()", before) > before);
    }

    @Test
    void writeBurstInsertsAndCleanupEmptiesSink() throws Exception {
        var r = post("/lab/chaos/write-burst?rows=800&payloadBytes=128");
        assertEquals(200, r.statusCode(), r.body());
        assertTrue(scalar("SELECT count(*) FROM chaos.write_sink WHERE experiment_id = 'it-exp'") >= 800);
        assertEquals(200, post("/lab/cleanup").statusCode());
        assertEquals(0, scalar("SELECT count(*) FROM chaos.write_sink"));
    }

    @Test
    void cpuAndHeavyReadQueriesRunAgainstSyntheticData() throws Exception {
        var cpu = post("/lab/chaos/cpu-query?work=50");
        assertEquals(200, cpu.statusCode(), cpu.body());
        assertEquals(50_000, json(cpu).get("rows").asLong());
        var read = post("/lab/chaos/heavy-read?scanPct=100");
        assertEquals(200, read.statusCode(), read.body());
        assertTrue(json(read).get("matched").asLong() > 0);
    }

    @Test
    void connectionStormOpensSeparatePhysicalConnections() throws Exception {
        var r = postAsync("/lab/chaos/connection-storm?count=6&holdMs=1500");
        Thread.sleep(800);
        long storm = scalar("SELECT count(*) FROM pg_stat_activity WHERE application_name = 'flightops-service-lab-storm'");
        assertEquals(200, r.get().statusCode(), r.get().body());
        assertEquals(6, json(r.get()).get("opened").asLong());
        assertTrue(storm >= 4, "pg_stat_activity'de storm bağlantıları: " + storm);
    }

    @Test
    void errorInjectionProducesGenuineDbErrorAnd5xx() throws Exception {
        var r = post("/lab/chaos/contend?records=50&holdMs=0&errorPct=100");
        assertTrue(r.statusCode() >= 500, r.statusCode() + " " + r.body());
    }

    @Test
    void memoryAndCpuAreBoundedAndReleasedByCleanup() throws Exception {
        var m = post("/lab/chaos/memory?mb=16&holdMs=60000");
        assertEquals(200, m.statusCode(), m.body());
        assertEquals(16, json(m).get("allocatedMb").asLong());
        var huge = post("/lab/chaos/memory?mb=4096&holdMs=1000");
        assertTrue(json(huge).get("allocatedMb") == null || json(huge).get("allocatedMb").asLong() < 4096, huge.body());
        var burn = post("/lab/chaos/cpu-burn?ms=300&threads=1");
        assertEquals(200, burn.statusCode(), burn.body());
        var clean = json(post("/lab/cleanup"));
        assertTrue(clean.get("chaos").get("ballastFreedMb").asLong() >= 16, clean.toString());
        var status = json(HTTP.send(HttpRequest.newBuilder(URI.create("http://localhost:" + PORT + "/lab/chaos/status"))
                .header("X-Lab-Token", LAB_TOKEN).GET().build(), HttpResponse.BodyHandlers.ofString()));
        assertEquals(0, status.get("ballastMb").asLong());
    }

    @Test
    void dependencyCallRetriesAndReportsAmplification() throws Exception {
        var ok = post("/lab/chaos/dependency-call?peer=flightops&timeoutMs=2000&retries=2");
        assertEquals(200, ok.statusCode(), ok.body());
        assertEquals(1, json(ok).get("attempts").asLong());
        var failed = post("/lab/chaos/dependency-call?peer=baggage&timeoutMs=300&retries=3&backoffMs=10");
        assertEquals(502, failed.statusCode(), failed.body());
        assertTrue(failed.body().contains("4 denemede"), failed.body());
        assertEquals(400, post("/lab/chaos/dependency-call?peer=evil").statusCode());
    }
}
