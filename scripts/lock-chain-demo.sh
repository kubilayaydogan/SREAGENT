#!/usr/bin/env bash
# Kaos paneli olmadan tek seferlik blocking zinciri gösterimi:
#  1) Reservation lab ucu sıcak uçuşun envanter satırını HOLD_MS tutar (gerçek rezervasyon iş yolu)
#  2) Aynı anda Check-in, aynı uçuştaki bir yolcuyu check-in eder (checked_in+1 → bekler)
#  3) pg_stat_activity + pg_blocking_pids ile zincir yazdırılır (lab_monitor yetkisi yeterli)
# Kullanım: LAB_TOKEN=... scripts/lock-chain-demo.sh
#   APP_VM: BASE_HOST=<app_vm_ip> PSQL="psql postgresql://lab_monitor@<db_vm>:5432/airlab -Atc" ...
set -euo pipefail
H="${BASE_HOST:-127.0.0.1}"; HOLD_MS="${HOLD_MS:-4000}"
PSQL="${PSQL:-docker exec -i airlab-postgres-local psql -U postgres -d airlab -Atc}"
PY="$(command -v python3 || command -v python)"
EXP="demo-$(date +%s)"
FID=$(curl -fs "http://$H:8081/api/flights/hot" | "$PY" -c "import sys,json;print(json.load(sys.stdin)[0]['flightId'])")
PNR=$(curl -fs "http://$H:8082/api/checkin/candidates?flightId=$FID&limit=1" | "$PY" -c "import sys,json;print(json.load(sys.stdin)[0]['pnr'])")
echo "sıcak uçuş=$FID check-in PNR=$PNR experiment_id=$EXP hold=${HOLD_MS}ms"
curl -s -o /dev/null -w "reservation hold → HTTP %{http_code} %{time_total}s\n" -X POST "http://$H:8081/lab/bookings/hold" \
  -H "X-Lab-Token: ${LAB_TOKEN:?LAB_TOKEN}" -H "X-Experiment-Id: $EXP" -H 'Content-Type: application/json' \
  -d "{\"flightId\":$FID,\"holdMs\":$HOLD_MS}" &
sleep 0.7
curl -s -o /dev/null -w "checkin        → HTTP %{http_code} %{time_total}s (bekleme dahil)\n" -X POST "http://$H:8082/api/checkin" \
  -H "X-Experiment-Id: $EXP" -H 'Content-Type: application/json' -d "{\"pnr\":\"$PNR\"}" &
sleep 1
echo "--- blocking zinciri (waiter ← blocker):"
$PSQL "SELECT w.application_name||'('||w.usename||') pid '||w.pid||' '||w.wait_event_type||'/'||w.wait_event
        ||'  ←  '||b.application_name||'('||b.usename||') pid '||b.pid||' xact_age='||round(extract(epoch from now()-b.xact_start)::numeric,2)||'s'
       FROM pg_stat_activity w CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) bp(pid)
       JOIN pg_stat_activity b ON b.pid=bp.pid WHERE w.datname='airlab'"
wait
