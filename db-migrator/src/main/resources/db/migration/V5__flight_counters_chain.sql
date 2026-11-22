-- V5: Uçuş başına check-in ve bagaj sayaçları → PostgreSQL üzerinden ZİNCİRLEME kilit bağımlılığı.
--
-- İş kuralı                                              Kilit sırası (transaction içinde)
--   Check-in   : sayaç +1, sonra envanter checked_in +1     checkin.flight_checkin_stats  →  flightops.flight_inventory
--   Bagaj kaydı: bagaj sayacı +1, sonra check-in sayısını   baggage.flight_baggage_stats  →  checkin.flight_checkin_stats (FOR SHARE)
--                tutarlı okur (bagaj ≤ yolcu × limit)
--   Uçuş durumu: uçuş satırı, sonra bagaj sayısını tutarlı  flightops.flight              →  baggage.flight_baggage_stats (FOR SHARE)
--                okur (kalkış/kapı kararı yüklü bagaja bağlı)
--
-- Sonuç: Reservation envanter satırını tutarsa Check-in bekler; bekleyen Check-in kendi sayaç satırını tuttuğu
-- için Baggage bekler; bekleyen Baggage kendi sayacını tuttuğu için Flight Ops bekler. Bekleyen istekler Hikari
-- bağlantısını tuttuğundan her servisin diğer işlemleri de yavaşlar. Kilit grafiği döngüsüzdür (deadlock yok):
--   inventory ← checkin_stats ← baggage_stats ← flight
-- Rol ayarlarındaki lock_timeout (8 sn) her halkadaki bekleme süresini sınırlar.

