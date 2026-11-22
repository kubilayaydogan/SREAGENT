package com.lab.air.baggage;

import com.lab.air.testsupport.LabPostgres;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.json.JsonMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Connection;
import java.sql.ResultSet;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class BaggageIT {

    static final JsonMapper JSON = JsonMapper.builder().build();
    static final HttpClient HTTP = HttpClient.newHttpClient();

    @LocalServerPort
    int port;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", () -> LabPostgres.jdbcUrl("baggage-service"));
        r.add("spring.datasource.username", () -> "svc_baggage");
        r.add("spring.datasource.password", () -> LabPostgres.SVC_PW);
        r.add("logging.structured.format.console", () -> "");
    }

    HttpResponse<String> call(String method, String path, String body, String... headers) throws Exception {
        var b = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body))
                .header("Content-Type", "application/json");
        for (int i = 0; i < headers.length; i += 2) {
            b.header(headers[i], headers[i + 1]);
        }
        return HTTP.send(b.build(), HttpResponse.BodyHandlers.ofString());
    }

    String candidate() throws Exception {
        return JSON.readTree(call("GET", "/api/bags/candidates?limit=1", null).body()).get(0).asString();
    }

    long scalar(String sql) throws Exception {
        try (Connection c = LabPostgres.owner(); var st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    @Test
    void registerIsIdempotentAndLimitedPerBooking() throws Exception {
        String pnr = candidate();
        long existing = scalar("SELECT count(*) FROM baggage.bag g JOIN reservation.booking b USING (booking_id) WHERE b.pnr='" + pnr + "'");
        String key = "bag-" + UUID.randomUUID();
        String body = "{\"pnr\":\"" + pnr + "\",\"weightKg\":20.5}";
        var r1 = call("POST", "/api/bags", body, "Idempotency-Key", key);
        assertEquals(201, r1.statusCode(), r1.body());
        String tag = JSON.readTree(r1.body()).at("/detail/bag/tag").asString();
        var r2 = call("POST", "/api/bags", body, "Idempotency-Key", key);
        assertEquals(200, r2.statusCode());
        assertEquals(tag, JSON.readTree(r2.body()).at("/detail/bag/tag").asString());

        for (long i = existing + 1; i < 3; i++) {
            assertEquals(201, call("POST", "/api/bags", body).statusCode());
        }
        var over = call("POST", "/api/bags", body);
        assertEquals(409, over.statusCode());
        assertTrue(over.body().contains("BAG_LIMIT"));

        assertEquals(200, call("POST", "/api/bags/" + tag + "/events", "{\"eventType\":\"LOADED\",\"location\":\"RAMP\"}").statusCode());
        var bad = call("POST", "/api/bags/" + tag + "/events", "{\"eventType\":\"DELIVERED\",\"location\":\"IST\"}");
        assertEquals(409, bad.statusCode());
        var detail = JSON.readTree(call("GET", "/api/bags/" + tag, null).body());
        assertEquals("LOADED", detail.at("/bag/status").asString());
        assertEquals(2, detail.get("events").size());
        assertEquals(0, scalar("SELECT coalesce(sum(violations),0) FROM lab.integrity_check"));
    }

    @Test
    void baggageIsNotBlockedByHeldInventoryLock() throws Exception {
        String pnr = candidate();
        long fid = scalar("SELECT flight_id FROM reservation.booking WHERE pnr='" + pnr + "'");
        try (Connection holder = LabPostgres.owner()) {
            holder.setAutoCommit(false);
            try (var st = holder.createStatement()) {
                st.executeUpdate("UPDATE flightops.flight_inventory SET version = version WHERE flight_id = " + fid);
            }
            long t0 = System.nanoTime();
            var r = call("POST", "/api/bags", "{\"pnr\":\"" + pnr + "\",\"weightKg\":10}");
            long ms = (System.nanoTime() - t0) / 1_000_000;
            assertTrue(r.statusCode() == 201 || r.statusCode() == 409, r.body());
            assertTrue(ms < 2000, "bagaj kaydı envanter kilidinde beklememeli, süre=" + ms);
            holder.rollback();
        }
    }

    /** V5 zinciri: check-in sayacı başka bir transaction'da kilitliyken bagaj kaydı bekler, kilit bırakılınca tamamlanır. */
    @Test
    void registerWaitsForHeldCheckinCounterThenCompletes() throws Exception {
        String pnr = candidate();
        long fid = scalar("SELECT flight_id FROM reservation.booking WHERE pnr='" + pnr + "'");
        try (Connection setup = LabPostgres.owner(); var st = setup.createStatement()) {   // satır commit'li var olmalı
            st.executeUpdate("INSERT INTO checkin.flight_checkin_stats(flight_id) VALUES (" + fid + ") ON CONFLICT DO NOTHING");
        }
        try (Connection holder = LabPostgres.owner()) {
            holder.setAutoCommit(false);
            try (var st = holder.createStatement()) {   // check-in transaction'ını taklit: sayaç satırını kilitle
                st.executeUpdate("UPDATE checkin.flight_checkin_stats SET updated_at = now() WHERE flight_id = " + fid);
            }
            var pending = HTTP.sendAsync(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/bags"))
                    .POST(HttpRequest.BodyPublishers.ofString("{\"pnr\":\"" + pnr + "\",\"weightKg\":12}"))
                    .header("Content-Type", "application/json").build(), HttpResponse.BodyHandlers.ofString());
            Thread.sleep(1500);
            assertFalse(pending.isDone(), "bagaj kaydı check-in sayacı kilidini beklemeliydi");
            holder.rollback();
            var r = pending.get(10, TimeUnit.SECONDS);
            assertTrue(r.statusCode() == 201 || r.statusCode() == 409, r.body());
        }
        assertEquals(0, scalar("SELECT coalesce(sum(violations),0) FROM lab.integrity_check"));
    }

    @Test
    void reportsAndUi() throws Exception {
        var h = call("GET", "/api/reports/bag-history?days=60", null);
        assertEquals(200, h.statusCode(), h.body());
        assertTrue(JSON.readTree(h.body()).size() > 0);
        String tag = JSON.readTree(call("GET", "/api/bags/sample?limit=1", null).body()).get(0).asString();
        assertEquals(200, call("GET", "/ui/bags/" + tag, null).statusCode());
        assertEquals(200, call("GET", "/", null).statusCode());
        assertEquals(404, call("GET", "/api/bags/NOPE", null).statusCode());
    }
}
