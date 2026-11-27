package com.lab.air.checkin;

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
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class CheckinIT {

    static final JsonMapper JSON = JsonMapper.builder().build();
    static final HttpClient HTTP = HttpClient.newHttpClient();

    @LocalServerPort
    int port;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", () -> LabPostgres.jdbcUrl("checkin-service"));
        r.add("spring.datasource.username", () -> "svc_checkin");
        r.add("spring.datasource.password", () -> LabPostgres.SVC_PW);
        r.add("logging.structured.format.console", () -> "");
    }

    HttpResponse<String> call(String method, String path, String body) throws Exception {
        return HTTP.send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body))
                .header("Content-Type", "application/json").build(), HttpResponse.BodyHandlers.ofString());
    }

    long scalar(String sql) throws Exception {
        try (Connection c = LabPostgres.owner(); var st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    String candidate() throws Exception {
        var arr = JSON.readTree(call("GET", "/api/checkin/candidates?limit=1", null).body());
        return arr.get(0).get("pnr").asString();
    }

    @Test
    void concurrentCheckinIsIdempotentAndCountsOnce() throws Exception {
        String pnr = candidate();
        long fid = scalar("SELECT flight_id FROM reservation.booking WHERE pnr='" + pnr + "'");
        long before = scalar("SELECT checked_in FROM flightops.flight_inventory WHERE flight_id=" + fid);

        List<CompletableFuture<Integer>> fs = new ArrayList<>();
        try (var ex = Executors.newFixedThreadPool(5)) {
            for (int i = 0; i < 5; i++) {
                fs.add(CompletableFuture.supplyAsync(() -> {
                    try {
                        return call("POST", "/api/checkin", "{\"pnr\":\"" + pnr + "\",\"channel\":\"WEB\"}").statusCode();
                    } catch (Exception e) {
                        return -1;
                    }
                }, ex));
            }
        }
        var codes = fs.stream().map(CompletableFuture::join).toList();
        assertEquals(1, codes.stream().filter(c -> c == 201).count(), codes.toString());
        assertEquals(4, codes.stream().filter(c -> c == 200).count(), codes.toString());
        assertEquals(before + 1, scalar("SELECT checked_in FROM flightops.flight_inventory WHERE flight_id=" + fid));
        assertEquals(0, scalar("SELECT coalesce(sum(violations),0) FROM lab.integrity_check"));
    }

    @Test
    void seatsAreUniqueAndRequireCheckin() throws Exception {
        var arr = JSON.readTree(call("GET", "/api/checkin/candidates?limit=50", null).body());
        // aynı uçuştan iki aday bul
        String a = null, b = null;
        outer:
        for (int i = 0; i < arr.size(); i++) {
            for (int j = i + 1; j < arr.size(); j++) {
                if (arr.get(i).get("flightId").asLong() == arr.get(j).get("flightId").asLong()) {
                    a = arr.get(i).get("pnr").asString();
                    b = arr.get(j).get("pnr").asString();
                    break outer;
                }
            }
        }
        if (a == null) {
            long fid = arr.get(0).get("flightId").asLong();
            var same = JSON.readTree(call("GET", "/api/checkin/candidates?limit=2&flightId=" + fid, null).body());
            a = same.get(0).get("pnr").asString();
            b = same.get(1).get("pnr").asString();
        }
        assertEquals(409, call("POST", "/api/checkin/" + a + "/seat", "{\"seatNo\":\"30F\"}").statusCode(),
                "check-in olmadan koltuk atanmamalı");
        assertEquals(201, call("POST", "/api/checkin", "{\"pnr\":\"" + a + "\"}").statusCode());
        assertEquals(201, call("POST", "/api/checkin", "{\"pnr\":\"" + b + "\"}").statusCode());

        // seed'de olmayan bir sıra seç
        String seat = "25F";
        var r1 = call("POST", "/api/checkin/" + a + "/seat", "{\"seatNo\":\"" + seat + "\"}");
        if (r1.statusCode() == 409) {
            seat = "24E";
            r1 = call("POST", "/api/checkin/" + a + "/seat", "{\"seatNo\":\"" + seat + "\"}");
        }
        assertEquals(200, r1.statusCode(), r1.body());
        var replay = call("POST", "/api/checkin/" + a + "/seat", "{\"seatNo\":\"" + seat + "\"}");
        assertTrue(JSON.readTree(replay.body()).get("replayed").asBoolean());
        var taken = call("POST", "/api/checkin/" + b + "/seat", "{\"seatNo\":\"" + seat + "\"}");
        assertEquals(409, taken.statusCode());
        assertTrue(taken.body().contains("SEAT_TAKEN"));
        assertEquals(400, call("POST", "/api/checkin/" + b + "/seat", "{\"seatNo\":\"0Z\"}").statusCode());
    }

    @Test
    void cancelledBookingCannotCheckIn() throws Exception {
        String pnr = candidate();
        try (Connection c = LabPostgres.owner(); var st = c.createStatement()) {
            st.execute("UPDATE reservation.booking SET status='CANCELLED', cancelled_at=now() WHERE pnr='" + pnr + "'");
            st.execute("UPDATE flightops.flight_inventory i SET sold = sold - 1 FROM reservation.booking b "
                    + "WHERE b.pnr='" + pnr + "' AND i.flight_id=b.flight_id");
        }
        var r = call("POST", "/api/checkin", "{\"pnr\":\"" + pnr + "\"}");
        assertEquals(409, r.statusCode());
        assertTrue(r.body().contains("BOOKING_CANCELLED"));
        assertEquals(404, call("GET", "/api/checkin/NOPE99", null).statusCode());
        assertEquals(200, call("GET", "/", null).statusCode());
        assertEquals(0, scalar("SELECT coalesce(sum(violations),0) FROM lab.integrity_check"));
    }
}
