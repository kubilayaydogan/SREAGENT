-- Chaos v2 için SUPERUSER (postgres) gerektiren, dar kapsamlı ayarlar. İdempotent.
-- Çalıştırma (DB_VM):  sudo -u postgres psql -d airlab -v ON_ERROR_STOP=1 -f db-vm/chaos-v2-superuser.sql
-- create-roles.sql de bu dosyayı sonunda \ir ile çağırır (yeni kurulumlar).
--
-- 1) lab_os.host_stats(): DB host'unun /proc/stat, /proc/meminfo, /proc/loadavg içeriğini döndürür.
--    Chaos controller adaptive workload control ve live dashboard için DB_VM CPU/RAM'i saniyeler mertebesinde
--    ölçer (Dynatrace verisi ~1-2 dk gecikmeli). Yalnız bu ÜÇ SABİT dosya okunur; parametre almaz.
--    SECURITY DEFINER sahibi superuser (postgres); EXECUTE yalnız lab_monitor'a (read-only rol) verilir.
-- 2) svc_* rollerine temp_file_limit: temp spill senaryosu disk doldurmasın (sunucu tarafı sert sınır).

CREATE SCHEMA IF NOT EXISTS lab_os AUTHORIZATION CURRENT_USER;   -- superuser (postgres)
REVOKE ALL ON SCHEMA lab_os FROM PUBLIC;

CREATE OR REPLACE FUNCTION lab_os.host_stats(OUT stat text, OUT meminfo text, OUT loadavg text)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT pg_read_file('/proc/stat'), pg_read_file('/proc/meminfo'), pg_read_file('/proc/loadavg')
$$;
REVOKE ALL ON FUNCTION lab_os.host_stats() FROM PUBLIC;
GRANT USAGE ON SCHEMA lab_os TO lab_monitor;
GRANT EXECUTE ON FUNCTION lab_os.host_stats() TO lab_monitor;

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['svc_reservation','svc_checkin','svc_flightops','svc_baggage'] LOOP
    EXECUTE format('ALTER ROLE %I SET temp_file_limit = %L', r, '2GB');
  END LOOP;
END $$;
