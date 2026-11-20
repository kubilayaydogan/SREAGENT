package com.lab.air.reservation;

import com.lab.air.common.BusinessException;
import com.lab.air.common.lab.LabHolds;
import com.lab.air.reservation.Model.Booking;
import com.lab.air.reservation.Model.BookingResult;
import com.lab.air.reservation.Model.CreateBooking;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.util.Map;

import static com.lab.air.common.SqlTag.tag;

/**
 * Rezervasyon yazma transaction'ları.
 *
 * <h3>create (ortak satır kilidi)</h3>
 * <ol>
 *   <li>flight durum kontrolü (kilitsiz SELECT)</li>
 *   <li>INSERT passenger</li>
 *   <li><b>UPDATE flightops.flight_inventory SET sold = sold + 1 WHERE flight_id = ? AND sold &lt; capacity</b>
 *       → envanter satırında tuple lock; COMMIT'e kadar tutulur. Check-in (checked_in+1) ve
 *       FlightOps (capacity) aynı satırı güncellerken bu kilidi bekler.</li>
 *   <li>INSERT booking (UNIQUE pnr, UNIQUE idempotency_key)</li>
 *   <li>[yalnız lab] sınırlı pg_sleep: kilidi bilinçli olarak uzatır</li>
 * </ol>
 * Aşırı satış: koşullu UPDATE + CHECK(sold &lt;= capacity) iki katmanlı korur.
 * Tekrar gönderim: Idempotency-Key UNIQUE; çakışmada transaction geri alınır, mevcut kayıt döner.
 */
@Service
public class BookingService {

    private static final Logger log = LoggerFactory.getLogger(BookingService.class);
    private static final char[] PNR_CHARS = "ABCDEFGHJKLMNPQRTUVWXYZ23456789".toCharArray(); // 'S' seed'e ayrıldı
    private static final Map<String, BigDecimal> BASE_FARE = Map.of(
            "Y", new BigDecimal("120.00"), "B", new BigDecimal("380.00"), "J", new BigDecimal("900.00"));

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final ReservationRepository repo;
    private final LabHolds holds;
    private final SecureRandom random = new SecureRandom();

    public BookingService(JdbcClient jdbc, PlatformTransactionManager tm, ReservationRepository repo, LabHolds holds,
                          @Value("${app.tx-timeout-seconds:10}") int txTimeout) {
        this.jdbc = jdbc;
        this.repo = repo;
        this.holds = holds;
        this.tx = new TransactionTemplate(tm);
        this.tx.setTimeout(txTimeout);
    }

    public BookingResult create(CreateBooking req, String idempotencyKey, long labHoldMs) {
        if (idempotencyKey != null) {
            var existing = repo.byIdempotencyKey(idempotencyKey);
            if (existing.isPresent()) {
                return new BookingResult(existing.get(), true);
            }
        }
        for (int attempt = 1; ; attempt++) {
            try {
                String pnr = tx.execute(s -> doCreate(req, idempotencyKey, labHoldMs));
                return new BookingResult(repo.byPnr(pnr).orElseThrow(), false);
            } catch (DuplicateKeyException e) {
                if (idempotencyKey != null) {
                    var existing = repo.byIdempotencyKey(idempotencyKey);
                    if (existing.isPresent()) {
                        return new BookingResult(existing.get(), true);   // eşzamanlı aynı anahtar
                    }
                }
                if (attempt >= 3 || !String.valueOf(e.getMessage()).contains("pnr")) {
                    throw e;
                }
                log.info("PNR çakışması, yeniden deneniyor (deneme {})", attempt);
            }
        }
    }

