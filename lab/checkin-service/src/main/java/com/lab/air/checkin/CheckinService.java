package com.lab.air.checkin;

import com.lab.air.checkin.Model.CheckinView;
import com.lab.air.checkin.Model.Result;
import com.lab.air.common.BusinessException;
import com.lab.air.common.lab.LabHolds;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.Set;

import static com.lab.air.common.SqlTag.tag;

/**
 * Check-in yazma transaction'ları.
 *
 * <h3>check-in</h3>
 * <ol>
 *   <li>SELECT booking ... FOR SHARE OF b — eşzamanlı iptal (FOR UPDATE) ile serileşir</li>
 *   <li>INSERT checkin ON CONFLICT (booking_id) DO NOTHING — tekrar gönderim güvenli</li>
 *   <li>UPSERT checkin.flight_checkin_stats (uçuş sayacı) — satır kilidi transaction sonuna kadar tutulur (V5 zinciri)</li>
 *   <li><b>UPDATE flightops.flight_inventory SET checked_in = checked_in + 1</b> — ORTAK SATIR;
 *       Reservation create/cancel veya FlightOps kapasite güncellemesi aynı satırı tutuyorsa burada bekler
 *       (wait_event_type=Lock, wait_event=transactionid).</li>
 * </ol>
 * <h3>koltuk atama</h3>
 * Yalnız checkin şemasında; envanter satırına dokunmaz → kilit deneyinde etkilenmemesi beklenir.
 */
@Service
public class CheckinService {

    private static final Set<String> CLOSED_FLIGHT = Set.of("DEPARTED", "ARRIVED", "CANCELLED");

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final CheckinRepository repo;
    private final LabHolds holds;

    public CheckinService(JdbcClient jdbc, PlatformTransactionManager tm, CheckinRepository repo, LabHolds holds,
                          @Value("${app.tx-timeout-seconds:10}") int txTimeout) {
        this.jdbc = jdbc;
        this.repo = repo;
        this.holds = holds;
        this.tx = new TransactionTemplate(tm);
        this.tx.setTimeout(txTimeout);
    }

    public CheckinView get(String pnr) {
        return repo.view(pnr).orElseThrow(() -> BusinessException.notFound("BOOKING_NOT_FOUND", "PNR yok: " + pnr));
    }

    public Result checkIn(String pnr, String channel, long labHoldMs) {
        boolean replay = Boolean.TRUE.equals(tx.execute(s -> {
            var b = jdbc.sql(tag("""
                            SELECT b.booking_id, b.flight_id, b.status, f.status
                              FROM reservation.booking b JOIN flightops.flight f ON f.flight_id = b.flight_id
                             WHERE b.pnr = ?
                               FOR SHARE OF b"""))
                    .param(pnr.toUpperCase())
                    .query((rs, i) -> new Object[]{rs.getLong(1), rs.getLong(2), rs.getString(3), rs.getString(4)})
                    .optional()
                    .orElseThrow(() -> BusinessException.notFound("BOOKING_NOT_FOUND", "PNR yok: " + pnr));
            long bookingId = (Long) b[0];
            long flightId = (Long) b[1];
            if ("CANCELLED".equals(b[2])) {
                throw BusinessException.conflict("BOOKING_CANCELLED", "iptal edilmiş rezervasyon");
            }
            if (CLOSED_FLIGHT.contains((String) b[3])) {
                throw BusinessException.conflict("FLIGHT_CLOSED", "uçuş check-in'e kapalı: " + b[3]);
            }
            boolean inserted = jdbc.sql(tag("""
                            INSERT INTO checkin.checkin(booking_id, flight_id, channel) VALUES (?, ?, ?)
                            ON CONFLICT (booking_id) DO NOTHING RETURNING checkin_id"""))
                    .params(bookingId, flightId, channel)
                    .query(Long.class).optional().isPresent();
            if (!inserted) {
                return true;
            }
            // ---- Uçuş check-in sayacı (V5). Bu satır transaction sonuna kadar kilitli kalır: aşağıdaki envanter
            //      güncellemesi beklerse, sayacı FOR SHARE okuyan Baggage da zincirleme bekler.
            jdbc.sql(tag("""
                            INSERT INTO checkin.flight_checkin_stats AS s(flight_id, checked_in, last_checkin_at)
                            VALUES (?, 1, now())
                            ON CONFLICT (flight_id) DO UPDATE
                               SET checked_in = s.checked_in + 1, last_checkin_at = now(), updated_at = now()"""))
                    .param(flightId).update();
            // ---- ORTAK SATIR KİLİDİ: Reservation/FlightOps tutuyorsa burada beklenir ----
            jdbc.sql(tag("""
                            UPDATE flightops.flight_inventory
                               SET checked_in = checked_in + 1, version = version + 1, updated_at = now()
                             WHERE flight_id = ?"""))
                    .param(flightId).update();
            holds.holdInTransaction(jdbc, "hold-inventory-lock", labHoldMs);
            return false;
        }));
        return new Result(get(pnr), replay);
    }

    public Result assignSeat(String pnr, String seatNo) {
        try {
            boolean replay = Boolean.TRUE.equals(tx.execute(s -> {
                var c = jdbc.sql(tag("""
                                SELECT c.booking_id, c.flight_id, i.capacity
                                  FROM checkin.checkin c
                                  JOIN reservation.booking b ON b.booking_id = c.booking_id
                                  JOIN flightops.flight_inventory i ON i.flight_id = c.flight_id
                                 WHERE b.pnr = ?"""))
                        .param(pnr.toUpperCase())
                        .query((rs, i) -> new long[]{rs.getLong(1), rs.getLong(2), rs.getLong(3)})
                        .optional()
                        .orElseThrow(() -> BusinessException.conflict("NOT_CHECKED_IN", "önce check-in yapılmalı"));
                int row = Integer.parseInt(seatNo.substring(0, seatNo.length() - 1));
                long rows = Math.min(99, (c[2] + 5) / 6);
                if (row > rows) {
                    throw BusinessException.badRequest("SEAT_OUT_OF_RANGE", "bu uçakta en fazla " + rows + " sıra var");
                }
                var current = jdbc.sql(tag("SELECT seat_no FROM checkin.seat_assignment WHERE booking_id = ? FOR UPDATE"))
                        .param(c[0]).query(String.class).optional();
                if (current.isPresent() && current.get().equals(seatNo)) {
                    return true;
                }
                if (current.isPresent()) {
                    jdbc.sql(tag("UPDATE checkin.seat_assignment SET seat_no = ?, assigned_at = now() WHERE booking_id = ?"))
                            .params(seatNo, c[0]).update();
                } else {
                    jdbc.sql(tag("INSERT INTO checkin.seat_assignment(flight_id, seat_no, booking_id) VALUES (?, ?, ?)"))
                            .params(c[1], seatNo, c[0]).update();
                }
                return false;
            }));
            return new Result(get(pnr), replay);
        } catch (DuplicateKeyException e) {
            throw BusinessException.conflict("SEAT_TAKEN", "koltuk dolu: " + seatNo);
        }
    }
}
