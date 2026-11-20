#!/bin/bash
# Yerel PG17 container'ı ilk açılışta: DB_VM ile aynı rol/DB betiği.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -v db_name="$DB_NAME" \
  -v owner_pw="$OWNER_PW" -v reservation_pw="$RESERVATION_PW" -v checkin_pw="$CHECKIN_PW" \
  -v flightops_pw="$FLIGHTOPS_PW" -v baggage_pw="$BAGGAGE_PW" \
  -v monitor_pw="$MONITOR_PW" -v dynatrace_pw="$DYNATRACE_PW" \
  -f /airlab/create-roles.sql
