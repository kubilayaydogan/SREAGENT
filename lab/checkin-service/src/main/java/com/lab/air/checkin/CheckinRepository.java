package com.lab.air.checkin;

import com.lab.air.checkin.Model.Candidate;
import com.lab.air.checkin.Model.CheckinView;
import com.lab.air.checkin.Model.Seat;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static com.lab.air.common.SqlTag.tag;

@Repository
public class CheckinRepository {

    static final RowMapper<CheckinView> VIEW = (rs, i) -> new CheckinView(
            rs.getString("pnr"), rs.getLong("booking_id"), rs.getLong("flight_id"), rs.getString("flight_no"),
            rs.getString("origin"), rs.getString("destination"), rs.getObject("dep_time", OffsetDateTime.class),
            rs.getString("flight_status"), rs.getString("booking_status"), rs.getString("first_name"),
            rs.getString("last_name"), rs.getObject("checked_in_at") != null,
            rs.getObject("checked_in_at", OffsetDateTime.class), rs.getString("channel"), rs.getString("seat_no"));

    private static final String CANDIDATES = """
            SELECT b.pnr, b.flight_id
              FROM reservation.booking b
              JOIN flightops.flight f ON f.flight_id = b.flight_id
             WHERE b.status = 'CONFIRMED'
               AND f.status IN ('SCHEDULED','DELAYED','BOARDING') AND f.dep_time > now()
               AND NOT EXISTS (SELECT 1 FROM checkin.checkin c WHERE c.booking_id = b.booking_id)
            """;

    private static final RowMapper<Candidate> CANDIDATE = (rs, i) -> new Candidate(rs.getString(1), rs.getLong(2));

    private final JdbcClient jdbc;

    public CheckinRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** Kilitsiz okuma: PostgreSQL MVCC sayesinde envanter kilidi tutulurken de bloklanmaz. */
    public Optional<CheckinView> view(String pnr) {
        return jdbc.sql(tag("""
                        SELECT b.pnr, b.booking_id, b.flight_id, f.flight_no, f.origin, f.destination, f.dep_time,
                               f.status AS flight_status, b.status AS booking_status, p.first_name, p.last_name,
                               c.checked_in_at, c.channel, s.seat_no
                          FROM reservation.booking b
                          JOIN reservation.passenger p ON p.passenger_id = b.passenger_id
                          JOIN flightops.flight f ON f.flight_id = b.flight_id
                          LEFT JOIN checkin.checkin c ON c.booking_id = b.booking_id
                          LEFT JOIN checkin.seat_assignment s ON s.booking_id = b.booking_id
                         WHERE b.pnr = ?"""))
                .param(pnr.toUpperCase()).query(VIEW).optional();
    }

    /** Check-in yapılmamış CONFIRMED rezervasyonlar (kaos yük üreticisinin veri havuzu). */
    public List<Candidate> candidates(Long flightId, boolean hotOnly, int limit) {
        if (flightId != null) {
            return jdbc.sql(tag(CANDIDATES + " AND b.flight_id = ? ORDER BY random() LIMIT ?"))
                    .param(flightId).param(limit).query(CANDIDATE).list();
        }
        String filter = hotOnly ? " AND f.is_hot" : " AND NOT f.is_hot";
        return jdbc.sql(tag(CANDIDATES + filter + " ORDER BY random() LIMIT ?"))
                .param(limit).query(CANDIDATE).list();
    }

    public List<Seat> seatMap(long flightId) {
        return jdbc.sql(tag("""
                        SELECT s.seat_no, b.pnr FROM checkin.seat_assignment s
                          JOIN reservation.booking b ON b.booking_id = s.booking_id
                         WHERE s.flight_id = ? ORDER BY length(s.seat_no), s.seat_no"""))
                .param(flightId).query((rs, i) -> new Seat(rs.getString(1), rs.getString(2))).list();
    }
}
