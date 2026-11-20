package com.lab.air.chaos.ops;

import com.lab.air.chaos.ops.OpDef.Kind;
import com.lab.air.chaos.ops.OpDef.OpContext;
import tools.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Uygulama başına operasyon kataloğu ve varsayılan "normal" karışımlar.
 * Her operasyon gerçek bir REST ucunu çağırır; keyfi SQL veya serbest yol yoktur.
 */
public final class Catalog {

    public static final List<String> APPS = List.of("reservation", "checkin", "flightops", "baggage");
    private static final String[] AIRPORTS = {"IST", "SAW", "ESB", "ADB", "AYT", "LHR", "CDG", "FRA", "AMS", "JFK", "DXB"};
    private static final String[] BAG_FLOW = {"LOADED", "IN_TRANSIT", "ARRIVED", "DELIVERED"};

    private final Map<String, Map<String, OpDef>> ops = new LinkedHashMap<>();
    private final Map<String, Map<String, Integer>> normalMix = new LinkedHashMap<>();

    public Catalog() {
        // ---------------- Reservation ----------------
        add(new OpDef("reservation", "search_flights", Kind.READ, "Uçuş arama (dataSize → ± gün ve limit)",
                c -> {
                    var r = ThreadLocalRandom.current();
                    LocalDate d = LocalDate.now(ZoneOffset.UTC).plusDays(r.nextInt(0, 20));
                    int flex = Math.min(30, c.params().dataSize());
                    return HttpCall.get("/api/flights?origin=" + AIRPORTS[r.nextInt(AIRPORTS.length)]
                            + "&date=" + d + "&flexDays=" + flex + "&limit=" + Math.min(200, 20 * c.params().dataSize()));
                }, null));
        add(new OpDef("reservation", "get_booking", Kind.READ, "PNR ile rezervasyon okuma",
                c -> pnr(c.pools().pnrs.peek(), "/api/bookings/%s"), null));
        add(new OpDef("reservation", "create_booking", Kind.WRITE,
                "Rezervasyon oluşturma (hotRow → sıcak uçuş envanter satırı)",
                c -> {
                    Long fid = c.params().hotRow() ? c.pools().hotFlights.peek() : c.pools().resFlights.peek();
                    if (fid == null) {
                        return null;
                    }
                    int n = ThreadLocalRandom.current().nextInt(1_000_000);
                    return HttpCall.post("/api/bookings", "{\"flightId\":" + fid + ",\"firstName\":\"Chaos\",\"lastName\":\"P"
                                    + n + "\",\"email\":\"c" + n + "@example.test\",\"fareClass\":\"Y\"}")
                            .withHeader("Idempotency-Key", "chaos-" + UUID.randomUUID());
                },
                (c, call, status, body) -> {
                    if (status == 201) {
                        String pnr = field(c, body, "/booking/pnr");
                        c.pools().cancellable.offer(pnr);
                        c.pools().pnrs.offer(pnr);
                        c.pools().checkinCandidates.offer(pnr);
                    }
                }));
        add(new OpDef("reservation", "cancel_booking", Kind.WRITE, "Bu üreticinin oluşturduğu rezervasyonu iptal",
                c -> pnr(c.pools().cancellable.take(), "/api/bookings/%s/cancel", true), null));
        add(new OpDef("reservation", "route_demand_report", Kind.READ,
                "MALİYETLİ: booking tam tarama + agregasyon (dataSize → gün penceresi)",
                c -> HttpCall.get("/api/reports/route-demand?days=" + Math.min(365, 30 * c.params().dataSize()) + "&limit=50"),
                null));
        add(new OpDef("reservation", "lab_hold_booking", Kind.LAB,
                "LAB: gerçek rezervasyon + envanter satır kilidini holdMs tutar",
                c -> {
                    Long fid = c.pools().hotFlights.peek();
                    return HttpCall.labPost("/lab/bookings/hold",
                            "{\"flightId\":" + (fid == null ? "null" : fid) + ",\"holdMs\":" + c.params().holdMs() + "}");
                }, null));
        addCommonLab("reservation");

        // ---------------- Check-in ----------------
        add(new OpDef("checkin", "lookup_booking", Kind.READ, "PNR ile check-in durumu (kilitsiz okuma)",
                c -> pnr(c.pools().pnrs.peek(), "/api/checkin/%s"), null));
        add(new OpDef("checkin", "checkin", Kind.WRITE,
                "Check-in: envanter checked_in+1 (hotRow → sıcak uçuş, kilit deneyinin etkilenen operasyonu)",
                c -> {
                    String p = c.params().hotRow() ? c.pools().hotCheckinCandidates.take() : c.pools().checkinCandidates.take();
                    return p == null ? null : HttpCall.post("/api/checkin", "{\"pnr\":\"" + p + "\",\"channel\":\"API\"}");
                },
                (c, call, status, body) -> {
                    if (status == 201 || status == 200) {
                        String pnr = field(c, body, "/checkin/pnr");
                        c.pools().checkedIn.offer(pnr);
                        c.pools().bagCandidates.offer(pnr);
                    }
                }));
        add(new OpDef("checkin", "assign_seat", Kind.WRITE, "Koltuk atama (yalnız checkin şeması; envantere dokunmaz)",
                c -> {
                    String p = c.pools().checkedIn.peek();
                    if (p == null) {
                        return null;
                    }
                    var r = ThreadLocalRandom.current();
                    return HttpCall.post("/api/checkin/" + p + "/seat",
                            "{\"seatNo\":\"" + r.nextInt(1, 30) + "ABCDEF".charAt(r.nextInt(6)) + "\"}");
                }, null));
        add(new OpDef("checkin", "seat_map", Kind.READ, "Uçuş koltuk haritası",
                c -> id(c.pools().resFlights.peek(), "/api/flights/%d/seatmap"), null));
        add(new OpDef("checkin", "lab_hold_checkin", Kind.LAB,
                "LAB: gerçek check-in + envanter satır kilidini holdMs tutar (ters yön)",
                c -> {
                    String p = c.pools().hotCheckinCandidates.take();
                    return HttpCall.labPost("/lab/checkin/hold",
                            "{\"pnr\":" + (p == null ? "null" : "\"" + p + "\"") + ",\"holdMs\":" + c.params().holdMs() + "}");
                }, null));
        addCommonLab("checkin");

        // ---------------- Flight Operations ----------------
        add(new OpDef("flightops", "list_flights", Kind.READ, "Uçuş listesi (dataSize → saat penceresi)",
                c -> HttpCall.get("/api/flights?hoursAhead=" + Math.min(1440, 24 * c.params().dataSize()) + "&limit=50"), null));
        add(new OpDef("flightops", "get_flight", Kind.READ, "Uçuş ayrıntısı + operasyon olayları",
                c -> id(c.pools().opsFlights.peek(), "/api/flights/%d"), null));
        add(new OpDef("flightops", "update_status", Kind.WRITE, "Uçuş gecikme/kapı güncelleme (flight satırı)",
                c -> {
                    Long id = c.pools().opsFlights.peek();
                    var r = ThreadLocalRandom.current();
                    return id == null ? null : HttpCall.post("/api/flights/" + id + "/status",
                            "{\"status\":\"DELAYED\",\"gate\":\"" + "ABCDEF".charAt(r.nextInt(6)) + r.nextInt(1, 40)
                                    + "\",\"delayMin\":" + r.nextInt(5, 120) + "}");
                }, null));
        add(new OpDef("flightops", "update_capacity", Kind.WRITE,
                "Kapasite güncelleme: ORTAK envanter satırı (hotRow → sıcak uçuş, kapasite korunur)",
                c -> {
                    if (c.params().hotRow()) {
                        Long id = c.pools().hotFlights.peek();
                        return id == null ? null : HttpCall.post("/api/flights/" + id + "/capacity", "{\"capacity\":1000000}");
                    }
                    Long id = c.pools().opsFlights.peek();
                    return id == null ? null : HttpCall.post("/api/flights/" + id + "/capacity",
                            "{\"capacity\":" + ThreadLocalRandom.current().nextInt(300, 400) + "}");
                }, null));
        add(new OpDef("flightops", "load_factor_report", Kind.READ,
                "MALİYETLİ: uçuş × booking join/agregasyon (dataSize → gün penceresi)",
                c -> HttpCall.get("/api/reports/load-factor?days=" + Math.min(365, 7 * c.params().dataSize()) + "&limit=200"),
                null));
        add(new OpDef("flightops", "lab_hold_capacity", Kind.LAB,
                "LAB: gerçek kapasite güncellemesi + envanter satır kilidini holdMs tutar",
                c -> {
                    Long fid = c.pools().hotFlights.peek();
                    return HttpCall.labPost("/lab/flights/capacity-hold",
                            "{\"flightId\":" + (fid == null ? "null" : fid) + ",\"holdMs\":" + c.params().holdMs() + "}");
                }, null));
        addCommonLab("flightops");

        // ---------------- Baggage ----------------
        add(new OpDef("baggage", "bag_status", Kind.READ, "Etiket ile bagaj durumu (bağımsız okuma)",
                c -> pnr(c.pools().bagTags.peek(), "/api/bags/%s"), null));
        add(new OpDef("baggage", "flight_bags", Kind.READ, "Uçuşa ait bagajlar (indeksli)",
                c -> id(c.pools().resFlights.peek(), "/api/flights/%d/bags?limit=100"), null));
        add(new OpDef("baggage", "register_bag", Kind.WRITE, "Bagaj kaydı (yalnız baggage şemasına yazar)",
                c -> {
                    String p = c.pools().bagCandidates.peek();
                    return p == null ? null : HttpCall.post("/api/bags",
                            "{\"pnr\":\"" + p + "\",\"weightKg\":" + ThreadLocalRandom.current().nextInt(5, 32) + "}");
                },
                (c, call, status, body) -> {
                    if (status == 201) {
                        String tag = field(c, body, "/detail/bag/tag");
                        c.pools().bagTags.offer(tag);
                        c.pools().bagProgress.offer(new String[]{tag, "0"});
                    } else if (status == 409 && body != null && body.contains("BAG_LIMIT")) {
                        String pnr = call.jsonBody().replaceAll(".*\"pnr\":\"([A-Z0-9]+)\".*", "$1");
                        c.pools().bagCandidates.take();   // havuzu yavaşça tazele
                        c.pools().pnrs.offer(pnr);
                    }
                }));
        add(new OpDef("baggage", "bag_event", Kind.WRITE, "Bagaj olay akışı (LOADED→...→DELIVERED)",
                c -> {
                    String[] p = c.pools().bagProgress.take();
                    if (p == null) {
                        return null;
                    }
                    int step = Integer.parseInt(p[1]);
                    return HttpCall.post("/api/bags/" + p[0] + "/events",
                            "{\"eventType\":\"" + BAG_FLOW[step] + "\",\"location\":\"IST\"}").withHeader("X-Bag-Step", p[0] + ":" + step);
                },
                (c, call, status, body) -> {
                    String[] s = call.headers().get("X-Bag-Step").split(":");
                    int next = Integer.parseInt(s[1]) + 1;
                    if (status == 200 && next < BAG_FLOW.length) {
                        c.pools().bagProgress.offer(new String[]{s[0], String.valueOf(next)});
                    }
                }));
        add(new OpDef("baggage", "bag_history_report", Kind.READ,
                "I/O AĞIRLIKLI: bag_event tam tarama (dataSize → gün penceresi)",
                c -> HttpCall.get("/api/reports/bag-history?days=" + Math.min(365, 30 * c.params().dataSize())), null));
        addCommonLab("baggage");

        normalMix.put("reservation", ordered("search_flights", 50, "get_booking", 25, "create_booking", 15, "cancel_booking", 10));
        normalMix.put("checkin", ordered("lookup_booking", 40, "checkin", 30, "assign_seat", 20, "seat_map", 10));
        normalMix.put("flightops", ordered("list_flights", 40, "get_flight", 40, "update_status", 15, "update_capacity", 5));
        normalMix.put("baggage", ordered("bag_status", 45, "flight_bags", 15, "register_bag", 25, "bag_event", 15));
    }

