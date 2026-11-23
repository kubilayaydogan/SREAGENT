-- Chaos v2: iş verisinden İZOLE sentetik şema. Tüm sentetik workload'lar (yazma burst'ü, ağır okuma,
-- temp spill, deadlock, satır contention) yalnız bu tablolara dokunur; iş tablolarına yıkıcı işlem yapılmaz.
-- Workload'lar uygulamaların KENDİ rolleriyle (svc_*) çalışır ki pg_stat_activity / pg_stat_statements /
-- blocking zincirinde etki doğru uygulamaya atfedilsin.
--
-- Boyut sınırı: synthetic_data varsayılan 400k satır (~70 MB). chaos.resize_synthetic(n) ile en fazla
-- 5M satıra (~900 MB) büyütülebilir (yalnız lab_owner / db-migrator `chaos-seed`). write_sink chaos.trim_write_sink
-- ile sınırlandırılır; cleanup sırasında boşaltılır.

CREATE SCHEMA IF NOT EXISTS chaos;

-- ---------- ağır okuma / CPU / temp spill hedefi (k ve val üzerinde BİLİNÇLİ olarak indeks yok)
CREATE TABLE chaos.synthetic_data (
    id       bigint PRIMARY KEY,
    bucket   integer NOT NULL,
    k        integer NOT NULL,
    val      double precision NOT NULL,
    payload  text NOT NULL
);

-- ---------- satır contention / long transaction hedefi (birden fazla uygulama aynı satırları günceller)
CREATE TABLE chaos.shared_records (
    id          integer PRIMARY KEY,
    counter     bigint NOT NULL DEFAULT 0,
    touched_by  text,
    touched_at  timestamptz
);

-- ---------- deadlock çiftleri (a,b) = (2i, 2i+1)
CREATE TABLE chaos.deadlock_pairs (
    id  integer PRIMARY KEY,
    v   bigint NOT NULL DEFAULT 0
);

-- ---------- yazma burst'ü (WAL + I/O); sınırsız büyümez: trim_write_sink
CREATE TABLE chaos.write_sink (
    id             bigserial PRIMARY KEY,
    app            text NOT NULL,
    experiment_id  text,
    payload        text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX write_sink_created_idx ON chaos.write_sink (created_at);

INSERT INTO chaos.shared_records (id) SELECT g FROM generate_series(0, 999) g;
INSERT INTO chaos.deadlock_pairs (id) SELECT g FROM generate_series(0, 399) g;

CREATE OR REPLACE FUNCTION chaos.resize_synthetic(p_rows integer) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE cur bigint;
BEGIN
    IF p_rows < 10000 OR p_rows > 5000000 THEN
        RAISE EXCEPTION 'p_rows 10000..5000000 olmalı (verilen %)', p_rows;
    END IF;
    SELECT count(*) INTO cur FROM chaos.synthetic_data;
    IF cur > p_rows THEN
        DELETE FROM chaos.synthetic_data WHERE id >= p_rows;
    ELSIF cur < p_rows THEN
        INSERT INTO chaos.synthetic_data (id, bucket, k, val, payload)
        SELECT g, g % 512, (g * 7919) % 100000, random() * 1000,
               md5(g::text) || md5((g * 31)::text) || md5((g * 17)::text) || md5((g * 13)::text)
          FROM generate_series(cur, p_rows - 1) g;
    END IF;
    ANALYZE chaos.synthetic_data;
    RETURN p_rows;
END $$;
REVOKE ALL ON FUNCTION chaos.resize_synthetic(integer) FROM PUBLIC;

SELECT chaos.resize_synthetic(400000);

-- svc_* rolleri write_sink'i silemez; sınırlı budama bu SECURITY DEFINER fonksiyonla yapılır (owner: lab_owner).
CREATE OR REPLACE FUNCTION chaos.trim_write_sink(p_keep integer) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, chaos AS $$
DECLARE n bigint;
BEGIN
    IF p_keep < 0 THEN
        RAISE EXCEPTION 'p_keep negatif olamaz';
    END IF;
    IF p_keep = 0 THEN
        SELECT count(*) INTO n FROM chaos.write_sink;
        TRUNCATE chaos.write_sink;
        RETURN n;
    END IF;
    DELETE FROM chaos.write_sink
     WHERE id < (SELECT coalesce(max(id), 0) - p_keep FROM chaos.write_sink);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;
REVOKE ALL ON FUNCTION chaos.trim_write_sink(integer) FROM PUBLIC;

-- ---------- en az yetki
GRANT USAGE ON SCHEMA chaos TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT SELECT ON chaos.synthetic_data TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT SELECT, UPDATE (counter, touched_by, touched_at) ON chaos.shared_records
      TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT SELECT, UPDATE (v) ON chaos.deadlock_pairs TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT SELECT, INSERT ON chaos.write_sink TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT USAGE ON SEQUENCE chaos.write_sink_id_seq TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT EXECUTE ON FUNCTION chaos.trim_write_sink(integer) TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT USAGE ON SCHEMA chaos TO lab_monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA chaos TO lab_monitor;
