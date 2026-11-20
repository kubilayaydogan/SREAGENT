-- Ölçeklenebilir sentetik seed ve onaylı reset. Yalnız lab_owner çalıştırabilir
-- (servis rollerinin lab şemasında USAGE yetkisi yoktur).

CREATE OR REPLACE FUNCTION lab.base36(n bigint) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    chars constant text := '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    r text := '';
    v bigint := n;
BEGIN
    IF v = 0 THEN RETURN '0'; END IF;
    WHILE v > 0 LOOP
        r := substr(chars, (v % 36)::int + 1, 1) || r;
        v := v / 36;
    END LOOP;
    RETURN r;
END $$;

-- p_scale          : 1 => ~200 uçuş, ~20k rezervasyon, ~33k bag_event
-- p_events_per_bag : I/O deneyi için bag_event hacmini büyütür (varsayılan 5)
-- p_hot_flights    : kilit deneyleri için ayrılmış "sıcak" uçuş sayısı
-- p_hot_bookings   : her sıcak uçuşta check-in bekleyen CONFIRMED rezervasyon
CREATE OR REPLACE FUNCTION lab.seed(p_scale integer,
                                    p_events_per_bag integer DEFAULT 5,
                                    p_hot_flights integer DEFAULT 4,
                                    p_hot_bookings integer DEFAULT 2000)
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_flights   integer := 200 * p_scale;
    v_bookings  integer := 20000 * p_scale;
    v_hot_total integer := p_hot_flights * p_hot_bookings * p_scale;
    v_ids       bigint[];
    v_n         integer;
    v_pax_base  bigint;
    v_result    jsonb;
