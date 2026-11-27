-- Chaos v2: yeni PostgreSQL yük tipleri için sentetik nesneler (iş verisinden izole; yalnız chaos şeması).
--   * chaos.lock_target : tablo seviyesinde kilit kuyruğu (ACCESS EXCLUSIVE tutulur, okuyanlar bekler).
--   * chaos.churn       : toplu UPDATE hedefi; v indeksli olduğundan güncellemeler HOT olmaz → dead tuple,
--                         indeks bakımı, WAL ve autovacuum işi oluşur.
-- Diğer yeni yük tipleri (serialization çakışması, commit storm, advisory lock, idle in transaction,
-- parallel query, point query, bellek içi sort) mevcut chaos.* tablolarını kullanır ya da tablo gerektirmez.

CREATE TABLE chaos.lock_target (
    id   integer PRIMARY KEY,
    val  integer NOT NULL DEFAULT 0
);
INSERT INTO chaos.lock_target (id) SELECT g FROM generate_series(0, 999) g;

CREATE TABLE chaos.churn (
    id          integer PRIMARY KEY,
    v           bigint NOT NULL DEFAULT 0,
    pad         text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX churn_v_idx ON chaos.churn (v);
INSERT INTO chaos.churn (id, pad) SELECT g, md5(g::text) FROM generate_series(0, 49999) g;
ANALYZE chaos.churn;

-- en az yetki: LOCK TABLE ... ACCESS EXCLUSIVE için tablo seviyesinde UPDATE yetkisi gerekir (satır silinemez).
GRANT SELECT, UPDATE ON chaos.lock_target TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT SELECT, UPDATE (v, pad, updated_at) ON chaos.churn TO svc_reservation, svc_checkin, svc_flightops, svc_baggage;
GRANT SELECT ON chaos.lock_target, chaos.churn TO lab_monitor;
