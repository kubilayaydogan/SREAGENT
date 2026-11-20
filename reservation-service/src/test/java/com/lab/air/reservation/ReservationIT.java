package com.lab.air.reservation;

import com.lab.air.testsupport.LabPostgres;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Connection;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Gerçek PG17 + gerçek roller/migration/seed üzerinde Reservation dikey parça testleri. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ReservationIT {

    static final String LAB_TOKEN = "test-lab-token-0123456789";
    static final JsonMapper JSON = JsonMapper.builder().build();
    static final HttpClient HTTP = HttpClient.newHttpClient();

    @LocalServerPort
    int port;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", () -> LabPostgres.jdbcUrl("reservation-service"));
        r.add("spring.datasource.username", () -> "svc_reservation");
        r.add("spring.datasource.password", () -> LabPostgres.SVC_PW);
        r.add("lab.enabled", () -> "true");
        r.add("lab.token", () -> LAB_TOKEN);
        r.add("lab.allowed-cidrs", () -> "127.0.0.1/32,0:0:0:0:0:0:0:1/128");
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

    long scalar(String sql) throws Exception {
        try (Connection c = LabPostgres.owner(); var st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    long futureFlight() throws Exception {
        return scalar("SELECT f.flight_id FROM flightops.flight f JOIN flightops.flight_inventory i USING (flight_id) "
                + "WHERE NOT is_hot AND status='SCHEDULED' AND dep_time > now() + interval '3 days' AND sold < capacity - 5 "
                + "ORDER BY flight_id LIMIT 1");
    }

    String bookingJson(long flightId) {
        return "{\"flightId\":" + flightId + ",\"firstName\":\"Test\",\"lastName\":\"User\",\"email\":\"t@example.test\",\"fareClass\":\"Y\"}";
    }

    @Test
    void createIsPersistedAndIdempotent() throws Exception {
        long fid = futureFlight();
        long soldBefore = scalar("SELECT sold FROM flightops.flight_inventory WHERE flight_id=" + fid);
        String key = "it-" + UUID.randomUUID();

        var r1 = call("POST", "/api/bookings", bookingJson(fid), "Idempotency-Key", key, "X-Experiment-Id", "exp-it-1");
        assertEquals(201, r1.statusCode(), r1.body());
        String pnr = JSON.readTree(r1.body()).at("/booking/pnr").asString();

        var r2 = call("POST", "/api/bookings", bookingJson(fid), "Idempotency-Key", key);
        assertEquals(200, r2.statusCode());
        assertEquals(pnr, JSON.readTree(r2.body()).at("/booking/pnr").asString());
        assertTrue(JSON.readTree(r2.body()).get("replayed").asBoolean());

        assertEquals(soldBefore + 1, scalar("SELECT sold FROM flightops.flight_inventory WHERE flight_id=" + fid));
        assertEquals(1, scalar("SELECT count(*) FROM reservation.booking WHERE idempotency_key='" + key + "'"));

        // İptal → sold geri döner; ikinci iptal idempotent
        var c1 = call("POST", "/api/bookings/" + pnr + "/cancel", null);
        assertEquals(200, c1.statusCode(), c1.body());
        assertEquals("CANCELLED", JSON.readTree(c1.body()).at("/booking/status").asString());
        var c2 = call("POST", "/api/bookings/" + pnr + "/cancel", null);
        assertTrue(JSON.readTree(c2.body()).get("replayed").asBoolean());
        assertEquals(soldBefore, scalar("SELECT sold FROM flightops.flight_inventory WHERE flight_id=" + fid));
        assertEquals(0, scalar("SELECT coalesce(sum(violations),0) FROM lab.integrity_check"));
    }

    @Test
    void concurrentBookingsNeverOverbook() throws Exception {
        long fid = scalar("SELECT flight_id FROM flightops.flight WHERE NOT is_hot AND status='SCHEDULED' "
                + "AND dep_time > now() + interval '5 days' ORDER BY flight_id DESC LIMIT 1");
        try (Connection c = LabPostgres.owner(); var st = c.createStatement()) {
            st.execute("UPDATE flightops.flight_inventory SET capacity = sold + 3 WHERE flight_id = " + fid);
        }
        List<CompletableFuture<Integer>> fs = new ArrayList<>();
        try (var ex = Executors.newFixedThreadPool(10)) {
            for (int i = 0; i < 10; i++) {
                fs.add(CompletableFuture.supplyAsync(() -> {
                    try {
                        return call("POST", "/api/bookings", bookingJson(fid)).statusCode();
                    } catch (Exception e) {
                        return -1;
                    }
                }, ex));
            }
        }
        long created = fs.stream().map(CompletableFuture::join).filter(s -> s == 201).count();
        long soldOut = fs.stream().map(CompletableFuture::join).filter(s -> s == 409).count();
        assertEquals(3, created);
        assertEquals(7, soldOut);
        assertEquals(0, scalar("SELECT count(*) FROM flightops.flight_inventory WHERE sold > capacity"));
        assertEquals(0, scalar("SELECT coalesce(sum(violations),0) FROM lab.integrity_check"));
    }

    @Test
    void labHoldCreatesRealBlockingChainVisibleInPgBlockingPids() throws Exception {
        assertEquals(401, call("POST", "/lab/bookings/hold", "{\"holdMs\":10}").statusCode());

        long hot = scalar("SELECT flight_id FROM flightops.flight WHERE is_hot ORDER BY flight_id LIMIT 1");
        var holder = CompletableFuture.supplyAsync(() -> {
            try {
                return call("POST", "/lab/bookings/hold", "{\"flightId\":" + hot + ",\"holdMs\":3000}",
                        "X-Lab-Token", LAB_TOKEN, "X-Experiment-Id", "exp-lock-it");
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        });
        Thread.sleep(700);

        // Check-in rolünün gerçek iş UPDATE'i ile aynı satırı güncelleyen ikinci oturum
        var waiter = CompletableFuture.supplyAsync(() -> {
            try (Connection c = LabPostgres.as("svc_checkin", "checkin-service"); var st = c.createStatement()) {
                long t0 = System.nanoTime();
                st.executeUpdate("UPDATE flightops.flight_inventory SET version = version + 1 WHERE flight_id = " + hot);
                return (System.nanoTime() - t0) / 1_000_000;
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        });
        Thread.sleep(700);

        String chain;
        // pg_monitor yetkili salt-okur rol: başka rollerin oturum ayrıntılarını görebilir
        try (Connection c = LabPostgres.as("lab_monitor", "it-monitor"); var st = c.createStatement(); ResultSet rs = st.executeQuery("""
                SELECT w.application_name || '>' || b.application_name || '|' || w.usename || '>' || b.usename
                       || '|' || w.wait_event_type || '|' || (b.query LIKE '%exp=exp-lock-it%')
                  FROM pg_stat_activity w
                  JOIN LATERAL unnest(pg_blocking_pids(w.pid)) bp(pid) ON true
                  JOIN pg_stat_activity b ON b.pid = bp.pid
                 WHERE w.application_name = 'checkin-service'""")) {
            assertTrue(rs.next(), "blocking zinciri bulunamadı");
            chain = rs.getString(1);
        }
        assertEquals("checkin-service>reservation-service|svc_checkin>svc_reservation|Lock|true", chain);

        assertEquals(200, holder.get().statusCode(), holder.get().body());
        long waitedMs = waiter.get();
        assertTrue(waitedMs >= 1000, "bekleme süresi beklenenden kısa: " + waitedMs);
    }

    @Test
    void uiPagesRender() throws Exception {
        var r = call("GET", "/", null);
        assertEquals(200, r.statusCode());
        assertTrue(r.body().contains("Uçuş ara"));
        var sample = JSON.readTree(call("GET", "/api/bookings/sample?limit=5", null).body());
        JsonNode first = sample.get(0);
        var page = call("GET", "/ui/bookings/" + first.asString(), null);
        assertTrue(page.body().contains(first.asString()));
        assertEquals(200, call("GET", "/api/reports/route-demand?days=60", null).statusCode());
        assertTrue(call("GET", "/actuator/prometheus", null).body().contains("hikaricp_connections_active"));
    }
}
