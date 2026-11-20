#!/usr/bin/env bash
# Veri bütünlüğü + servis/rol/application_name eşlemesi + bağlantı kapasitesi (salt-okur).
# Yerel: scripts/verify-db.sh   |  VM: PSQL="psql 'host=<db_vm> dbname=airlab user=lab_owner' -Atc" scripts/verify-db.sh
set -uo pipefail
PSQL="${PSQL:-docker exec -i airlab-postgres-local psql -U postgres -d airlab -Atc}"
echo "--- lab.integrity_check (hepsi 0 olmalı)"; $PSQL "select check_name||' = '||violations from lab.integrity_check"
echo "--- rol | application_name | durum | adet"; $PSQL "select usename||' | '||application_name||' | '||coalesce(state,'')||' | '||count(*) from pg_stat_activity where backend_type='client backend' group by usename,application_name,state order by 1"
echo "--- kapasite"; $PSQL "select 'kullanılan='||(select count(*) from pg_stat_activity where backend_type='client backend')||' max='||current_setting('max_connections')||' superuser_reserved='||current_setting('superuser_reserved_connections')||' reserved='||current_setting('reserved_connections')"
echo "--- rol bazlı pg_stat_statements (kümülatif)"; $PSQL "select r.rolname||' calls='||sum(calls)||' exec_ms='||round(sum(total_exec_time)) from pg_stat_statements s join pg_roles r on r.oid=s.userid where r.rolname like 'svc%' group by r.rolname order by 1"