BEGIN
    IF p_scale < 1 OR p_scale > 500 THEN
        RAISE EXCEPTION 'scale 1..500 araliginda olmali (verilen %)', p_scale;
    END IF;
    IF p_events_per_bag < 1 OR p_events_per_bag > 500 THEN
        RAISE EXCEPTION 'events_per_bag 1..500 araliginda olmali';
    END IF;
    IF EXISTS (SELECT 1 FROM flightops.flight) THEN
        RAISE EXCEPTION 'Veri zaten var. Once onayli reset calistirin (db-migrator reset).';
    END IF;

    PERFORM setseed(0.42);  -- tekrarlanabilir dağılım

    INSERT INTO flightops.airport(code, city, country) VALUES
      ('IST','Istanbul','TR'),('SAW','Istanbul','TR'),('ESB','Ankara','TR'),('ADB','Izmir','TR'),
      ('AYT','Antalya','TR'),('TZX','Trabzon','TR'),('LHR','London','GB'),('CDG','Paris','FR'),
      ('FRA','Frankfurt','DE'),('AMS','Amsterdam','NL'),('FCO','Rome','IT'),('MAD','Madrid','ES'),
      ('JFK','New York','US'),('DXB','Dubai','AE'),('DOH','Doha','QA'),('NRT','Tokyo','JP'),
      ('SIN','Singapore','SG'),('GRU','Sao Paulo','BR'),('CPT','Cape Town','ZA'),('SYD','Sydney','AU');

    -- Normal uçuşlar: -30..+30 gün aralığına dağılmış
    INSERT INTO flightops.flight(flight_no, origin, destination, dep_time, arr_time, aircraft, status)
    SELECT 'TK' || lpad(g::text, 4, '0'),
           r.o, r.d, r.dep, r.dep + make_interval(mins => 60 + (random()*600)::int),
           (ARRAY['A320','A321','B737','B738','A330','B777','B787'])[1 + floor(random()*7)::int],
           CASE WHEN r.dep < now() THEN 'ARRIVED' ELSE 'SCHEDULED' END
    FROM generate_series(1, v_flights) g
    CROSS JOIN LATERAL (
        SELECT a1.code AS o, a2.code AS d,
               date_trunc('minute', now() - interval '30 days' + random() * interval '60 days') + g * interval '0 second' AS dep
        FROM (SELECT code FROM flightops.airport ORDER BY random() + g*0 LIMIT 1) a1,
             LATERAL (SELECT code FROM flightops.airport WHERE code <> a1.code ORDER BY random() + g*0 LIMIT 1) a2
    ) r;

    INSERT INTO flightops.flight_inventory(flight_id, capacity)
    SELECT flight_id, 150 + (random()*150)::int FROM flightops.flight;

    -- Sıcak uçuşlar: büyük kapasite, check-in bekleyen çok sayıda yolcu
    INSERT INTO flightops.flight(flight_no, origin, destination, dep_time, arr_time, aircraft, status, is_hot)
    SELECT 'TK9H' || lpad(h::text, 2, '0'), 'IST', 'LHR',
           date_trunc('hour', now()) + interval '2 days' + h * interval '1 hour',
           date_trunc('hour', now()) + interval '2 days' + h * interval '1 hour' + interval '4 hours',
           'B777', 'SCHEDULED', true
    FROM generate_series(1, p_hot_flights) h;
    INSERT INTO flightops.flight_inventory(flight_id, capacity)
    SELECT flight_id, 1000000 FROM flightops.flight WHERE is_hot;

    -- Yolcular (rezervasyon başına bir yolcu)
    INSERT INTO reservation.passenger(first_name, last_name, email)
    SELECT (ARRAY['Ali','Ayse','Mehmet','Zeynep','Can','Elif','Emre','Deniz','Selin','Mert'])[1 + floor(random()*10)::int],
           (ARRAY['Yilmaz','Kaya','Demir','Sahin','Celik','Aydin','Ozturk','Arslan','Dogan','Kilic'])[1 + floor(random()*10)::int],
           'pax' || g || '@example.test'
    FROM generate_series(1, v_bookings + v_hot_total) g;
    SELECT min(passenger_id) - 1 INTO v_pax_base FROM reservation.passenger;

    -- Normal rezervasyonlar
    SELECT array_agg(flight_id ORDER BY flight_id), count(*) INTO v_ids, v_n
    FROM flightops.flight WHERE NOT is_hot;

    INSERT INTO reservation.booking(pnr, flight_id, passenger_id, status, fare_class, price, created_at, updated_at, cancelled_at)
    SELECT 'S' || lpad(lab.base36(g), 6, '0'),
           f.flight_id, v_pax_base + g,
           CASE WHEN random() < 0.07 THEN 'CANCELLED' ELSE 'CONFIRMED' END,
           (ARRAY['Y','Y','Y','Y','B','J'])[1 + floor(random()*6)::int],
           round((50 + random()*900)::numeric, 2),
           f.dep_time - random() * interval '20 days',
           f.dep_time - random() * interval '1 day',
           NULL
    FROM generate_series(1, v_bookings) g
    JOIN LATERAL (SELECT v_ids[1 + floor(random()*v_n)::int + (g*0)] AS fid) pick ON true
    JOIN flightops.flight f ON f.flight_id = pick.fid;
    UPDATE reservation.booking SET cancelled_at = updated_at WHERE status = 'CANCELLED';

    -- Sıcak uçuş rezervasyonları (hepsi CONFIRMED, check-in yapılmamış)
    INSERT INTO reservation.booking(pnr, flight_id, passenger_id, status, fare_class, price, created_at)
    SELECT 'S' || lpad(lab.base36(v_bookings + g), 6, '0'),
           hf.flight_id, v_pax_base + v_bookings + g, 'CONFIRMED', 'Y', 199.00, now() - interval '3 days'
    FROM generate_series(1, v_hot_total) g
    JOIN LATERAL (SELECT flight_id FROM flightops.flight WHERE is_hot
                  ORDER BY flight_id OFFSET ((g - 1) % p_hot_flights) LIMIT 1) hf ON true;

    -- Envanter: sold = CONFIRMED sayısı; kapasite asla sold'un altında kalmaz
    UPDATE flightops.flight_inventory i
       SET capacity = greatest(i.capacity, s.cnt), sold = s.cnt
      FROM (SELECT flight_id, count(*)::int AS cnt FROM reservation.booking
            WHERE status = 'CONFIRMED' GROUP BY flight_id) s
     WHERE s.flight_id = i.flight_id;

    -- Check-in: geçmiş uçuşların tamamı, 24 saat içindekilerin yarısı; sıcak uçuşlar hariç
    INSERT INTO checkin.checkin(booking_id, flight_id, channel, checked_in_at)
    SELECT b.booking_id, b.flight_id,
           (ARRAY['WEB','KIOSK','COUNTER','MOBILE'])[1 + floor(random()*4)::int],
           f.dep_time - random() * interval '20 hours'
    FROM reservation.booking b JOIN flightops.flight f USING (flight_id)
    WHERE b.status = 'CONFIRMED' AND NOT f.is_hot
      AND (f.dep_time < now() OR (f.dep_time < now() + interval '24 hours' AND random() < 0.5));

    INSERT INTO checkin.seat_assignment(flight_id, seat_no, booking_id, assigned_at)
    SELECT flight_id,
           (((rn - 1) / 6) + 1)::text || chr(65 + ((rn - 1) % 6)::int),
           booking_id, checked_in_at
    FROM (SELECT c.*, row_number() OVER (PARTITION BY flight_id ORDER BY booking_id) rn
          FROM checkin.checkin c) x
    WHERE rn <= 594;

    UPDATE flightops.flight_inventory i SET checked_in = s.cnt
      FROM (SELECT flight_id, count(*)::int cnt FROM checkin.checkin GROUP BY flight_id) s
     WHERE s.flight_id = i.flight_id;

    -- Bagaj: check-in yapanların ~%70'i
    INSERT INTO baggage.bag(tag, booking_id, flight_id, weight_kg, status, created_at, updated_at)
    SELECT 'S' || lpad(lab.base36(c.checkin_id), 8, '0'), c.booking_id, c.flight_id,
           round((5 + random()*27)::numeric, 2),
           CASE WHEN f.dep_time < now() THEN 'DELIVERED' ELSE 'CHECKED' END,
           c.checked_in_at, c.checked_in_at
    FROM checkin.checkin c JOIN flightops.flight f USING (flight_id)
    WHERE random() < 0.7;

    -- Bagaj olay geçmişi: I/O deneyi için satır başına ~330 bayt (sıkıştırılamaz not)
    INSERT INTO baggage.bag_event(bag_id, flight_id, event_type, location, note, created_at)
    SELECT b.bag_id, b.flight_id,
           (ARRAY['CHECKED','LOADED','IN_TRANSIT','ARRIVED','DELIVERED'])[1 + ((e - 1) % 5)],
           (ARRAY['IST','BELT-1','BELT-2','RAMP','HOLD','TRANSFER'])[1 + floor(random()*6)::int],
           md5(random()::text) || md5(random()::text) || md5(random()::text) || md5(random()::text)
             || md5(random()::text) || md5(random()::text) || md5(random()::text) || md5(random()::text),
           b.created_at + e * interval '7 minutes'
    FROM baggage.bag b CROSS JOIN generate_series(1, p_events_per_bag) e;

    ANALYZE flightops.flight, flightops.flight_inventory, reservation.passenger, reservation.booking,
            checkin.checkin, checkin.seat_assignment, baggage.bag, baggage.bag_event;

    SELECT jsonb_build_object(
        'scale', p_scale,
        'events_per_bag', p_events_per_bag,
        'flights', (SELECT count(*) FROM flightops.flight),
        'hot_flights', (SELECT count(*) FROM flightops.flight WHERE is_hot),
        'bookings', (SELECT count(*) FROM reservation.booking),
        'checkins', (SELECT count(*) FROM checkin.checkin),
        'seats', (SELECT count(*) FROM checkin.seat_assignment),
        'bags', (SELECT count(*) FROM baggage.bag),
        'bag_events', (SELECT count(*) FROM baggage.bag_event),
        'db_size', pg_size_pretty(pg_database_size(current_database()))
    ) INTO v_result;
    RETURN v_result;
END $$;

-- Tüm iş verisini siler. db-migrator yalnız açık onayla (LAB_ALLOW_RESET=true ve
-- --confirm=<veritabanı adı>) çağırır.
CREATE OR REPLACE FUNCTION lab.reset_data() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    TRUNCATE baggage.bag_event, baggage.bag,
             checkin.seat_assignment, checkin.checkin,
             reservation.booking, reservation.passenger,
             flightops.ops_event, flightops.flight_inventory, flightops.flight, flightops.airport
             RESTART IDENTITY CASCADE;
END $$;

REVOKE ALL ON FUNCTION lab.seed(integer, integer, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION lab.reset_data() FROM PUBLIC;

-- Yerleşik veri tutarlılık kontrolü (smoke testleri ve runbook kullanır)
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
 WHERE c.booking_id IS NULL;
