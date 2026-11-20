#!/usr/bin/env bash
# Kurulum öncesi VM kontrolü (salt-okur). Her iki VM'de çalıştırın:
#   ./scripts/vm-precheck.sh app <dynatrace-environment-id>     # APP_VM (Docker + 4 app + chaos + collector + ActiveGate)
#   ./scripts/vm-precheck.sh db  <dynatrace-environment-id>     # DB_VM  (PostgreSQL 17)
# Çıktı: her satır OK / UYARI / HATA. HATA varsa kuruluma başlamayın.
set -uo pipefail
ROLE="${1:?rol: app | db}"; DT_ENV="${2:-}"
fail=0
ok()   { printf 'OK     %s\n' "$*"; }
warn() { printf 'UYARI  %s\n' "$*"; }
bad()  { printf 'HATA   %s\n' "$*"; fail=1; }

. /etc/os-release
echo "== $(hostname) — $PRETTY_NAME — rol: $ROLE"
case "$ID:${VERSION_ID%%.*}" in
  rocky:9|rocky:10) ok "işletim sistemi $ID $VERSION_ID" ;;
  *) warn "beklenmeyen işletim sistemi $ID $VERSION_ID" ;;
esac
[[ "$(uname -m)" == "x86_64" ]] && ok "mimari x86_64" || bad "mimari $(uname -m) (x86_64 bekleniyor)"

# Rocky/RHEL 10 x86-64-v3 ister (AVX2, BMI2, FMA, MOVBE). Hipervizörde CPU tipi 'host' veya v3 uyumlu olmalı.
flags="$(grep -m1 '^flags' /proc/cpuinfo)"
miss=""; for f in avx2 bmi2 fma movbe; do grep -qw "$f" <<<"$flags" || miss+=" $f"; done
[[ -z "$miss" ]] && ok "x86-64-v3 CPU bayrakları" || { [[ "${VERSION_ID%%.*}" == "10" ]] && bad "x86-64-v3 eksik:$miss" || warn "x86-64-v3 eksik:$miss"; }

cpus=$(nproc); mem_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
root_free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
opt_free_gb=$(df -BG --output=avail /opt 2>/dev/null | tail -1 | tr -dc 0-9)
var_free_gb=$(df -BG --output=avail /var 2>/dev/null | tail -1 | tr -dc 0-9)
echo "   vCPU=$cpus RAM=${mem_mb}MB  boş: /=${root_free_gb}G /opt=${opt_free_gb}G /var=${var_free_gb}G"
if [[ "$ROLE" == "app" ]]; then
  # 4×640m app + 512m chaos + 128m collector + ActiveGate 2–4 GB + OneAgent + işletim sistemi
  (( mem_mb >= 7600 )) && ok "RAM ≥ 8 GB" || { (( mem_mb >= 5600 )) && warn "RAM < 8 GB: ActiveGate + 5 JVM için sıkışık" || bad "RAM < 6 GB: ActiveGate ile birlikte yetersiz"; }
  (( cpus >= 4 )) && ok "vCPU ≥ 4" || warn "vCPU < 4: yük üreticisi ve ActiveGate APP_VM ölçümlerini bozabilir"
  (( opt_free_gb >= 12 )) && ok "/opt ≥ 12 GB (ActiveGate + extensions + OneAgent)" || warn "/opt boş alan ${opt_free_gb}G (ActiveGate için ≥ 12 GB önerilir)"
  (( var_free_gb >= 15 )) && ok "/var ≥ 15 GB (Docker imajları + loglar)" || warn "/var boş alan ${var_free_gb}G"
else
  (( mem_mb >= 3800 )) && ok "RAM ≥ 4 GB" || warn "RAM < 4 GB: db-vm.env PG_SHARED_BUFFERS değerini düşürün"
  (( cpus >= 2 )) && ok "vCPU ≥ 2" || warn "vCPU < 2"
  (( var_free_gb >= 10 )) && ok "/var ≥ 10 GB (PGDATA)" || warn "/var boş alan ${var_free_gb}G (seed scale=2 ≈ 1 GB+)"
fi

getenforce 2>/dev/null | grep -q Enforcing && ok "SELinux Enforcing" || warn "SELinux Enforcing değil ($(getenforce 2>/dev/null))"
systemctl is-active -q firewalld && ok "firewalld aktif" || warn "firewalld aktif değil (kurulum betiği açar)"
if command -v chronyc >/dev/null && chronyc tracking >/dev/null 2>&1; then
  off=$(chronyc tracking | awk '/System time/{print $4}')
  awk -v o="$off" 'BEGIN{exit !(o<0.1)}' && ok "saat sapması ${off}s" || warn "saat sapması ${off}s (≥ 0.1 s)"
else warn "chrony yok/çalışmıyor (kurulum betiği kurar)"; fi

# Ağ: diğer VM ve Dynatrace SaaS'a HTTPS çıkışı
if [[ -n "$DT_ENV" ]]; then
  for h in "${DT_ENV}.live.dynatrace.com" "${DT_ENV}.apps.dynatrace.com"; do
    code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$h/" || echo 000)
    [[ "$code" != "000" ]] && ok "HTTPS çıkışı $h (HTTP $code)" || bad "HTTPS çıkışı yok: $h (proxy/firewall?)"
  done
else warn "Dynatrace environment id verilmedi; SaaS erişimi test edilmedi"; fi
for h in download.postgresql.org download.docker.com; do
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$h/" || echo 000)
  [[ "$code" != "000" ]] && ok "HTTPS çıkışı $h" || warn "HTTPS çıkışı yok: $h"
done

# 192.168.1.1 çoğu ağda varsayılan ağ geçididir; APP_VM bu IP'de ise çakışma yok mu?
gw=$(ip route show default 2>/dev/null | awk '{print $3; exit}')
myips=$(hostname -I 2>/dev/null)
[[ -n "$gw" ]] && grep -qw "$gw" <<<"$myips" && bad "bu host varsayılan ağ geçidi IP'sine ($gw) sahip — IP planını kontrol edin"
echo "   IP'ler: $myips  varsayılan ağ geçidi: ${gw:-yok}"

(( fail == 0 )) && echo "SONUÇ: kuruluma geçilebilir" || { echo "SONUÇ: HATA var, önce düzeltin"; exit 1; }
