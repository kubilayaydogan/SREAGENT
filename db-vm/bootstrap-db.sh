#!/usr/bin/env bash
# Rolleri, veritabanını ve pg_stat_statements'ı oluşturur (idempotent).
# DB_VM'de: sudo ./bootstrap-db.sh ./db-vm.env
# Yerel geliştirmede postgres container init betiği de aynı SQL'i kullanır.
set -euo pipefail
ENV_FILE="${1:-./db-vm.env}"
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$ENV_FILE"; set +a

PSQL="${PSQL:-/usr/pgsql-17/bin/psql}"
RUN_AS="${RUN_AS:-postgres}"

run_psql() {
  if [[ "$(id -un)" == "$RUN_AS" ]]; then "$PSQL" "$@"; else sudo -u "$RUN_AS" "$PSQL" "$@"; fi
}

run_psql -v ON_ERROR_STOP=1 -d postgres \
  -v db_name="$DB_NAME" \
  -v owner_pw="$OWNER_PW" -v reservation_pw="$RESERVATION_PW" -v checkin_pw="$CHECKIN_PW" \
  -v flightops_pw="$FLIGHTOPS_PW" -v baggage_pw="$BAGGAGE_PW" \
  -v monitor_pw="$MONITOR_PW" -v dynatrace_pw="$DYNATRACE_PW" \
  -f "$HERE/create-roles.sql"

echo "Roller ve '$DB_NAME' hazır. Migration/seed APP_VM'den db-migrator ile çalıştırılır (bkz. docs/lab/runbook.md)."
