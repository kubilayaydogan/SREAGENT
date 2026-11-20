#!/usr/bin/env bash
# DB_VM kabul kontrolleri (salt-okur). sudo ./verify-db-vm.sh ./db-vm.env
set -uo pipefail
ENV_FILE="${1:-./db-vm.env}"
set -a; source "$ENV_FILE"; set +a
PSQL=/usr/pgsql-17/bin/psql
q() { sudo -u postgres "$PSQL" -d "$DB_NAME" -Atc "$1"; }

echo "--- systemd";            systemctl is-active postgresql-17
echo "--- container DEĞİL";    systemd-detect-virt --container || echo "(container değil)"
echo "--- dinleme";            ss -ltnp | grep ":${DB_PORT}" || echo "UYARI: port dinlenmiyor"
echo "--- firewalld";          firewall-cmd --list-rich-rules
echo "--- SELinux";            getenforce
echo "--- saat";               chronyc tracking | grep -E 'Leap|System time'
echo "--- ayarlar";            q "select name||'='||setting from pg_settings where name in ('max_connections','reserved_connections','superuser_reserved_connections','shared_preload_libraries','compute_query_id','track_io_timing','log_lock_waits')"
echo "--- extension";          q "select extname||' '||extversion from pg_extension"
echo "--- roller";             q "select rolname, rolconnlimit, coalesce(array_to_string(rolconfig,','),'') from pg_roles where rolname like 'svc_%' or rolname in ('lab_owner','lab_monitor','dynatrace') order by 1"
echo "--- servis bağlantıları (rol/application_name)"
q "select usename||' | '||coalesce(application_name,'')||' | '||state||' | '||count(*) from pg_stat_activity where datname=current_database() group by 1 order by 1" 2>/dev/null \
  || q "select usename, application_name, state, count(*) from pg_stat_activity where datname=current_database() group by 1,2,3 order by 1,2"
echo "--- veri tutarlılığı";   q "select check_name||'='||violations from lab.integrity_check" 2>/dev/null || echo "(migration henüz yok)"
