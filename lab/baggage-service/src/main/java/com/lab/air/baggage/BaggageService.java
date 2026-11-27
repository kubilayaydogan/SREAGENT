package com.lab.air.baggage;

import com.lab.air.baggage.Model.Bag;
import com.lab.air.baggage.Model.BagDetail;
import com.lab.air.baggage.Model.Result;
import com.lab.air.common.BusinessException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.security.SecureRandom;
import java.util.Map;
import java.util.Set;

import static com.lab.air.common.SqlTag.tag;

/**
 * Bagaj işlemleri.
 * <ul>
 *   <li>register: booking + checkin kilitsiz okunur (FK yok). Ardından V5 zinciri: uçuş bagaj sayacı
 *       (baggage.flight_baggage_stats) güncellenip kilitlenir, check-in sayacı (checkin.flight_checkin_stats)
 *       FOR SHARE okunur → bir check-in transaction'ı sayacı tutuyorsa bagaj kaydı bekler; bekleyen bagaj kaydı
 *       da kendi sayacını tuttuğu için Flight Ops durum güncellemesini bekletir. Rezervasyon başına en fazla 3
 *       bagaj kuralı pg_advisory_xact_lock ile serileştirilir.</li>
 *   <li>event: yalnız ilgili bag satırı FOR UPDATE.</li>
 * </ul>
 */
@Service
public class BaggageService {

    static final int MAX_BAGS = 3;
    static final Map<String, Set<String>> TRANSITIONS = Map.of(
            "CHECKED", Set.of("LOADED", "MISHANDLED"),
            "LOADED", Set.of("IN_TRANSIT", "MISHANDLED"),
            "IN_TRANSIT", Set.of("ARRIVED", "MISHANDLED"),
            "ARRIVED", Set.of("DELIVERED", "MISHANDLED"),
            "MISHANDLED", Set.of("ARRIVED", "DELIVERED"),
            "DELIVERED", Set.of());
    private static final char[] TAG_CHARS = "ABCDEFGHJKLMNPQRTUVWXYZ0123456789".toCharArray();

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final BaggageRepository repo;
    private final SecureRandom random = new SecureRandom();

    public BaggageService(JdbcClient jdbc, PlatformTransactionManager tm, BaggageRepository repo,
                          @Value("${app.tx-timeout-seconds:10}") int txTimeout) {
        this.jdbc = jdbc;
        this.repo = repo;
        this.tx = new TransactionTemplate(tm);
        this.tx.setTimeout(txTimeout);
    }

    public BagDetail get(String tag) {
        Bag b = repo.byTag(tag).orElseThrow(() -> BusinessException.notFound("BAG_NOT_FOUND", "etiket yok: " + tag));
        return new BagDetail(b, repo.events(b.bagId()));
    }

    public Result register(String pnr, BigDecimal weightKg, String idempotencyKey) {
        if (idempotencyKey != null) {
            var existing = repo.byIdempotencyKey(idempotencyKey);
            if (existing.isPresent()) {
                return new Result(new BagDetail(existing.get(), repo.events(existing.get().bagId())), true);
            }
        }
        for (int attempt = 1; ; attempt++) {
            try {
                String tag = tx.execute(s -> doRegister(pnr, weightKg, idempotencyKey));
                return new Result(get(tag), false);
            } catch (DuplicateKeyException e) {
                if (idempotencyKey != null) {
                    var existing = repo.byIdempotencyKey(idempotencyKey);
                    if (existing.isPresent()) {
                        return new Result(new BagDetail(existing.get(), repo.events(existing.get().bagId())), true);
                    }
                }
                if (attempt >= 3) {
                    throw e;
                }
            }
        }
    }

