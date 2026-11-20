package com.lab.air.flightops;

import com.lab.air.flightops.Model.Flight;
import com.lab.air.flightops.Model.LoadFactor;
import com.lab.air.flightops.Model.OpsEvent;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static com.lab.air.common.SqlTag.tag;

@Repository
public class FlightOpsRepository {

    static final String FLIGHT_SELECT = """
            SELECT f.flight_id, f.flight_no, f.origin, f.destination, f.dep_time, f.arr_time, f.aircraft,
                   f.status, f.gate, f.delay_min, i.capacity, i.sold, i.checked_in, f.is_hot, i.version, f.updated_at
              FROM flightops.flight f JOIN flightops.flight_inventory i ON i.flight_id = f.flight_id
            """;

    static final RowMapper<Flight> FLIGHT = (rs, i) -> new Flight(rs.getLong(1), rs.getString(2), rs.getString(3),
            rs.getString(4), rs.getObject(5, OffsetDateTime.class), rs.getObject(6, OffsetDateTime.class),
            rs.getString(7), rs.getString(8), rs.getString(9), rs.getInt(10), rs.getInt(11), rs.getInt(12),
            rs.getInt(13), rs.getBoolean(14), rs.getLong(15), rs.getObject(16, OffsetDateTime.class));

    private final JdbcClient jdbc;

    public FlightOpsRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public List<Flight> list(String status, int hoursAhead, int limit) {
        String sql = FLIGHT_SELECT + " WHERE f.dep_time BETWEEN now() - interval '6 hours' AND now() + make_interval(hours => ?)"
                + (status != null ? " AND f.status = ?" : "") + " ORDER BY f.dep_time LIMIT ?";
        var spec = jdbc.sql(tag(sql)).param(hoursAhead);
        if (status != null) {
            spec = spec.param(status);
        }
        return spec.param(limit).query(FLIGHT).list();
    }

    public Optional<Flight> get(long id) {
        return jdbc.sql(tag(FLIGHT_SELECT + " WHERE f.flight_id = ?")).param(id).query(FLIGHT).optional();
    }

    public List<Flight> hot() {
        return jdbc.sql(tag(FLIGHT_SELECT + " WHERE f.is_hot ORDER BY f.flight_id")).query(FLIGHT).list();
    }

    /** Okuma yükü için ucuz rastgele uçuş kimliği örneği. */
    public List<Long> sampleIds(int limit) {
        return jdbc.sql(tag("""
                        SELECT flight_id FROM flightops.flight
                         WHERE NOT is_hot AND dep_time > now() AND status IN ('SCHEDULED','DELAYED')
                         ORDER BY random() LIMIT ?"""))
                .param(limit).query(Long.class).list();
    }

    public List<OpsEvent> events(long flightId, int limit) {
        return jdbc.sql(tag("""
                        SELECT event_id, event_type, detail, created_at FROM flightops.ops_event
                         WHERE flight_id = ? ORDER BY created_at DESC LIMIT ?"""))
                .param(flightId).param(limit)
                .query((rs, i) -> new OpsEvent(rs.getLong(1), rs.getString(2), rs.getString(3),
                        rs.getObject(4, OffsetDateTime.class)))
                .list();
    }

    /**
     * MALİYETLİ RAPOR: zaman penceresindeki tüm uçuşlar için booking tablosunu
     * join + gruplayarak envanter tutarlılığını ve doluluğu hesaplar. Pencere
     * büyüdükçe (days) DB CPU maliyeti artar. Kilit almaz (MVCC okuma).
     */
    public List<LoadFactor> loadFactor(int days, int limit) {
        return jdbc.sql(tag("""
                        SELECT f.flight_id, f.flight_no, f.origin, f.destination, f.dep_time, i.capacity, i.sold,
                               count(b.booking_id) FILTER (WHERE b.status = 'CONFIRMED') AS confirmed,
                               count(b.booking_id) FILTER (WHERE b.status = 'CANCELLED') AS cancelled,
                               round(100.0 * i.sold / i.capacity, 1) AS lf
                          FROM flightops.flight f
                          JOIN flightops.flight_inventory i ON i.flight_id = f.flight_id
                          LEFT JOIN reservation.booking b ON b.flight_id = f.flight_id
                         WHERE f.dep_time BETWEEN now() - make_interval(days => ?) AND now() + make_interval(days => ?)
                         GROUP BY f.flight_id, i.flight_id
                         ORDER BY lf DESC, f.flight_id
                         LIMIT ?"""))
                .param(days).param(days).param(limit)
                .query((rs, i) -> new LoadFactor(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4),
                        rs.getObject(5, OffsetDateTime.class), rs.getInt(6), rs.getInt(7), rs.getLong(8),
                        rs.getLong(9), rs.getBigDecimal(10), rs.getInt(7) != rs.getLong(8)))
                .list();
    }
}
