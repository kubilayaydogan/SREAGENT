package com.lab.air.reservation;

import com.lab.air.reservation.Model.Booking;
import com.lab.air.reservation.Model.Flight;
import com.lab.air.reservation.Model.RouteDemand;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static com.lab.air.common.SqlTag.tag;

/** Okuma sorguları. Yazma transaction'ları {@link BookingService} içindedir. */
@Repository
public class ReservationRepository {

    static final String BOOKING_SELECT = """
            SELECT b.booking_id, b.pnr, b.flight_id, f.flight_no, f.origin, f.destination, f.dep_time,
                   b.status, b.fare_class, b.price, p.first_name, p.last_name, p.email,
                   b.created_at, b.cancelled_at,
                   EXISTS (SELECT 1 FROM checkin.checkin c WHERE c.booking_id = b.booking_id) AS checked_in
              FROM reservation.booking b
              JOIN reservation.passenger p ON p.passenger_id = b.passenger_id
              JOIN flightops.flight f ON f.flight_id = b.flight_id
            """;

    static final RowMapper<Booking> BOOKING = (rs, i) -> new Booking(
            rs.getLong("booking_id"), rs.getString("pnr"), rs.getLong("flight_id"), rs.getString("flight_no"),
            rs.getString("origin"), rs.getString("destination"), rs.getObject("dep_time", OffsetDateTime.class),
            rs.getString("status"), rs.getString("fare_class"), rs.getBigDecimal("price"),
            rs.getString("first_name"), rs.getString("last_name"), rs.getString("email"),
            rs.getObject("created_at", OffsetDateTime.class), rs.getObject("cancelled_at", OffsetDateTime.class),
            rs.getBoolean("checked_in"));

    static final RowMapper<Flight> FLIGHT = (rs, i) -> new Flight(
            rs.getLong("flight_id"), rs.getString("flight_no"), rs.getString("origin"), rs.getString("destination"),
            rs.getObject("dep_time", OffsetDateTime.class), rs.getObject("arr_time", OffsetDateTime.class),
            rs.getString("status"), rs.getInt("capacity"), rs.getInt("sold"), rs.getBoolean("is_hot"));

    private static final String FLIGHT_SELECT = """
            SELECT f.flight_id, f.flight_no, f.origin, f.destination, f.dep_time, f.arr_time, f.status,
                   i.capacity, i.sold, f.is_hot
              FROM flightops.flight f JOIN flightops.flight_inventory i ON i.flight_id = f.flight_id
            """;

    private final JdbcClient jdbc;

    public ReservationRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** Uçuş arama: rota indeksi (origin, destination, dep_time) kullanır. flexDays arttıkça maliyet artar. */
    public List<Flight> search(String origin, String destination, LocalDate date, int flexDays, int limit) {
        StringBuilder sql = new StringBuilder(FLIGHT_SELECT)
                .append(" WHERE f.dep_time >= :from AND f.dep_time < :to AND f.status IN ('SCHEDULED','DELAYED')");
        List<String> conds = new ArrayList<>();
        if (origin != null && !origin.isBlank()) {
            conds.add(" AND f.origin = :origin");
        }
        if (destination != null && !destination.isBlank()) {
            conds.add(" AND f.destination = :destination");
        }
        conds.forEach(sql::append);
        sql.append(" ORDER BY f.dep_time LIMIT :limit");
        LocalDate d = date == null ? LocalDate.now(ZoneOffset.UTC) : date;
        var spec = jdbc.sql(tag(sql.toString()))
                .param("from", d.minusDays(flexDays).atStartOfDay().atOffset(ZoneOffset.UTC))
                .param("to", d.plusDays(flexDays + 1L).atStartOfDay().atOffset(ZoneOffset.UTC))
                .param("limit", limit);
        if (origin != null && !origin.isBlank()) {
            spec = spec.param("origin", origin.toUpperCase());
        }
        if (destination != null && !destination.isBlank()) {
            spec = spec.param("destination", destination.toUpperCase());
        }
        return spec.query(FLIGHT).list();
    }

    public Optional<Flight> flight(long id) {
        return jdbc.sql(tag(FLIGHT_SELECT + " WHERE f.flight_id = ?")).param(id).query(FLIGHT).optional();
    }

    public List<Flight> hotFlights() {
        return jdbc.sql(tag(FLIGHT_SELECT + " WHERE f.is_hot ORDER BY f.flight_id")).query(FLIGHT).list();
    }

    public Optional<Booking> byPnr(String pnr) {
        return jdbc.sql(tag(BOOKING_SELECT + " WHERE b.pnr = ?")).param(pnr.toUpperCase()).query(BOOKING).optional();
    }

    public Optional<Booking> byIdempotencyKey(String key) {
        return jdbc.sql(tag(BOOKING_SELECT + " WHERE b.idempotency_key = ?")).param(key).query(BOOKING).optional();
    }

    /** Okuma yükü için ucuz rastgele PNR örneği (PK aralık taraması, rastgele başlangıç). */
    public List<String> samplePnrs(int limit) {
        return jdbc.sql(tag("""
                        SELECT pnr FROM reservation.booking
                         WHERE booking_id >= (SELECT floor(random() * greatest(max(booking_id) - ?, 1))
                                                FROM reservation.booking)
                         ORDER BY booking_id LIMIT ?"""))
                .param(limit).param(limit).query(String.class).list();
    }

    /**
     * MALİYETLİ RAPOR: created_at üzerinde indeks yoktur; booking tablosunun tamamı
     * taranır, flight ile join edilir ve gruplanır. DB CPU deneyinde kullanılır.
     */
    public List<RouteDemand> routeDemand(int days, int limit) {
        return jdbc.sql(tag("""
                        SELECT f.origin, f.destination,
                               count(*) AS bookings,
                               count(*) FILTER (WHERE b.status = 'CANCELLED') AS cancelled,
                               round(avg(b.price), 2) AS avg_price,
                               sum(b.price) AS revenue
                          FROM reservation.booking b
                          JOIN flightops.flight f ON f.flight_id = b.flight_id
                         WHERE b.created_at >= now() - make_interval(days => ?)
                         GROUP BY f.origin, f.destination
                         ORDER BY revenue DESC
                         LIMIT ?"""))
                .param(days).param(limit)
                .query((rs, i) -> new RouteDemand(rs.getString(1), rs.getString(2), rs.getLong(3), rs.getLong(4),
                        rs.getBigDecimal(5), rs.getBigDecimal(6)))
                .list();
    }
}
