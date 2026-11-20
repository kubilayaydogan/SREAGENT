package com.lab.air.chaos.ops;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/**
 * Yük üreticisinin kullandığı gerçek veri kimlikleri. Yalnız uygulamaların
 * HTTP API'lerinden doldurulur (DB'ye bağlanılmaz).
 */
public final class DataPools {

    private static final Logger log = LoggerFactory.getLogger(DataPools.class);

    public final BoundedPool<Long> resFlights = new BoundedPool<>(500);
    public final BoundedPool<Long> hotFlights = new BoundedPool<>(32);
    public final BoundedPool<Long> opsFlights = new BoundedPool<>(500);
    public final BoundedPool<String> pnrs = new BoundedPool<>(1000);
    public final BoundedPool<String> cancellable = new BoundedPool<>(2000);
    public final BoundedPool<String> checkinCandidates = new BoundedPool<>(2000);
    public final BoundedPool<String> hotCheckinCandidates = new BoundedPool<>(5000);
    public final BoundedPool<String> checkedIn = new BoundedPool<>(2000);
    public final BoundedPool<String> bagCandidates = new BoundedPool<>(2000);
    public final BoundedPool<String> bagTags = new BoundedPool<>(1000);
    /** [etiket, sıradaki olay] — yalnız bu üreticinin kaydettiği bagajlar. */
    public final BoundedPool<String[]> bagProgress = new BoundedPool<>(2000);

    private final Map<String, String> apps;
    private final HttpClient http;
    private final JsonMapper json;

    public DataPools(Map<String, String> apps, HttpClient http, JsonMapper json) {
        this.apps = apps;
        this.http = http;
        this.json = json;
    }

    /** Tüm havuzları API'lerden yeniler. Hata olursa mevcut içerik korunur. */
    public void refresh() {
        replace(resFlights, "reservation", "/api/flights?flexDays=14&limit=200", n -> n.get("flightId").asLong());
        replace(hotFlights, "reservation", "/api/flights/hot", n -> n.get("flightId").asLong());
        replace(pnrs, "reservation", "/api/bookings/sample?limit=500", JsonNode::asString);
        add(checkinCandidates, "checkin", "/api/checkin/candidates?limit=300", n -> n.get("pnr").asString());
        if (hotCheckinCandidates.size() < 1000) {
            add(hotCheckinCandidates, "checkin", "/api/checkin/candidates?hot=true&limit=500", n -> n.get("pnr").asString());
        }
        replace(opsFlights, "flightops", "/api/flights/sample?limit=200", JsonNode::asLong);
        replace(bagTags, "baggage", "/api/bags/sample?limit=300", JsonNode::asString);
        add(bagCandidates, "baggage", "/api/bags/candidates?limit=300", JsonNode::asString);
    }

    public String summary() {
        return "resFlights=" + resFlights.size() + " hotFlights=" + hotFlights.size() + " opsFlights=" + opsFlights.size()
                + " pnrs=" + pnrs.size() + " checkinCandidates=" + checkinCandidates.size()
                + " hotCheckinCandidates=" + hotCheckinCandidates.size() + " checkedIn=" + checkedIn.size()
                + " bagCandidates=" + bagCandidates.size() + " bagTags=" + bagTags.size()
                + " bagProgress=" + bagProgress.size() + " cancellable=" + cancellable.size();
    }

    private <T> void replace(BoundedPool<T> pool, String app, String path, Function<JsonNode, T> f) {
        List<T> l = fetch(app, path, f);
        if (!l.isEmpty()) {
            pool.replaceAll(l);
        }
    }

    private <T> void add(BoundedPool<T> pool, String app, String path, Function<JsonNode, T> f) {
        pool.addAll(fetch(app, path, f));
    }

    private <T> List<T> fetch(String app, String path, Function<JsonNode, T> f) {
        String base = apps.get(app);
        List<T> out = new ArrayList<>();
        if (base == null) {
            return out;
        }
        try {
            var res = http.send(HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofSeconds(10))
                    .header("X-Request-Id", "chaos-pool-refresh").GET().build(), HttpResponse.BodyHandlers.ofString());
            if (res.statusCode() == 200) {
                for (JsonNode n : json.readTree(res.body())) {
                    out.add(f.apply(n));
                }
            } else {
                log.warn("havuz yenileme {} {} -> HTTP {}", app, path, res.statusCode());
            }
        } catch (Exception e) {
            log.warn("havuz yenileme {} {} başarısız: {}", app, path, e.toString());
        }
        return out;
    }
}
