# Kurulum planı — bu ortam (APP_VM 192.168.1.1 · DB_VM 192.168.1.2 · Rocky 10.3 · Dynatrace SaaS)

Bu belge `docs/runbook.md`'nin bu ortama özel, sıralı hâlidir. Her adımın **kabul** satırı
`docs/acceptance-gate.md` tablosuna işlenir. `<env>` = Dynatrace environment id (ör. `abc12345`;
`https://<env>.apps.dynatrace.com` adresindeki ilk parça).

## 0. Kararlar

| Konu | Karar | Gerekçe |
|---|---|---|
| ActiveGate yeri | **APP_VM (192.168.1.1)**, Environment ActiveGate, Linux host kurulumu | DB_VM, E5 (DB CPU) ve E6 (I/O) deneylerinin **ölçülen kaynağı**; ActiveGate JVM'i oraya konursa DB host CPU/RAM'i kirlenir ve "DB kaynaklı" bulgu bozulur. APP_VM'deki tüketim OneAgent'ta ayrı process group olarak görünür ve raporda ayrıca gösterilir. pg_hba/firewall zaten APP_VM IP'sine açık → ek kural gerekmez |
| ⚠ Destek durumu | Dynatrace ActiveGate destek tablosu Rocky Linux için **8.10, 9.7, 9.8, 10.1, 10.2** listeler; **10.3 listede yok** (doküman 2026-11). OneAgent tablosu "Rocky Linux 8, 9, 10" der (ana sürüm, alt sürümler dahil) → OneAgent 10.3'te destekli | ActiveGate 10.3'te kurulur ama **resmî destek dışı** sayılır. Kurulum reddedilir veya Extension Execution Controller sağlıksız olursa → **B planı**: ayrı küçük VM (Rocky **9.8** veya **10.2**, 2 vCPU / 4 GB / 20 GB), IP'si `db-vm.env` → `ACTIVEGATE_IP`, sonra DB_VM'de `install-postgres17.sh` yeniden çalıştırılır |
| OneAgent modu | APP_VM **full-stack**, DB_VM **infrastructure** | APP_VM'de Java code module (servis, JDBC, JVM) gerekli; DB_VM'de host metrikleri yeterli, PostgreSQL iç metrikleri extension + collector'dan gelir |
| Micrometer yolu | **A: SaaS Metrics API v2** | OneAgent yerel ingest ucunun container'dan erişimi doğrulanmadı; A yolu yalnız HTTPS çıkışı ister |
| PostgreSQL extension | `com.dynatrace.extension.postgres` (Hub: "PostgreSQL"), ActiveGate grubu `airlab`, endpoint `192.168.1.2:5432` | Resmî gereksinim: `pg_monitor`, `pg_stat_statements`, `dynatrace` şeması — `create-roles.sql`/`postgresql.lab.conf` bunları zaten yapıyor |
| MCP | Remote MCP, `https://<env>.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp`, `Authorization: Bearer <platform token>` | Resmî doküman (2026-11-09). Araç adları sabitlenmez, `tools/list` ile okunur |

> **IP notu:** 192.168.1.1 birçok ağda varsayılan ağ geçidinin adresidir. `vm-precheck.sh` bu çakışmayı kontrol eder.
> APP_VM gerçekten bu IP'deyse sorun yok.

> **Rocky 10 notu:** RHEL/Rocky 10 **x86-64-v3** CPU ister (AVX2/BMI2/FMA/MOVBE). Hipervizörde CPU tipi `host`
> (veya v3 uyumlu) değilse işletim sistemi açılsa bile bazı paketler çalışmaz; ön kontrol bunu yakalar.

### Kaynak bütçesi (APP_VM)

