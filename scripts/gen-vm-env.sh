#!/usr/bin/env bash
# APP_VM ve DB_VM için birbiriyle TUTARLI env dosyalarını üretir (rastgele parolalar).
# Parolalar ekrana yazılmaz; çıktı dizini .gitignore'dadır (deploy/out/).
#
# Kullanım:
#   scripts/gen-vm-env.sh --app-ip 192.168.1.3 --db-ip 192.168.1.2 --dt-env <environment-id> [--out deploy/out] [--force]
#
# Üretilen:
#   deploy/out/app-vm.env   → APP_VM: airlab/app-vm/.env      (chmod 600)
#   deploy/out/db-vm.env    → DB_VM:  airlab/db-vm/db-vm.env  (chmod 600)
# Dynatrace token'ları (DT_INGEST_TOKEN, DT_METRICS_TOKEN) BOŞ bırakılır; APP_VM'de elle girilir.
set -euo pipefail

APP_IP="" DB_IP="" DT_ENV="" OUT="deploy/out" FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-ip) APP_IP="$2"; shift 2 ;;
    --db-ip)  DB_IP="$2";  shift 2 ;;
    --dt-env) DT_ENV="$2"; shift 2 ;;
    --out)    OUT="$2";    shift 2 ;;
    --force)  FORCE=1;     shift ;;
    *) echo "bilinmeyen argüman: $1"; exit 2 ;;
  esac
done
[[ -n "$APP_IP" && -n "$DB_IP" ]] || { echo "--app-ip ve --db-ip zorunlu"; exit 2; }
ipre='^([0-9]{1,3}\.){3}[0-9]{1,3}$'
[[ "$APP_IP" =~ $ipre && "$DB_IP" =~ $ipre ]] || { echo "IP biçimi geçersiz"; exit 2; }
[[ "$APP_IP" != "$DB_IP" ]] || { echo "APP_VM ve DB_VM IP'leri farklı olmalı"; exit 2; }
command -v openssl >/dev/null || { echo "openssl gerekli"; exit 1; }

mkdir -p "$OUT"
for f in "$OUT/app-vm.env" "$OUT/db-vm.env"; do
  [[ ! -e "$f" || $FORCE -eq 1 ]] || { echo "$f zaten var (parolaları korumak için durdu; yeniden üretmek için --force)"; exit 1; }
done

pw() { openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-32; }   # URL/env/psql güvenli 32 karakter
OWNER=$(pw) RES=$(pw) CHK=$(pw) OPS=$(pw) BAG=$(pw) MON=$(pw) DTPW=$(pw)
LAB_TOKEN=$(openssl rand -hex 24) CHAOS_PW=$(pw)

if [[ -n "$DT_ENV" ]]; then DT_LIVE="https://${DT_ENV}.live.dynatrace.com"; else DT_LIVE=""; fi

umask 077
cat > "$OUT/db-vm.env" <<EOF
# Üretildi: $(date -u +%FT%TZ) — scripts/gen-vm-env.sh. app-vm.env ile aynı parolalar.
APP_VM_IP=${APP_IP}
DB_LISTEN_ADDR=${DB_IP}
DB_PORT=5432
DB_NAME=airlab

# DB_VM RAM'ine göre ayarlayın: shared_buffers ≈ RAM/4, effective_cache_size ≈ RAM*3/4
PG_MAX_CONNECTIONS=100
PG_SHARED_BUFFERS=1GB
PG_EFFECTIVE_CACHE_SIZE=3GB

OWNER_PW=${OWNER}
RESERVATION_PW=${RES}
CHECKIN_PW=${CHK}
FLIGHTOPS_PW=${OPS}
BAGGAGE_PW=${BAG}
MONITOR_PW=${MON}
DYNATRACE_PW=${DTPW}

# ActiveGate APP_VM üzerinde → ayrı IP gerekmez (pg_hba/firewall APP_VM_IP'yi zaten içerir).
# ActiveGate ayrı bir VM'e taşınırsa IP'sini buraya yazıp install-postgres17.sh'i yeniden çalıştırın.
ACTIVEGATE_IP=
EOF

cat > "$OUT/app-vm.env" <<EOF
# Üretildi: $(date -u +%FT%TZ) — scripts/gen-vm-env.sh. db-vm.env ile aynı parolalar.
DB_HOST=${DB_IP}
DB_PORT=5432
DB_NAME=airlab
DB_HOST_LABEL=db-vm

RESERVATION_DB_PASSWORD=${RES}
CHECKIN_DB_PASSWORD=${CHK}
FLIGHTOPS_DB_PASSWORD=${OPS}
BAGGAGE_DB_PASSWORD=${BAG}
OWNER_DB_PASSWORD=${OWNER}
MONITOR_DB_PASSWORD=${MON}
DYNATRACE_DB_PASSWORD=${DTPW}

APP_BIND_IP=${APP_IP}
SPRING_PROFILES_ACTIVE=lab
LOG_FORMAT=ecs
HIKARI_MAX_POOL=8
HIKARI_MIN_IDLE=2
HIKARI_CONNECTION_TIMEOUT_MS=2000
TOMCAT_MAX_THREADS=50
APP_MEM_LIMIT=640m
APP_CPUS=1.0

LAB_TOKEN=${LAB_TOKEN}
# APP_VM IP'si: Docker host'tan gelen isteklerde kaynak IP'yi korur (Rocky 10 / Docker 29'da doğrulandı)
LAB_ALLOWED_CIDRS=172.28.0.0/24,127.0.0.1/32,${APP_IP}/32
LAB_MAX_HOLD_MS=5000
LAB_PRESSURE_POOL_MAX=22

CHAOS_USER=chaos
CHAOS_PASSWORD=${CHAOS_PW}
CHAOS_MEM_LIMIT=512m
CHAOS_CPUS=1.0

LAB_ALLOW_RESET=false

# ---- pg-lock-collector → Dynatrace SaaS (Metrics/Logs API v2) ----
# İlk açılış dryrun; ingest token girildikten sonra 'both' yapın.
COLLECTOR_DT_MODE=dryrun
COLLECTOR_INTERVAL_S=5
COLLECTOR_STATEMENTS_INTERVAL_S=60
DT_URL=${DT_LIVE}
# Classic API token, kapsamlar: metrics.ingest, logs.ingest
DT_INGEST_TOKEN=

# ---- Micrometer → Dynatrace (yol A: SaaS Metrics API v2) ----
DT_METRICS_EXPORT=false
DT_METRICS_URI=${DT_LIVE:+${DT_LIVE}/api/v2/metrics/ingest}
DT_METRICS_TOKEN=
DT_METRICS_STEP=30s

IMAGE_TAG=1.0.0
EOF
chmod 600 "$OUT/app-vm.env" "$OUT/db-vm.env" 2>/dev/null || true

echo "Üretildi:"
echo "  $OUT/db-vm.env  → DB_VM  (${DB_IP}):  airlab/db-vm/db-vm.env"
echo "  $OUT/app-vm.env → APP_VM (${APP_IP}): airlab/app-vm/.env"
echo "Kopyaladıktan sonra her iki VM'de: chmod 600 <dosya>. Parolalar yalnız bu iki dosyadadır."