    private String doCreate(CreateBooking req, String idempotencyKey, long labHoldMs) {
        var flight = jdbc.sql(tag("SELECT status, dep_time FROM flightops.flight WHERE flight_id = ?"))
                .param(req.flightId())
                .query((rs, i) -> new Object[]{rs.getString(1), rs.getObject(2, OffsetDateTime.class)})
                .optional()
                .orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok: " + req.flightId()));
        String status = (String) flight[0];
        OffsetDateTime dep = (OffsetDateTime) flight[1];
        if (!("SCHEDULED".equals(status) || "DELAYED".equals(status)) || dep.isBefore(OffsetDateTime.now())) {
            throw BusinessException.conflict("FLIGHT_CLOSED", "uçuş satışa kapalı: " + status);
        }

        long paxId = jdbc.sql(tag("""
                        INSERT INTO reservation.passenger(first_name, last_name, email)
                        VALUES (?, ?, ?) RETURNING passenger_id"""))
                .params(req.firstName().trim(), req.lastName().trim(), req.email().trim().toLowerCase())
                .query(Long.class).single();

        // ---- ORTAK SATIR KİLİDİ burada alınır ----
        var inv = jdbc.sql(tag("""
                        UPDATE flightops.flight_inventory
                           SET sold = sold + 1, version = version + 1, updated_at = now()
                         WHERE flight_id = ? AND sold < capacity
                     RETURNING sold, capacity"""))
                .param(req.flightId())
                .query((rs, i) -> new int[]{rs.getInt(1), rs.getInt(2)})
                .optional()
                .orElseThrow(() -> BusinessException.conflict("SOLD_OUT", "uçuşta boş koltuk yok"));

        String pnr = newPnr();
        jdbc.sql(tag("""
                        INSERT INTO reservation.booking(pnr, flight_id, passenger_id, status, fare_class, price, idempotency_key)
                        VALUES (?, ?, ?, 'CONFIRMED', ?, ?, ?)"""))
                .params(pnr, req.flightId(), paxId, req.fare(), price(req.fare(), inv[0], inv[1]), idempotencyKey)
                .update();

        holds.holdInTransaction(jdbc, "hold-inventory-lock", labHoldMs);
        return pnr;
    }

    /**
     * İptal: booking FOR UPDATE (check-in'in FOR SHARE'i ile serileşir), check-in yapılmışsa reddedilir,
     * envanter sold-1 (ortak satır kilidi). Zaten iptal ise idempotent döner.
     */
    public BookingResult cancel(String pnr, long labHoldMs) {
        boolean replay = Boolean.TRUE.equals(tx.execute(s -> {
            var row = jdbc.sql(tag("""
                            SELECT booking_id, flight_id, status FROM reservation.booking
                             WHERE pnr = ? FOR UPDATE"""))
                    .param(pnr.toUpperCase())
                    .query((rs, i) -> new Object[]{rs.getLong(1), rs.getLong(2), rs.getString(3)})
                    .optional()
                    .orElseThrow(() -> BusinessException.notFound("BOOKING_NOT_FOUND", "PNR yok: " + pnr));
            long bookingId = (Long) row[0];
            long flightId = (Long) row[1];
            if ("CANCELLED".equals(row[2])) {
                return true;
            }
            boolean checkedIn = jdbc.sql(tag("SELECT EXISTS (SELECT 1 FROM checkin.checkin WHERE booking_id = ?)"))
                    .param(bookingId).query(Boolean.class).single();
            if (checkedIn) {
                throw BusinessException.conflict("ALREADY_CHECKED_IN", "check-in yapılmış rezervasyon iptal edilemez");
            }
            jdbc.sql(tag("""
                            UPDATE reservation.booking SET status = 'CANCELLED', cancelled_at = now(), updated_at = now()
                             WHERE booking_id = ?"""))
                    .param(bookingId).update();
            jdbc.sql(tag("""
                            UPDATE flightops.flight_inventory
                               SET sold = sold - 1, version = version + 1, updated_at = now()
                             WHERE flight_id = ?"""))
                    .param(flightId).update();
            holds.holdInTransaction(jdbc, "hold-inventory-lock", labHoldMs);
            return false;
        }));
        return new BookingResult(repo.byPnr(pnr).orElseThrow(), replay);
    }

    /** Doluluk arttıkça fiyat artar (basit, deterministik). */
    static BigDecimal price(String fare, int sold, int capacity) {
        double lf = capacity == 0 ? 1 : (double) sold / capacity;
        return BASE_FARE.get(fare).multiply(BigDecimal.valueOf(1 + Math.min(lf, 1.0)))
                .setScale(2, RoundingMode.HALF_UP);
    }

    private String newPnr() {
        char[] c = new char[6];
        for (int i = 0; i < c.length; i++) {
            c[i] = PNR_CHARS[random.nextInt(PNR_CHARS.length)];
        }
        return new String(c);
    }

    public Booking get(String pnr) {
        return repo.byPnr(pnr).orElseThrow(() -> BusinessException.notFound("BOOKING_NOT_FOUND", "PNR yok: " + pnr));
    }
}
