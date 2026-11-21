# Runbook

Bu ortamın gerçek değerleriyle sıralı plan: `docs/deploy-plan.md`. Yer tutucular: `APP_VM=192.0.2.10`, `DB_VM=192.0.2.20`, yönetim ağı `10.0.0.0/24`. Kendi değerlerinizle değiştirin.

## 1. DB_VM kurulumu (Rocky 9, root)
```bash
git clone <repo> airlab && cd airlab/db-vm
cp db-vm.env.example db-vm.env && chmod 600 db-vm.env && vi db-vm.env   # APP_VM_IP, DB_LISTEN_ADDR, parolalar
sudo ./install-postgres17.sh ./db-vm.env      # PGDG, initdb, conf.d/lab.conf, pg_hba, firewalld, systemd
sudo ./bootstrap-db.sh ./db-vm.env            # roller, airlab DB, pg_stat_statements, dynatrace şeması
sudo ./verify-db-vm.sh ./db-vm.env            # systemd active, container değil, firewall, SELinux Enforcing
```
Beklenen: `postgresql-17` active; `shared_preload_libraries=pg_stat_statements`; `reserved_connections=3`;
firewall'da yalnız APP_VM IP'si için 5432 rich rule; `getenforce` → Enforcing.

## 2. APP_VM kurulumu
```bash
sudo ./app-vm/install-docker.sh 10.0.0.0/24
cd app-vm && cp .env.example .env && chmod 600 .env && vi .env   # DB_HOST=DB_VM, aynı parolalar, LAB_TOKEN, CHAOS_PASSWORD, APP_BIND_IP
docker compose build                                           # ~3-5 dk (JDK21+Maven container'da)
docker compose --profile tools run --rm db-migrator migrate    # TEK migration runner
docker compose --profile tools run --rm db-migrator seed --scale=1
docker compose --profile tools run --rm db-migrator check      # tüm satırlar 0
docker compose up -d
docker compose --profile observability up -d pg-lock-collector
```
Kaynak limitleri `.env`: `APP_MEM_LIMIT=640m`, `APP_CPUS=1.0`, `CHAOS_MEM_LIMIT=512m`, `HIKARI_MAX_POOL=8`.

## 3. Doğrulama
```bash
BASE_HOST=192.0.2.10 PSQL="psql postgresql://lab_owner@192.0.2.20:5432/airlab -Atc" scripts/smoke.sh   # 31 kontrol
PSQL="psql postgresql://lab_owner@192.0.2.20:5432/airlab -Atc" scripts/verify-db.sh
BASE_HOST=192.0.2.10 LAB_TOKEN=... PSQL="psql postgresql://lab_monitor@192.0.2.20:5432/airlab -Atc" scripts/lock-chain-demo.sh
```
`lab_owner` parolası için `~/.pgpass` kullanın. smoke.sh DB doğrulaması iş tablolarını okuduğundan `lab_owner` gerekir;
blocking sorgusu için `lab_monitor` (pg_monitor) yeterlidir. Not: pg_hba yalnız APP_VM IP'sine izin verdiği için bu
komutları APP_VM'den çalıştırın.

Arayüzler: `http://APP_VM:8081` Reservation, `:8082` Check-in, `:8083` FlightOps, `:8084` Baggage,
`:8080` Chaos paneli (Basic auth), her serviste `/swagger-ui.html`, `/actuator/health`, `/actuator/prometheus`.

## 4. Deney çalıştırma
Panel → Hazır reçete → (isteğe göre parametre) → Deneyi başlat. Veya `docs/experiments.md` §Çalıştırma.
Aynı anda tek aktif deney. Deney kaydı: panel → Deney geçmişi, JSON `GET /api/experiments/{id}`.

## 5. Acil durdurma
1. Panel **STOP-ALL** veya `curl -u chaos:*** -X POST http://APP_VM:8080/api/experiments/stop-all`
   → yeni yük durur, uçuştaki istekler timeout'larıyla biter, her servise `/lab/cleanup` çağrılır.
2. Kontrol: `scripts/verify-db.sh` → `*-lab-pressure` oturumu kalmamalı;
   `select count(*) from pg_stat_activity where query like '%lab:%' and state='active'` → 0.
3. Gerekirse (DB_VM, postgres): `select pg_terminate_backend(pid) from pg_stat_activity where application_name like '%-lab-pressure';`
4. Son çare: `docker compose restart <servis>`. Sunucu tarafı sınırlar (statement_timeout 15 sn,
   idle_in_transaction 30 sn, client_connection_check_interval 2 sn) her durumda oturumları sonlandırır.

## 6. Veri sıfırlama (açık onay gerekir)
```bash
docker compose --profile tools run --rm -e LAB_ALLOW_RESET=true db-migrator reset --confirm=airlab
docker compose --profile tools run --rm db-migrator seed --scale=1             # veya I/O için --scale=10 --events-per-bag=100
```
`LAB_ALLOW_RESET` yoksa veya `--confirm` DB adıyla eşleşmezse reddedilir (yerelde doğrulandı).

## 7. Sorun giderme
| Belirti | Olası neden | Kontrol |
|---|---|---|
| health DOWN, `POOL_TIMEOUT`/`DB_UNAVAILABLE` | DB erişilemiyor, pg_hba/firewall | `docker logs airlab-<svc>`, DB_VM `firewall-cmd --list-rich-rules`, `pg_hba.conf` airlab bloğu |
| `/lab/**` 401/403/503 | token yok/kısa, IP allowlist dışında | `.env` `LAB_TOKEN` (≥16), `LAB_ALLOWED_CIDRS` (chaos 172.28.0.50) |
| chaos 503 "CHAOS_PASSWORD" | parola tanımsız (<8) | `.env` |
| deney "veri yok" (noData) yüksek | havuzlar boş (aday kalmadı) | `seed`, hot-bookings; check-in adayları tükenmiş olabilir → reset+seed |
| collector `up=0` | lab_monitor parolası, pg_hba | `docker logs airlab-pg-lock-collector` |
| `remaining connection slots are reserved` | E4 sırasında beklenen | stop-all |

## 8. Yerel geliştirme (Windows/Linux, yalnız Docker gerekir)
```bash
scripts/build.sh                     # Docker içinde mvn verify (birim + Testcontainers PG17 entegrasyon)
cd app-vm && cp .env.example .env.local   # DB_HOST=postgres, LOCAL_PG_SUPERUSER_PASSWORD ekleyin
docker compose -f docker-compose.yml -f compose.local.yml --env-file .env.local up -d --build
docker compose -f docker-compose.yml -f compose.local.yml --env-file .env.local --profile tools run --rm db-migrator migrate
```
`compose.local.yml` PostgreSQL'i container'da çalıştırır — **yalnız test içindir**; hedefte DB_VM systemd kullanılır.
