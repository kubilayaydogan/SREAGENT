package com.lab.air.flightops;

import com.lab.air.common.BusinessException;
import com.lab.air.common.lab.LabHolds;
import com.lab.air.flightops.Model.Result;
import com.lab.air.flightops.Model.StatusUpdate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.Map;
import java.util.Objects;
import java.util.Set;

import static com.lab.air.common.SqlTag.tag;

/**
 * Operasyon güncellemeleri.
 * <ul>
 *   <li>status: yalnız flight satırı (FOR NO KEY UPDATE). Rezervasyon/check-in INSERT'lerinin FK kontrolü
 *       aynı satırda FOR KEY SHARE alır; PostgreSQL'de bu iki kip çakışmaz → durum güncellemesi satışı
 *       bloklamaz (bilinçli seçim; FOR UPDATE kullanılsaydı FK kontrolleri beklerdi).</li>
 *   <li>capacity: <b>flight_inventory ORTAK SATIRI</b> (Reservation sold / Check-in checked_in ile aynı satır).</li>
 * </ul>
 */
@Service
public class FlightOpsService {

    static final Map<String, Set<String>> TRANSITIONS = Map.of(
            "SCHEDULED", Set.of("DELAYED", "BOARDING", "CANCELLED"),
            "DELAYED", Set.of("DELAYED", "BOARDING", "CANCELLED"),
            "BOARDING", Set.of("DELAYED", "DEPARTED"),
            "DEPARTED", Set.of("ARRIVED"),
            "ARRIVED", Set.of(),
            "CANCELLED", Set.of());

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final FlightOpsRepository repo;
    private final LabHolds holds;

    public FlightOpsService(JdbcClient jdbc, PlatformTransactionManager tm, FlightOpsRepository repo, LabHolds holds,
                            @Value("${app.tx-timeout-seconds:10}") int txTimeout) {
        this.jdbc = jdbc;
        this.repo = repo;
        this.holds = holds;
        this.tx = new TransactionTemplate(tm);
        this.tx.setTimeout(txTimeout);
    }

    public Result updateStatus(long flightId, StatusUpdate req) {
        boolean replay = Boolean.TRUE.equals(tx.execute(s -> {
            var cur = jdbc.sql(tag("""
                            SELECT status, gate, delay_min FROM flightops.flight WHERE flight_id = ? FOR NO KEY UPDATE"""))
                    .param(flightId)
                    .query((rs, i) -> new Object[]{rs.getString(1), rs.getString(2), rs.getInt(3)})
                    .optional()
                    .orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok: " + flightId));
            String from = (String) cur[0];
            String gate = req.gate() != null ? req.gate() : (String) cur[1];
            int delay = req.delayMin() != null ? req.delayMin() : (Integer) cur[2];
            if (from.equals(req.status()) && Objects.equals(gate, cur[1]) && delay == (Integer) cur[2]) {
                return true;   // aynı istek tekrarı
            }
            if (!from.equals(req.status()) && !TRANSITIONS.get(from).contains(req.status())) {
                throw BusinessException.conflict("INVALID_TRANSITION", from + " → " + req.status() + " geçersiz");
            }
            // ---- V5 zinciri: kapı/kalkış kararı yüklü bagaj sayısına bağlı → bagaj sayacı tutarlı okunur (FOR SHARE).
            //      Bir bagaj kaydı transaction'ı sayacı tutuyorsa burada beklenir (uçuş satırı kilitliyken).
            int bags = jdbc.sql(tag("SELECT bags FROM baggage.flight_baggage_stats WHERE flight_id = ? FOR SHARE"))
                    .param(flightId).query(Integer.class).optional().orElse(0);
            jdbc.sql(tag("""
                            UPDATE flightops.flight SET status = ?, gate = ?, delay_min = ?, updated_at = now()
                             WHERE flight_id = ?"""))
                    .params(req.status(), gate, delay, flightId).update();
            jdbc.sql(tag("INSERT INTO flightops.ops_event(flight_id, event_type, detail) VALUES (?, 'STATUS', ?)"))
                    .params(flightId, from + "->" + req.status() + " gate=" + gate + " delay=" + delay + " bags=" + bags)
                    .update();
            return false;
        }));
        return new Result(repo.get(flightId).orElseThrow(), replay);
    }

    /**
     * Kapasite değişikliği: ORTAK envanter satırını günceller. sold'un altına inemez
     * (koşullu UPDATE + CHECK kısıtı). labHoldMs yalnız lab uçlarından gelir.
     */
    public Result updateCapacity(long flightId, int capacity, long labHoldMs) {
        boolean replay = Boolean.TRUE.equals(tx.execute(s -> {
            var row = jdbc.sql(tag("""
                            UPDATE flightops.flight_inventory
                               SET capacity = ?, version = version + 1, updated_at = now()
                             WHERE flight_id = ? AND sold <= ?
                         RETURNING flight_id"""))
                    .params(capacity, flightId, capacity)
                    .query((rs, i) -> Boolean.TRUE).optional();
            if (row.isEmpty()) {
                Integer sold = jdbc.sql(tag("SELECT sold FROM flightops.flight_inventory WHERE flight_id = ?"))
                        .param(flightId).query(Integer.class).optional()
                        .orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok: " + flightId));
                throw BusinessException.conflict("CAPACITY_BELOW_SOLD", "kapasite satılan koltuktan (" + sold + ") az olamaz");
            }
            jdbc.sql(tag("INSERT INTO flightops.ops_event(flight_id, event_type, detail) VALUES (?, 'CAPACITY', ?)"))
                    .params(flightId, "capacity=" + capacity).update();
            holds.holdInTransaction(jdbc, "hold-inventory-lock", labHoldMs);
            return false;
        }));
        return new Result(repo.get(flightId).orElseThrow(), replay);
    }
}