    private String doRegister(String pnr, BigDecimal weightKg, String idempotencyKey) {
        var b = jdbc.sql(tag("""
                        SELECT b.booking_id, b.flight_id, b.status,
                               EXISTS (SELECT 1 FROM checkin.checkin c WHERE c.booking_id = b.booking_id)
                          FROM reservation.booking b WHERE b.pnr = ?"""))
                .param(pnr.toUpperCase())
                .query((rs, i) -> new Object[]{rs.getLong(1), rs.getLong(2), rs.getString(3), rs.getBoolean(4)})
                .optional()
                .orElseThrow(() -> BusinessException.notFound("BOOKING_NOT_FOUND", "PNR yok: " + pnr));
        long bookingId = (Long) b[0];
        if ("CANCELLED".equals(b[2])) {
            throw BusinessException.conflict("BOOKING_CANCELLED", "iptal edilmiş rezervasyon");
        }
        if (!(Boolean) b[3]) {
            throw BusinessException.conflict("NOT_CHECKED_IN", "bagaj için önce check-in gerekli");
        }
        long flightId = (Long) b[1];
        // ---- Uçuş bagaj sayacı (V5). Satır transaction sonuna kadar kilitli: Flight Ops durum güncellemesi bu
        //      sayacı FOR SHARE okur → Baggage beklerse Flight Ops da zincirleme bekler.
        long bags = jdbc.sql(tag("""
                        INSERT INTO baggage.flight_baggage_stats AS s(flight_id, bags, total_weight_kg, last_bag_at)
                        VALUES (?, 1, ?, now())
                        ON CONFLICT (flight_id) DO UPDATE
                           SET bags = s.bags + 1, total_weight_kg = s.total_weight_kg + EXCLUDED.total_weight_kg,
                               last_bag_at = now(), updated_at = now()
                        RETURNING bags"""))
                .params(flightId, weightKg).query(Long.class).single();
        // ---- Check-in sayacını tutarlı oku (FOR SHARE): bir check-in transaction'ı sayacı tutuyorsa burada beklenir.
        //      Kural: uçuştaki bagaj sayısı check-in yapan yolcu × limit'i aşamaz.
        int checkedIn = jdbc.sql(tag("""
                        SELECT checked_in FROM checkin.flight_checkin_stats WHERE flight_id = ? FOR SHARE"""))
                .param(flightId).query(Integer.class).optional().orElse(0);
        if (bags > (long) checkedIn * MAX_BAGS) {
            throw BusinessException.conflict("FLIGHT_BAG_LIMIT",
                    "uçuştaki bagaj sayısı check-in yapan yolcu sayısıyla tutarsız (" + bags + " > " + checkedIn + "×" + MAX_BAGS + ")");
        }
        // Rezervasyona özgü, yalnız baggage içinde kullanılan kısa kilit (bagaj limiti yarışını önler)
        jdbc.sql(tag("SELECT pg_advisory_xact_lock(hashtext('baggage'), ?::int)"))
                .param((int) (bookingId % Integer.MAX_VALUE)).query().listOfRows();
        long count = jdbc.sql(tag("SELECT count(*) FROM baggage.bag WHERE booking_id = ?"))
                .param(bookingId).query(Long.class).single();
        if (count >= MAX_BAGS) {
            throw BusinessException.conflict("BAG_LIMIT", "rezervasyon başına en fazla " + MAX_BAGS + " bagaj");
        }
        String tag = newTag();
        long bagId = jdbc.sql(tag("""
                        INSERT INTO baggage.bag(tag, booking_id, flight_id, weight_kg, idempotency_key)
                        VALUES (?, ?, ?, ?, ?) RETURNING bag_id"""))
                .params(tag, bookingId, b[1], weightKg, idempotencyKey)
                .query(Long.class).single();
        jdbc.sql(tag("""
                        INSERT INTO baggage.bag_event(bag_id, flight_id, event_type, location, note)
                        VALUES (?, ?, 'CHECKED', 'COUNTER', ?)"""))
                .params(bagId, b[1], "weight=" + weightKg).update();
        return tag;
    }

    public Result addEvent(String tag, String eventType, String location) {
        boolean replay = Boolean.TRUE.equals(tx.execute(s -> {
            var bag = jdbc.sql(tag("SELECT bag_id, flight_id, status FROM baggage.bag WHERE tag = ? FOR UPDATE"))
                    .param(tag.toUpperCase())
                    .query((rs, i) -> new Object[]{rs.getLong(1), rs.getLong(2), rs.getString(3)})
                    .optional()
                    .orElseThrow(() -> BusinessException.notFound("BAG_NOT_FOUND", "etiket yok: " + tag));
            String from = (String) bag[2];
            if (from.equals(eventType)) {
                return true;
            }
            if (!TRANSITIONS.get(from).contains(eventType)) {
                throw BusinessException.conflict("INVALID_TRANSITION", from + " → " + eventType + " geçersiz");
            }
            jdbc.sql(tag("UPDATE baggage.bag SET status = ?, updated_at = now() WHERE bag_id = ?"))
                    .params(eventType, bag[0]).update();
            jdbc.sql(tag("""
                            INSERT INTO baggage.bag_event(bag_id, flight_id, event_type, location)
                            VALUES (?, ?, ?, ?)"""))
                    .params(bag[0], bag[1], eventType, location).update();
            return false;
        }));
        return new Result(get(tag), replay);
    }

    private String newTag() {
        char[] c = new char[10];
        c[0] = 'B';
        for (int i = 1; i < c.length; i++) {
            c[i] = TAG_CHARS[random.nextInt(TAG_CHARS.length)];
        }
        return new String(c);
    }
}