| Bileşen | RAM | CPU |
|---|---|---|
| 4 iş servisi | 4 × 640 MB (limit) | 4 × 1.0 (limit) |
| chaos-controller | 512 MB | 1.0 |
| pg-lock-collector | 128 MB | 0.25 |
| Environment ActiveGate + Extensions | 2 GB (4 GB önerilen) | 1 dual-core |
| OneAgent full-stack | ~0,5 GB | düşük |
| **Önerilen APP_VM** | **≥ 8 GB** | **≥ 4 vCPU**, /opt ≥ 12 GB, /var ≥ 15 GB |

DB_VM: ≥ 2 vCPU / 4 GB (`db-vm.env` varsayılanları: `shared_buffers=1GB`, `effective_cache_size=3GB`).

## 1. Dynatrace tarafında hazırlanacak token'lar

Hepsini kendi ortamınızda saklayın; sohbete/repoya yazmayın.

| # | Token | Nerede üretilir | Kapsam | Kullanan |
|---|---|---|---|---|
| T1 | OneAgent kurulum | *Deploy OneAgent → Linux* (UI token üretir) | UI varsayılanı (InstallerDownload) | iki VM, yalnız kurulum anı |
| T2 | ActiveGate kurulum | *Deploy ActiveGate → Linux → Environment ActiveGate* | UI varsayılanı | APP_VM, yalnız kurulum anı |
| T3 | Ingest (classic API token) | *Access tokens → Generate* | `metrics.ingest`, `logs.ingest` | `.env`: `DT_INGEST_TOKEN`, `DT_METRICS_TOKEN` (aynı token olabilir) |
| T4 | MCP (platform token) | *Account Management → Identity & access management → Platform tokens* | `mcp-gateway:servers:invoke`, `mcp-gateway:servers:read`, `storage:metrics:read`, `storage:logs:read`, `storage:entities:read`, `storage:events:read`, `storage:buckets:read`, `storage:smartscape:read`, `storage:spans:read` | `scripts/mcp-probe.py` (ve Aşama 2) |

T4: token yalnız sahibinin kullanıcı izinleri kadar okuyabilir; kullanıcının da aynı izinlere sahip olması gerekir.
`mcp-gateway:*` kapsamları eksikse MCP çağrısı **403** döner; `storage:logs:read` eksikse log sorguları boş gelir
(boş ≠ veri yok — probe bunları ayrı sınıflar).

Tenant ayarı: *Settings → Log Monitoring* açık olmalı (PostgreSQL extension top queries log olarak gelir;
container stdout logları OneAgent ile alınır — *Log ingest rules* altında `airlab-*` container'larının
dahil olduğunu kontrol edin).

## 2. Env dosyalarını üret (geliştirme makinesinde veya bir VM'de, bir kez)

```bash
# Git Bash / Linux; openssl gerekir
scripts/gen-vm-env.sh --app-ip 192.168.1.1 --db-ip 192.168.1.2 --dt-env <env>
scp deploy/out/db-vm.env  root@192.168.1.2:/opt/airlab/db-vm/db-vm.env
scp deploy/out/app-vm.env root@192.168.1.1:/opt/airlab/app-vm/.env
```
Parolalar rastgele ve iki dosyada aynıdır; `deploy/out/` git dışıdır. DB_VM RAM'i 4 GB değilse
`PG_SHARED_BUFFERS`/`PG_EFFECTIVE_CACHE_SIZE`'ı (RAM/4, RAM×3/4) düzeltin.

## 3. Repo'yu iki VM'e getir

```bash
sudo dnf install -y git && sudo git clone <repo-url> /opt/airlab     # veya: scp/rsync ile kopyalayın
cd /opt/airlab && mkdir -p deploy/evidence
./scripts/vm-precheck.sh db  <env>     # DB_VM'de
./scripts/vm-precheck.sh app <env>     # APP_VM'de
```
**Kabul:** iki VM'de de `SONUÇ: kuruluma geçilebilir`. Çıktıyı `deploy/evidence/precheck-{app,db}.txt` olarak saklayın.

## 4. DB_VM (192.168.1.2)

