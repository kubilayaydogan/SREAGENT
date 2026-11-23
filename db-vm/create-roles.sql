-- Superuser (postgres) ile çalıştırılır: db-vm/bootstrap-db.sh
-- Gerekli psql değişkenleri: db_name, owner_pw, reservation_pw, checkin_pw,
--                             flightops_pw, baggage_pw, monitor_pw, dynatrace_pw
\set ON_ERROR_STOP on

-- ---------- Roller (idempotent) ----------
SELECT format('CREATE ROLE %I LOGIN', r) FROM unnest(ARRAY[
  'lab_owner','svc_reservation','svc_checkin','svc_flightops','svc_baggage','lab_monitor','dynatrace']) r
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) \gexec

SELECT format('ALTER ROLE lab_owner       PASSWORD %L', :'owner_pw')       \gexec
SELECT format('ALTER ROLE svc_reservation PASSWORD %L', :'reservation_pw') \gexec
SELECT format('ALTER ROLE svc_checkin     PASSWORD %L', :'checkin_pw')     \gexec
SELECT format('ALTER ROLE svc_flightops   PASSWORD %L', :'flightops_pw')   \gexec
SELECT format('ALTER ROLE svc_baggage     PASSWORD %L', :'baggage_pw')     \gexec
SELECT format('ALTER ROLE lab_monitor     PASSWORD %L', :'monitor_pw')     \gexec
SELECT format('ALTER ROLE dynatrace       PASSWORD %L', :'dynatrace_pw')   \gexec

-- ---------- Servis rolleri: sunucu tarafı emniyet sınırları ----------
-- CONNECTION LIMIT 40 = ana Hikari pool (8) + lab-pressure pool (<=30) + pay.
-- Böylece "rol limiti" global max_connections'tan önce dolmaz; iki durum ayrışır.
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['svc_reservation','svc_checkin','svc_flightops','svc_baggage'] LOOP
    EXECUTE format('ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 40', r);
    EXECUTE format('ALTER ROLE %I SET statement_timeout = %L', r, '15s');
    EXECUTE format('ALTER ROLE %I SET lock_timeout = %L', r, '8s');
    EXECUTE format('ALTER ROLE %I SET idle_in_transaction_session_timeout = %L', r, '30s');
  END LOOP;
END $$;

ALTER ROLE lab_owner   NOSUPERUSER NOCREATEROLE CONNECTION LIMIT 5;
ALTER ROLE lab_owner   SET statement_timeout = 0;            -- büyük seed uzun sürebilir

-- İzleme rolleri: salt-okur istatistik + rezerve bağlantı slotu
ALTER ROLE lab_monitor NOSUPERUSER CONNECTION LIMIT 5;
ALTER ROLE lab_monitor SET statement_timeout = '5s';
ALTER ROLE lab_monitor SET default_transaction_read_only = on;
GRANT pg_monitor TO lab_monitor;
GRANT pg_use_reserved_connections TO lab_monitor;           -- PG16+: connection baskısında da ölçebilsin

ALTER ROLE dynatrace NOSUPERUSER INHERIT CONNECTION LIMIT 5;
GRANT pg_monitor TO dynatrace;                               -- Dynatrace PostgreSQL extension gereksinimi
GRANT pg_use_reserved_connections TO dynatrace;

-- ---------- Veritabanı ----------
SELECT format('CREATE DATABASE %I OWNER lab_owner ENCODING %L TEMPLATE template0', :'db_name', 'UTF8')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO lab_owner, svc_reservation, svc_checkin, svc_flightops, svc_baggage, lab_monitor, dynatrace', :'db_name') \gexec
SELECT format('GRANT CREATE ON DATABASE %I TO lab_owner', :'db_name') \gexec

\connect :db_name
CREATE EXTENSION IF NOT EXISTS pg_stat_statements SCHEMA public;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Dynatrace extension için resmî dokümandaki şema/arama yolu adımları.
-- Execution plan / index advisor yardımcı fonksiyonları opsiyoneldir; gerekiyorsa
-- Dynatrace dokümanındaki güncel SQL'den alınmalıdır (burada uydurulmamıştır).
CREATE SCHEMA IF NOT EXISTS dynatrace;
GRANT USAGE ON SCHEMA dynatrace TO dynatrace;
ALTER ROLE dynatrace SET search_path TO dynatrace, public;

-- Chaos v2: /proc host istatistikleri (lab_monitor) + temp_file_limit
\ir chaos-v2-superuser.sql
