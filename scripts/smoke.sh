#!/usr/bin/env bash
# Uçtan uca işlevsellik testi: dört UYGULAMA ARAYÜZÜNDEN (form POST) işlem yapar,
# REST API ile ve doğrudan PostgreSQL'de kalıcı değişimi doğrular.
#
# Yerel:  scripts/smoke.sh
# APP_VM: BASE_HOST=192.0.2.10 PSQL="psql postgresql://lab_owner:***@192.0.2.20:5432/airlab -Atc" scripts/smoke.sh
#         (lab_owner parolasını komut satırına yazmak yerine ~/.pgpass kullanın)
set -uo pipefail

BASE_HOST="${BASE_HOST:-127.0.0.1}"
RES="http://$BASE_HOST:8081"; CHK="http://$BASE_HOST:8082"; OPS="http://$BASE_HOST:8083"; BAG="http://$BASE_HOST:8084"
PSQL="${PSQL:-docker exec -i airlab-postgres-local psql -U postgres -d airlab -Atc}"
PY="$(command -v python3 || command -v python)"
FAIL=0; PASS=0

ok()   { echo "  [OK]   $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
sql()  { $PSQL "$1" | tr -d '\r'; }
json() { "$PY" -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
eq()   { if [[ "$1" == "$2" ]]; then ok "$3 ($1)"; else bad "$3: beklenen '$2' gelen '$1'"; fi; }
# Form POST, yönlendirme hedefini döndürür (Thymeleaf UI)
form() { curl -s -o /dev/null -w "%{redirect_url}" -X POST "$1" "${@:2}" | sed "s/;jsessionid=[^?]*//"; }

echo "== 0. Sağlık"
for u in $RES $CHK $OPS $BAG; do
  s=$(curl -s "$u/actuator/health" | json "d['status']"); eq "$s" "UP" "$u health"
done

echo "== 1. Reservation UI: rezervasyon oluştur (idempotent form)"
FID=$(curl -s "$RES/api/flights?flexDays=20&limit=50" | json "[f['flightId'] for f in d if f['capacity']-f['sold']>5 and not f['hot']][0]")
SOLD0=$(sql "select sold from flightops.flight_inventory where flight_id=$FID")
IDEM="smoke-$(date +%s)-$RANDOM"
LOC=$(form "$RES/ui/bookings" -d flightId=$FID -d firstName=Smoke -d lastName=Test -d email=smoke@example.test -d fareClass=Y -d idemKey=$IDEM)
PNR="${LOC##*/}"
[[ "$LOC" == *"/ui/bookings/"* ]] && ok "UI yönlendirme → $LOC" || bad "UI rezervasyon yönlendirmesi yok: $LOC"
LOC2=$(form "$RES/ui/bookings" -d flightId=$FID -d firstName=Smoke -d lastName=Test -d email=smoke@example.test -d fareClass=Y -d idemKey=$IDEM)
eq "${LOC2##*/}" "$PNR" "aynı form tekrar gönderimi aynı PNR"
eq "$(curl -s "$RES/api/bookings/$PNR" | json "d['status']")" "CONFIRMED" "API booking durumu"
eq "$(sql "select status from reservation.booking where pnr='$PNR'")" "CONFIRMED" "DB booking durumu"
eq "$(sql "select count(*) from reservation.booking where idempotency_key='$IDEM'")" "1" "DB tek kayıt (idempotency)"
eq "$(sql "select sold from flightops.flight_inventory where flight_id=$FID")" "$((SOLD0+1))" "DB envanter sold+1"
curl -s "$RES/ui/bookings/$PNR" | grep -q "$PNR" && ok "UI rezervasyon sayfası PNR gösteriyor" || bad "UI sayfası"

echo "== 2. Check-in UI: check-in + koltuk"
CI0=$(sql "select checked_in from flightops.flight_inventory where flight_id=$FID")
form "$CHK/ui/checkin/$PNR" -d channel=WEB >/dev/null
form "$CHK/ui/checkin/$PNR" -d channel=WEB >/dev/null   # tekrar
eq "$(sql "select count(*) from checkin.checkin c join reservation.booking b using(booking_id) where b.pnr='$PNR'")" "1" "DB check-in tek kayıt"
eq "$(sql "select checked_in from flightops.flight_inventory where flight_id=$FID")" "$((CI0+1))" "DB envanter checked_in+1 (tekrar sayılmadı)"
SEAT=""
for s in 31A 32B 33C 34D 35E 36F 37A 38B; do
  form "$CHK/ui/checkin/$PNR/seat" -d seatNo=$s >/dev/null
  got=$(sql "select seat_no from checkin.seat_assignment s join reservation.booking b using(booking_id) where b.pnr='$PNR'")
  if [[ "$got" == "$s" ]]; then SEAT=$s; break; fi
done
[[ -n "$SEAT" ]] && ok "DB koltuk atandı ($SEAT)" || bad "koltuk atanamadı (uçak kapasitesi küçük olabilir)"
eq "$(curl -s "$CHK/api/checkin/$PNR" | json "d['seatNo']")" "$SEAT" "API koltuk"

echo "== 3. Reservation UI: check-in sonrası iptal reddedilmeli"
form "$RES/ui/bookings/$PNR/cancel" >/dev/null
eq "$(sql "select status from reservation.booking where pnr='$PNR'")" "CONFIRMED" "check-in yapılmış rezervasyon iptal edilmedi"

echo "== 4. Baggage UI: bagaj kaydı + olay"
BIDEM="smokebag-$(date +%s)-$RANDOM"
BLOC=$(form "$BAG/ui/bags" -d pnr=$PNR -d weightKg=21.5 -d idemKey=$BIDEM)
TAG="${BLOC##*/}"
[[ "$BLOC" == *"/ui/bags/"* ]] && ok "bagaj UI yönlendirme → $TAG" || bad "bagaj yönlendirme: $BLOC"
eq "$(sql "select status from baggage.bag where tag='$TAG'")" "CHECKED" "DB bagaj CHECKED"
form "$BAG/ui/bags/$TAG/events" -d eventType=LOADED -d location=RAMP >/dev/null
eq "$(sql "select status from baggage.bag where tag='$TAG'")" "LOADED" "DB bagaj LOADED"
eq "$(sql "select count(*) from baggage.bag_event e join baggage.bag g using(bag_id) where g.tag='$TAG'")" "2" "DB bagaj olay sayısı"
eq "$(curl -s "$BAG/api/bags/$TAG" | json "d['bag']['status']")" "LOADED" "API bagaj durumu"

echo "== 5. Reservation UI: ikinci rezervasyon + iptal"
LOC3=$(form "$RES/ui/bookings" -d flightId=$FID -d firstName=Cancel -d lastName=Me -d email=c@example.test -d fareClass=B -d idemKey="smoke-c-$RANDOM$RANDOM")
P2="${LOC3##*/}"
S1=$(sql "select sold from flightops.flight_inventory where flight_id=$FID")
form "$RES/ui/bookings/$P2/cancel" >/dev/null
eq "$(sql "select status from reservation.booking where pnr='$P2'")" "CANCELLED" "DB iptal"
eq "$(sql "select sold from flightops.flight_inventory where flight_id=$FID")" "$((S1-1))" "DB envanter sold-1"

echo "== 6. FlightOps UI: durum ve kapasite"
OFID=$(curl -s "$OPS/api/flights/sample?limit=1" | json "d[0]")
form "$OPS/ui/flights/$OFID/status" -d status=DELAYED -d gate=C7 -d delayMin=35 >/dev/null
eq "$(sql "select status||'/'||gate||'/'||delay_min from flightops.flight where flight_id=$OFID")" "DELAYED/C7/35" "DB uçuş durumu"
SOLD=$(sql "select sold from flightops.flight_inventory where flight_id=$FID")
CAP=$(sql "select capacity from flightops.flight_inventory where flight_id=$FID")
form "$OPS/ui/flights/$FID/capacity" -d capacity=$((SOLD-1)) >/dev/null
eq "$(sql "select capacity from flightops.flight_inventory where flight_id=$FID")" "$CAP" "satılanın altına kapasite reddedildi"
form "$OPS/ui/flights/$FID/capacity" -d capacity=$((CAP+5)) >/dev/null
eq "$(sql "select capacity from flightops.flight_inventory where flight_id=$FID")" "$((CAP+5))" "DB kapasite güncellendi"

echo "== 7. Veri bütünlüğü ve servis eşlemesi"
eq "$(sql "select coalesce(sum(violations),0) from lab.integrity_check")" "0" "lab.integrity_check ihlal yok"
MAP=$(sql "select string_agg(distinct usename||'='||application_name, ',' order by usename||'='||application_name) from pg_stat_activity where datname='airlab' and usename like 'svc\_%'")
for m in svc_baggage=baggage-service svc_checkin=checkin-service svc_flightops=flightops-service svc_reservation=reservation-service; do
  [[ "$MAP" == *"$m"* ]] && ok "DB oturumu $m" || bad "DB oturumu yok: $m ($MAP)"
done

echo
echo "SONUÇ: $PASS başarılı, $FAIL başarısız"
exit $FAIL