```bash
cd /opt/airlab/db-vm && chmod 600 db-vm.env
sudo ./install-postgres17.sh ./db-vm.env     # PGDG EL-10, initdb, conf.d, pg_hba (yalnız 192.168.1.1), firewalld, systemd
sudo ./bootstrap-db.sh ./db-vm.env           # roller, airlab DB, pg_stat_statements, dynatrace şeması
sudo ./verify-db-vm.sh ./db-vm.env | tee ../deploy/evidence/verify-db-vm.txt
```
**Kabul (kriter 1, DB tarafı):** `postgresql-17` active; `systemd-detect-virt --container` → none;
yalnız 192.168.1.1 için 5432 rich rule; SELinux Enforcing; `shared_preload_libraries=pg_stat_statements`.

OneAgent (infrastructure modu) — UI'daki T1 komutu, sonuna şu parametrelerle:
```bash
sudo /bin/sh Dynatrace-OneAgent-Linux-*.sh --set-monitoring-mode=infra-only --set-host-group=airlab
```

## 5. APP_VM (192.168.1.1)

```bash
cd /opt/airlab
sudo ./app-vm/install-docker.sh 192.168.1.0/24      # Docker CE (Rocky deposu), chrony, firewalld: 8080–8084 yalnız LAN'a
cd app-vm && chmod 600 .env
docker compose build                                # JDK21 + Maven container içinde, ~3–5 dk
docker compose --profile tools run --rm db-migrator migrate
docker compose --profile tools run --rm db-migrator seed --scale=1
docker compose --profile tools run --rm db-migrator check     # tüm satırlar 0
```

