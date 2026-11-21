#!/usr/bin/env bash
# SRE Operations Platform gizli değerlerini üretir (rastgele; değerler ekrana yazılmaz).
#   scripts/gen-sre-platform-env.sh [çıktı=deploy/out/sre-platform.env]
# Çıktıyı APP_VM'de app-vm/sre-platform.env olarak kopyalayın (chmod 600). Var olan dosyanın üzerine YAZMAZ:
# PostgreSQL parolası ilk açılışta volume'a işlenir, sonradan değiştirmek ALTER ROLE gerektirir.
set -euo pipefail
OUT="${1:-deploy/out/sre-platform.env}"
if [ -e "$OUT" ]; then echo "zaten var: $OUT (dokunulmadı)"; exit 0; fi
rnd() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
umask 077
PG=$(rnd 32)
{
  echo "# SRE platform gizli değerleri — repoya GİRMEZ"
  echo "POSTGRES_PASSWORD=${PG}"
  echo "SRE_DB_PASSWORD=${PG}"
  echo "SRE_JWT_SECRET=$(rnd 64)"
  echo "SRE_ADMIN_USERNAME=admin"
  echo "SRE_ADMIN_PASSWORD=$(rnd 20)"
  echo "SRE_ENVIRONMENT=lab"
} > "$OUT"
chmod 600 "$OUT"
echo "yazıldı: $OUT (değerler gösterilmez; admin parolası SRE_ADMIN_PASSWORD satırında)"
