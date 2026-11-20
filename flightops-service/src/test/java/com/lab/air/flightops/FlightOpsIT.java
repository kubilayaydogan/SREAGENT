package com.lab.air.flightops;

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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class FlightOpsIT {

    static final JsonMapper JSON = JsonMapper.builder().build();
    static final HttpClient HTTP = HttpClient.newHttpClient();
    static final String LAB_TOKEN = "test-lab-token-0123456789";

    @LocalServerPort
    int port;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", () -> LabPostgres.jdbcUrl("flightops-service"));
        r.add("spring.datasource.username", () -> "svc_flightops");
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

    @Test
    void statusTransitionsAreValidatedAndIdempotent() throws Exception {
        long id = JSON.readTree(call("GET", "/api/flights/sample?limit=1", null).body()).get(0).asLong();
        var r1 = call("POST", "/api/flights/" + id + "/status", "{\"status\":\"DELAYED\",\"gate\":\"B12\",\"delayMin\":25}");
        assertEquals(200, r1.statusCode(), r1.body());
        assertFalse(JSON.readTree(r1.body()).get("replayed").asBoolean());
        var r2 = call("POST", "/api/flights/" + id + "/status", "{\"status\":\"DELAYED\",\"gate\":\"B12\",\"delayMin\":25}");
        assertTrue(JSON.readTree(r2.body()).get("replayed").asBoolean());
        assertEquals(1, scalar("SELECT count(*) FROM flightops.ops_event WHERE flight_id=" + id));

        var bad = call("POST", "/api/flights/" + id + "/status", "{\"status\":\"ARRIVED\"}");
        assertEquals(409, bad.statusCode());
        assertTrue(bad.body().contains("INVALID_TRANSITION"));
        assertEquals(400, call("POST", "/api/flights/" + id + "/status", "{\"status\":\"FLYING\"}").statusCode());
    }

    @Test
    void capacityCannotDropBelowSold() throws Exception {
        long id = JSON.readTree(call("GET", "/api/flights/sample?limit=1", null).body()).get(0).asLong();
        long sold = scalar("SELECT sold FROM flightops.flight_inventory WHERE flight_id=" + id);
        var low = call("POST", "/api/flights/" + id + "/capacity", "{\"capacity\":" + Math.max(1, sold - 1) + "}");
        if (sold > 1) {
            assertEquals(409, low.statusCode(), low.body());
            assertTrue(low.body().contains("CAPACITY_BELOW_SOLD"));
        }
        var ok = call("POST", "/api/flights/" + id + "/capacity", "{\"capacity\":" + (sold + 40) + "}");
        assertEquals(200, ok.statusCode(), ok.body());
        assertEquals(sold + 40, scalar("SELECT capacity FROM flightops.flight_inventory WHERE flight_id=" + id));
    }

    @Test
    void reportsAndLabEndpoints() throws Exception {
        var lf = JSON.readTree(call("GET", "/api/reports/load-factor?days=30&limit=500", null).body());
        assertTrue(lf.size() > 0);
        for (var row : lf) {
            assertFalse(row.get("inventoryMismatch").asBoolean(), "seed sonrası envanter tutarsız: " + row);
        }
        assertEquals(401, call("POST", "/lab/flights/capacity-hold", "{\"holdMs\":10}").statusCode());
        var hold = call("POST", "/lab/flights/capacity-hold", "{\"holdMs\":50}", "X-Lab-Token", LAB_TOKEN);
        assertEquals(200, hold.statusCode(), hold.body());
        var conn = call("POST", "/lab/hold-connection?holdMs=50&pool=pressure", null, "X-Lab-Token", LAB_TOKEN);
        assertEquals(200, conn.statusCode(), conn.body());
        var cleanup = call("POST", "/lab/cleanup", null, "X-Lab-Token", LAB_TOKEN);
        assertEquals(200, cleanup.statusCode(), cleanup.body());
        assertTrue(cleanup.body().contains("cancelledBackends"));
        assertEquals(200, call("GET", "/", null).statusCode());
    }
}
