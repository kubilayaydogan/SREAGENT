#!/usr/bin/env bash
# DB_VM (Rocky Linux 9 veya 10, x86_64) — PostgreSQL 17 (PGDG) systemd kurulumu.
# EL sürümü /etc/os-release'ten okunur (Rocky 10.3 → EL-10 PGDG deposu).
# Kullanım:  sudo ./install-postgres17.sh ./db-vm.env
# İdempotenttir: tekrar çalıştırıldığında konfigürasyonu yeniler, veriyi silmez.
# SELinux ve firewalld KAPATILMAZ; yalnız APP_VM (ve varsa ActiveGate) IP'sine 5432 açılır.
set -euo pipefail

ENV_FILE="${1:-./db-vm.env}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ $EUID -eq 0 ]] || { echo "root olarak çalıştırın (sudo)"; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "env dosyası yok: $ENV_FILE"; exit 1; }
set -a; source "$ENV_FILE"; set +a

for v in APP_VM_IP DB_LISTEN_ADDR DB_PORT DB_NAME PG_MAX_CONNECTIONS PG_SHARED_BUFFERS PG_EFFECTIVE_CACHE_SIZE; do
  [[ -n "${!v:-}" ]] || { echo "eksik değişken: $v"; exit 1; }
done
[[ "$DB_LISTEN_ADDR" != "0.0.0.0" && "$DB_LISTEN_ADDR" != "*" ]] || { echo "DB_LISTEN_ADDR iç IP olmalı"; exit 1; }

PGVER=17
PGDATA="/var/lib/pgsql/${PGVER}/data"
PGBIN="/usr/pgsql-${PGVER}/bin"

echo "== 1/6 Saat senkronizasyonu (chrony)"
dnf install -y chrony
systemctl enable --now chronyd
chronyc tracking | head -3 || true

echo "== 2/6 PGDG deposu ve paketler"
EL_MAJOR="$(. /etc/os-release && echo "${VERSION_ID%%.*}")"
[[ "$EL_MAJOR" == "9" || "$EL_MAJOR" == "10" ]] || { echo "Desteklenmeyen EL sürümü: $EL_MAJOR"; exit 1; }
echo "   EL-${EL_MAJOR} ($(. /etc/os-release && echo "$PRETTY_NAME"))"
if ! rpm -q pgdg-redhat-repo >/dev/null 2>&1; then
  dnf install -y "https://download.postgresql.org/pub/repos/yum/reporpms/EL-${EL_MAJOR}-$(uname -m)/pgdg-redhat-repo-latest.noarch.rpm"
fi
# EL9: AppStream postgresql modülü PGDG paketlerini gölgeler → kapat. EL10'da modularity yok.
if [[ "$EL_MAJOR" == "9" ]]; then dnf -qy module disable postgresql || true; fi
dnf install -y "postgresql${PGVER}-server" "postgresql${PGVER}-contrib"

echo "== 3/6 initdb (yalnız ilk kurulumda)"
if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
  PGSETUP_INITDB_OPTIONS="--auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8" \
    "${PGBIN}/postgresql-${PGVER}-setup" initdb
fi

echo "== 4/6 Konfigürasyon"
install -d -o postgres -g postgres -m 0700 "$PGDATA/conf.d"
sed -e "s|@DB_LISTEN_ADDR@|${DB_LISTEN_ADDR}|" \
    -e "s|@DB_PORT@|${DB_PORT}|" \
    -e "s|@PG_MAX_CONNECTIONS@|${PG_MAX_CONNECTIONS}|" \
    -e "s|@PG_SHARED_BUFFERS@|${PG_SHARED_BUFFERS}|" \
    -e "s|@PG_EFFECTIVE_CACHE_SIZE@|${PG_EFFECTIVE_CACHE_SIZE}|" \
    "$HERE/postgresql.lab.conf" > "$PGDATA/conf.d/lab.conf"
chown postgres:postgres "$PGDATA/conf.d/lab.conf"; chmod 0600 "$PGDATA/conf.d/lab.conf"
grep -q "^include_dir = 'conf.d'" "$PGDATA/postgresql.conf" || echo "include_dir = 'conf.d'" >> "$PGDATA/postgresql.conf"

# pg_hba: yönetilen blok her çalıştırmada yeniden yazılır
HBA="$PGDATA/pg_hba.conf"
sed -i '/# BEGIN airlab/,/# END airlab/d' "$HBA"
{
  echo "# BEGIN airlab (install-postgres17.sh tarafından yönetilir)"
  echo "host  ${DB_NAME}  svc_reservation,svc_checkin,svc_flightops,svc_baggage,lab_owner,lab_monitor  ${APP_VM_IP}/32  scram-sha-256"
  echo "host  ${DB_NAME}  dynatrace  ${APP_VM_IP}/32  scram-sha-256"
  if [[ -n "${ACTIVEGATE_IP:-}" ]]; then
    echo "host  ${DB_NAME}  dynatrace  ${ACTIVEGATE_IP}/32  scram-sha-256"
    echo "host  postgres    dynatrace  ${ACTIVEGATE_IP}/32  scram-sha-256"
  fi
  echo "host  postgres    dynatrace  ${APP_VM_IP}/32  scram-sha-256"
  echo "# END airlab"
} >> "$HBA"

echo "== 5/6 SELinux / firewalld (kapatılmadan)"
if [[ "$DB_PORT" != "5432" ]] && command -v semanage >/dev/null; then
  semanage port -a -t postgresql_port_t -p tcp "$DB_PORT" 2>/dev/null || semanage port -m -t postgresql_port_t -p tcp "$DB_PORT"
fi
systemctl enable --now firewalld
ZONE="$(firewall-cmd --get-default-zone)"
add_rule() {
  local ip="$1"
  local rule="rule family=ipv4 source address=${ip}/32 port port=${DB_PORT} protocol=tcp accept"
  firewall-cmd --permanent --zone="$ZONE" --query-rich-rule="$rule" >/dev/null 2>&1 || \
    firewall-cmd --permanent --zone="$ZONE" --add-rich-rule="$rule"
}
add_rule "$APP_VM_IP"
[[ -n "${ACTIVEGATE_IP:-}" ]] && add_rule "$ACTIVEGATE_IP"
# Genel postgresql servisi açık OLMAMALI
firewall-cmd --permanent --zone="$ZONE" --remove-service=postgresql >/dev/null 2>&1 || true
firewall-cmd --reload

echo "== 6/6 systemd servisi"
systemctl enable "postgresql-${PGVER}"
systemctl restart "postgresql-${PGVER}"
systemctl --no-pager status "postgresql-${PGVER}" | head -5
sudo -u postgres "${PGBIN}/psql" -Atc "show shared_preload_libraries; show max_connections; show reserved_connections;"

echo "Kurulum tamam. Sıradaki adım: sudo ./bootstrap-db.sh $ENV_FILE"