### 5.1 OneAgent (full-stack) — container'lar başlamadan ÖNCE
```bash
sudo /bin/sh Dynatrace-OneAgent-Linux-*.sh --set-monitoring-mode=fullstack --set-host-group=airlab
```
Ardından uygulamaları başlatın (OneAgent'tan sonra başlayan süreçler enjekte edilir):
```bash
docker compose up -d
docker compose --profile observability up -d pg-lock-collector
```

### 5.2 ActiveGate (Environment, APP_VM)
UI: *Deploy ActiveGate → Linux → Environment ActiveGate*, ActiveGate grubu **`airlab`**; T2 ile
indirme komutunu çalıştırın:
```bash
sudo /bin/bash Dynatrace-ActiveGate-Linux-x86-*.sh --set-group=airlab
systemctl list-units --type=service | grep -Ei 'dynatrace|extension'
```
Rocky 10.3 destek listesinde olmadığı için kurulum çıktısını `deploy/evidence/activegate-install.txt`
olarak saklayın. Kabul: UI'da *Deployment status → ActiveGates* altında `airlab` grubunda **Extensions**
modülü etkin ve bağlı. Değilse §0 B planına geçin.

### 5.3 PostgreSQL extension
UI → *Hub → PostgreSQL → Add to environment* → *Add monitoring configuration*:
- ActiveGate grubu: `airlab`
- Endpoint: host `192.168.1.2`, port `5432`, database `airlab`, kimlik doğrulama basic, kullanıcı `dynatrace`,
  parola = `db-vm.env` → `DYNATRACE_PW` (credential vault önerilir), SSL kapalı (lab iç ağ)
- Feature set: varsayılanlar + **Queries** + **Locks** + **Activity**
- Query interval 1 dk (varsayılan). Index Recommendations kapalı (hypopg kurulmadı)

İsteğe bağlı execution plan: `dynatrace.dynatrace_execution_plan` fonksiyonunun SQL'i resmî dokümanda
(*PostgreSQL extension → Prerequisites*); buraya kopyalanmadı. Gerekirse oradan alıp `airlab`
veritabanında `postgres` kullanıcısıyla çalıştırın.

**Kabul:** 5 dk içinde `postgres.numbackends` DQL'de döner; *Extensions → PostgreSQL* sayfasında
monitoring configuration durumu **OK**.

### 5.4 Uygulama metrikleri ve collector'ı Dynatrace'e aç
`.env`'de T3'ü girin:
```
COLLECTOR_DT_MODE=both
DT_INGEST_TOKEN=<T3>
DT_METRICS_EXPORT=true
DT_METRICS_TOKEN=<T3>
```
```bash
docker compose up -d --force-recreate
docker compose --profile observability up -d --force-recreate pg-lock-collector
docker logs --tail 20 airlab-pg-lock-collector    # 'HTTP 202' / hata yok
```

## 6. Uçtan uca doğrulama (APP_VM'den)

```bash
cd /opt/airlab
cat > ~/.pgpass <<EOF
192.168.1.2:5432:airlab:lab_owner:<OWNER_DB_PASSWORD>
192.168.1.2:5432:airlab:lab_monitor:<MONITOR_DB_PASSWORD>
EOF
chmod 600 ~/.pgpass; sudo dnf install -y postgresql   # yalnız psql istemcisi
BASE_HOST=192.168.1.1 PSQL="psql 'host=192.168.1.2 dbname=airlab user=lab_owner' -Atc" scripts/smoke.sh | tee deploy/evidence/smoke.txt
PSQL="psql 'host=192.168.1.2 dbname=airlab user=lab_owner' -Atc" scripts/verify-db.sh | tee deploy/evidence/verify-db.txt
set -a; . app-vm/.env; set +a
BASE_HOST=192.168.1.1 PSQL="psql 'host=192.168.1.2 dbname=airlab user=lab_monitor' -Atc" scripts/lock-chain-demo.sh | tee deploy/evidence/lock-chain.txt
```
**Kabul:** smoke 31/31 (kriter 2–4 VM'de), lock zinciri waiter=checkin-service / blocker=reservation-service (kriter 10).

## 7. Deneyler (E0 → E2 → E1×3)

```bash
export CHAOS_URL=http://192.168.1.1:8080 CHAOS_USER=chaos CHAOS_PASSWORD=<app-vm.env'deki>
python3 scripts/run-experiment.py E0-normal-baseline --baseline 300 --json-out deploy/evidence/E0.json
python3 scripts/run-experiment.py E2-hot-row-lock    --baseline 300 --load 300 --recovery 300 --json-out deploy/evidence/E2.json
for i in 1 2 3; do python3 scripts/run-experiment.py E1-independent-baggage-read --baseline 300 --load 300 --recovery 300 --json-out deploy/evidence/E1-$i.json; done
```
5 dakikalık pencereler: extension 1 dk örnekler (≥ 5 örnek/pencere), top queries 5 dk'da bir gelir.
Deney kayıtlarındaki UTC faz zamanlarını not edin.

## 8. Telemetri / MCP kapısı

```bash
export DT_PLATFORM_TOKEN=<T4>      # komut satırına yazmayın; read -s ile girin
python3 scripts/mcp-probe.py --env <env> list                 # araçlar + sunucu bilgisi
python3 scripts/mcp-probe.py --env <env> gate --since 2h      # kapı sorgularının tamamı (E2 dahil pencere)
```
Probe her sinyal için `VERI_VAR / BOS / HATA / IZIN_HATASI` yazar ve token'sız kanıt dosyasını
`deploy/evidence/mcp-*.json` altına kaydeder. `docs/acceptance-gate.md` "Telemetri / MCP kapısı" tablosunu
bu çıktı + UI kontrolü ile doldurun. Gate sorgularındaki yerleşik metrik anahtarları
(`dt.service.request.*`, `dt.host.*`, `dt.containers.*`) **DOĞRULANMADI**; `BOS`/`HATA` dönerse doğru
anahtar UI'daki *Metrics browser*'dan bulunup `GATE_QUERIES` güncellenir.

Bunlar bitince `deploy/evidence/` çıktılarını bana iletin; kabul kapısını ve `PROJECT_STATE.md`'yi
gerçek sonuçlarla güncelleyeyim. **Aşama 2'ye bu kapı dolmadan geçilmez.**