CREATE TABLE checkin.flight_checkin_stats (
    flight_id        bigint PRIMARY KEY REFERENCES flightops.flight(flight_id),
    checked_in       integer NOT NULL DEFAULT 0 CHECK (checked_in >= 0),
    last_checkin_at  timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE baggage.flight_baggage_stats (
    flight_id        bigint PRIMARY KEY REFERENCES flightops.flight(flight_id),
    bags             integer NOT NULL DEFAULT 0 CHECK (bags >= 0),
    total_weight_kg  numeric(10,1) NOT NULL DEFAULT 0,
    last_bag_at      timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Mevcut veriden başlangıç değerleri (bütünlük görünümü ile tutarlı başlar)
INSERT INTO checkin.flight_checkin_stats(flight_id, checked_in, last_checkin_at)
SELECT flight_id, count(*), max(checked_in_at) FROM checkin.checkin GROUP BY flight_id;

INSERT INTO baggage.flight_baggage_stats(flight_id, bags, total_weight_kg, last_bag_at)
SELECT flight_id, count(*), coalesce(sum(weight_kg), 0), max(created_at) FROM baggage.bag GROUP BY flight_id;

-- ---------- yetkiler
GRANT SELECT, INSERT, UPDATE ON checkin.flight_checkin_stats TO svc_checkin;
GRANT SELECT, INSERT, UPDATE ON baggage.flight_baggage_stats TO svc_baggage;
-- SELECT ... FOR SHARE en az bir kolonda UPDATE yetkisi ister; yalnız updated_at açılır (V3'teki booking deseni)
GRANT SELECT, UPDATE (updated_at) ON checkin.flight_checkin_stats TO svc_baggage;
GRANT USAGE ON SCHEMA baggage TO svc_flightops;
GRANT SELECT, UPDATE (updated_at) ON baggage.flight_baggage_stats TO svc_flightops;

-- ---------- seed sayaçları da doldurur: V4 seed'i yeniden adlandırılır, aynı imzalı sarmalayıcı onu çağırıp
--            sayaçları gerçek kayıtlardan yeniden hesaplar (çağıranlar değişmez).
ALTER FUNCTION lab.seed(integer, integer, integer, integer) RENAME TO seed_v4;
REVOKE ALL ON FUNCTION lab.seed_v4(integer, integer, integer, integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION lab.rebuild_flight_counters() RETURNS void
LANGUAGE sql AS $$
    DELETE FROM checkin.flight_checkin_stats;
    INSERT INTO checkin.flight_checkin_stats(flight_id, checked_in, last_checkin_at)
    SELECT flight_id, count(*), max(checked_in_at) FROM checkin.checkin GROUP BY flight_id;
    DELETE FROM baggage.flight_baggage_stats;
    INSERT INTO baggage.flight_baggage_stats(flight_id, bags, total_weight_kg, last_bag_at)
    SELECT flight_id, count(*), coalesce(sum(weight_kg), 0), max(created_at) FROM baggage.bag GROUP BY flight_id;
$$;
REVOKE ALL ON FUNCTION lab.rebuild_flight_counters() FROM PUBLIC;

CREATE FUNCTION lab.seed(p_scale integer,
                         p_events_per_bag integer DEFAULT 5,
                         p_hot_flights integer DEFAULT 4,
                         p_hot_bookings integer DEFAULT 2000)
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE r jsonb;
BEGIN
    r := lab.seed_v4(p_scale, p_events_per_bag, p_hot_flights, p_hot_bookings);
    PERFORM lab.rebuild_flight_counters();
    RETURN r;
END $$;
REVOKE ALL ON FUNCTION lab.seed(integer, integer, integer, integer) FROM PUBLIC;

-- ---------- sıfırlama yeni tabloları da kapsar
CREATE OR REPLACE FUNCTION lab.reset_data() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    TRUNCATE baggage.flight_baggage_stats, baggage.bag_event, baggage.bag,
             checkin.flight_checkin_stats, checkin.seat_assignment, checkin.checkin,
             reservation.booking, reservation.passenger,
             flightops.ops_event, flightops.flight_inventory, flightops.flight, flightops.airport
             RESTART IDENTITY CASCADE;
END $$;
REVOKE ALL ON FUNCTION lab.reset_data() FROM PUBLIC;

-- ---------- bütünlük: sayaçlar gerçek kayıtlarla eşleşmeli
CREATE OR REPLACE VIEW lab.integrity_check AS
SELECT 'inventory_sold_mismatch' AS check_name, count(*) AS violations
  FROM flightops.flight_inventory i
  LEFT JOIN (SELECT flight_id, count(*) c FROM reservation.booking WHERE status='CONFIRMED' GROUP BY flight_id) b
         ON b.flight_id = i.flight_id
 WHERE i.sold <> coalesce(b.c, 0)
UNION ALL
SELECT 'inventory_checkedin_mismatch', count(*)
  FROM flightops.flight_inventory i
  LEFT JOIN (SELECT flight_id, count(*) c FROM checkin.checkin GROUP BY flight_id) c
         ON c.flight_id = i.flight_id
 WHERE i.checked_in <> coalesce(c.c, 0)
UNION ALL
SELECT 'checkin_on_cancelled_booking', count(*)
  FROM checkin.checkin c JOIN reservation.booking b USING (booking_id)
 WHERE b.status = 'CANCELLED'
UNION ALL
SELECT 'overbooked_flights', count(*)
  FROM flightops.flight_inventory WHERE sold > capacity
UNION ALL
SELECT 'bag_without_checkin', count(*)
  FROM baggage.bag g LEFT JOIN checkin.checkin c USING (booking_id)
 WHERE c.booking_id IS NULL
UNION ALL
SELECT 'checkin_stats_mismatch', count(*)
  FROM (SELECT flight_id, count(*) c FROM checkin.checkin GROUP BY flight_id) c
  FULL JOIN checkin.flight_checkin_stats s USING (flight_id)
 WHERE coalesce(s.checked_in, 0) <> coalesce(c.c, 0)
UNION ALL
SELECT 'baggage_stats_mismatch', count(*)
  FROM (SELECT flight_id, count(*) c FROM baggage.bag GROUP BY flight_id) g
  FULL JOIN baggage.flight_baggage_stats s USING (flight_id)
 WHERE coalesce(s.bags, 0) <> coalesce(g.c, 0);
