package com.lab.air.baggage;

import com.lab.air.baggage.Model.Bag;
import com.lab.air.baggage.Model.BagEvent;
import com.lab.air.baggage.Model.HistoryRow;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static com.lab.air.common.SqlTag.tag;

/**
 * Baggage okumaları. reservation/checkin tablolarını YALNIZ kilitsiz SELECT ile okur;
 * ortak envanter satırına hiç dokunmaz → kilit deneylerinde kontrol grubu.
 */
@Repository
public class BaggageRepository {

    static final String BAG_SELECT = """
            SELECT g.bag_id, g.tag, b.pnr, g.booking_id, g.flight_id, g.weight_kg, g.status, g.created_at, g.updated_at
              FROM baggage.bag g JOIN reservation.booking b ON b.booking_id = g.booking_id
            """;

    static final RowMapper<Bag> BAG = (rs, i) -> new Bag(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getLong(4),
            rs.getLong(5), rs.getBigDecimal(6), rs.getString(7), rs.getObject(8, OffsetDateTime.class),
            rs.getObject(9, OffsetDateTime.class));

    private final JdbcClient jdbc;

    public BaggageRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Bag> byTag(String tag) {
        return jdbc.sql(tag(BAG_SELECT + " WHERE g.tag = ?")).param(tag.toUpperCase()).query(BAG).optional();
    }

    public Optional<Bag> byIdempotencyKey(String key) {
        return jdbc.sql(tag(BAG_SELECT + " WHERE g.idempotency_key = ?")).param(key).query(BAG).optional();
    }

    public List<Bag> byFlight(long flightId, int limit) {
        return jdbc.sql(tag(BAG_SELECT + " WHERE g.flight_id = ? ORDER BY g.bag_id LIMIT ?"))
                .param(flightId).param(limit).query(BAG).list();
    }

    public List<Bag> byPnr(String pnr) {
        return jdbc.sql(tag(BAG_SELECT + " WHERE b.pnr = ? ORDER BY g.bag_id")).param(pnr.toUpperCase()).query(BAG).list();
    }

    public List<BagEvent> events(long bagId) {
        return jdbc.sql(tag("""
                        SELECT event_id, event_type, location, created_at FROM baggage.bag_event
                         WHERE bag_id = ? ORDER BY created_at, event_id"""))
                .param(bagId)
                .query((rs, i) -> new BagEvent(rs.getLong(1), rs.getString(2), rs.getString(3),
                        rs.getObject(4, OffsetDateTime.class)))
                .list();
    }

    /** Okuma yükü için ucuz rastgele etiket örneği (PK aralık taraması). */
    public List<String> sampleTags(int limit) {
        return jdbc.sql(tag("""
                        SELECT tag FROM baggage.bag
                         WHERE bag_id >= (SELECT floor(random() * greatest(max(bag_id) - ?, 1)) FROM baggage.bag)
                         ORDER BY bag_id LIMIT ?"""))
                .param(limit).param(limit).query(String.class).list();
    }

    /** Bagaj kaydı yapılabilecek (check-in yapılmış, < 3 bagaj) gelecekteki rezervasyonlar. */
    public List<String> candidates(int limit) {
        return candidates(limit, false);
    }

    /** hot=true → yalnız sıcak uçuşlar (zincirleme kilit deneyi: bagaj kaydı check-in sayacını bekler). */
    public List<String> candidates(int limit, boolean hot) {
        return jdbc.sql(tag("""
                        SELECT b.pnr
                          FROM checkin.checkin c
                          JOIN reservation.booking b ON b.booking_id = c.booking_id
                          JOIN flightops.flight f ON f.flight_id = c.flight_id
                         WHERE f.dep_time > now() AND b.status = 'CONFIRMED' AND (f.is_hot OR NOT ?)
                           AND (SELECT count(*) FROM baggage.bag g WHERE g.booking_id = c.booking_id) < 3
                         ORDER BY random() LIMIT ?"""))
                .params(hot, limit).query(String.class).list();
    }

    /**
     * I/O AĞIRLIKLI RAPOR: bag_event.flight_id üzerinde bilinçli olarak indeks yoktur.
     * Tablo tamamen taranır; seed --events-per-bag ile tablo shared_buffers'ı aşacak
     * boyuta büyütüldüğünde disk okuması (blks_read, I/O wait) üretir.
     */
    public List<HistoryRow> history(Long flightId, int days) {
        String where = flightId != null ? " AND e.flight_id = ?" : "";
        var spec = jdbc.sql(tag("""
                        SELECT e.event_type, e.location, count(*), min(e.created_at), max(e.created_at),
                               round(avg(length(e.note)), 1)
                          FROM baggage.bag_event e
                         WHERE e.created_at >= now() - make_interval(days => ?)""" + where + """

                         GROUP BY e.event_type, e.location
                         ORDER BY count(*) DESC"""))
                .param(days);
        if (flightId != null) {
            spec = spec.param(flightId);
        }
        return spec.query((rs, i) -> new HistoryRow(rs.getString(1), rs.getString(2), rs.getLong(3),
                rs.getObject(4, OffsetDateTime.class), rs.getObject(5, OffsetDateTime.class), rs.getBigDecimal(6))).list();
    }
}