    private void addCommonLab(String app) {
        add(new OpDef(app, "lab_hold_connection", Kind.LAB,
                "LAB: uygulamanın KENDİ Hikari havuzundan bağlantıyı holdMs tutar (tek-pool doygunluğu)",
                c -> HttpCall.labPost("/lab/hold-connection?pool=main&holdMs=" + c.params().holdMs(), null), null));
        add(new OpDef(app, "lab_pressure_connection", Kind.LAB,
                "LAB: ayrı lab-pressure havuzundan EK DB bağlantısı açıp tutar (global connection baskısı)",
                c -> HttpCall.labPost("/lab/hold-connection?pool=pressure&holdMs=" + c.params().holdMs(), null), null));
    }

    private void add(OpDef op) {
        ops.computeIfAbsent(op.app(), k -> new LinkedHashMap<>()).put(op.name(), op);
    }

    private static Map<String, Integer> ordered(Object... kv) {
        Map<String, Integer> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], (Integer) kv[i + 1]);
        }
        return m;
    }

    private static HttpCall pnr(String v, String fmt) {
        return pnr(v, fmt, false);
    }

    private static HttpCall pnr(String v, String fmt, boolean post) {
        if (v == null) {
            return null;
        }
        return post ? HttpCall.post(String.format(fmt, v), null) : HttpCall.get(String.format(fmt, v));
    }

    private static HttpCall id(Long v, String fmt) {
        return v == null ? null : HttpCall.get(String.format(fmt, v));
    }

    private static String field(OpContext c, String body, String pointer) {
        try {
            JsonNode n = c.json().readTree(body).at(pointer);
            return n.isMissingNode() ? null : n.asString();
        } catch (Exception e) {
            return null;
        }
    }

    public OpDef op(String app, String name) {
        var m = ops.get(app);
        return m == null ? null : m.get(name);
    }

    public Map<String, Map<String, OpDef>> all() {
        return ops;
    }

    public Map<String, Integer> normalMix(String app) {
        return normalMix.get(app);
    }

    /**
     * readWriteRatio verilirse normal karışımın okuma ağırlıklarını toplam payı
     * ratio olacak, yazma ağırlıklarını (1-ratio) olacak şekilde ölçekler.
     */
    public Map<String, Integer> mixFor(String app, Map<String, Integer> explicit, Double readWriteRatio) {
        if (explicit != null && !explicit.isEmpty()) {
            return explicit;
        }
        Map<String, Integer> base = normalMix(app);
        if (readWriteRatio == null) {
            return base;
        }
        int readSum = 0;
        int writeSum = 0;
        for (var e : base.entrySet()) {
            if (op(app, e.getKey()).kind() == Kind.READ) {
                readSum += e.getValue();
            } else {
                writeSum += e.getValue();
            }
        }
        Map<String, Integer> out = new LinkedHashMap<>();
        for (var e : base.entrySet()) {
            boolean read = op(app, e.getKey()).kind() == Kind.READ;
            double share = read ? readWriteRatio * e.getValue() / readSum : (1 - readWriteRatio) * e.getValue() / writeSum;
            int w = (int) Math.round(share * 1000);
            if (w > 0) {
                out.put(e.getKey(), w);
            }
        }
        return out;
    }
}
