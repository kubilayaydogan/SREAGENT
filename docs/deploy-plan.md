# Kurulum planı — bu ortam (APP_VM 172.20.229.26 · DB_VM 172.20.233.108 · Rocky 10.1 · Dynatrace SaaS)

Bu belge `docs/runbook.md`'nin bu ortama özel, sıralı hâlidir. Her adımın **kabul** satırı
`docs/acceptance-gate.md` tablosuna işlenir. Dynatrace environment id: `mfi74785`
(`https://mfi74785.apps.dynatrace.com`, ingest/kurulum API'leri `https://mfi74785.live.dynatrace.com`).

## 0. Kararlar

| Konu | Karar | Gerekçe |
|---|---|---|
| ActiveGate yeri | **APP_VM (172.20.229.26)**, Environment ActiveGate, Linux host kurulumu | DB_VM, E5 (DB CPU) ve E6 (I/O) deneylerinin **ölçülen kaynağı**; ActiveGate JVM'i oraya konursa DB host CPU/RAM'i kirlenir ve "DB kaynaklı" bulgu bozulur. APP_VM'deki tüketim OneAgent'ta ayrı process group olarak görünür ve raporda ayrıca gösterilir. pg_hba/firewall zaten APP_VM IP'sine açık → ek kural gerekmez |
| Destek durumu | Rocky **10.1**: ActiveGate destek tablosunda var (8.10, 9.7, 9.8, 10.1, 10.2); OneAgent "Rocky 8, 9, 10" → **ikisi de destekli** (doküman 2026-11). Kullanıcı kararı: 2026-11-21 | Kurulum yine de sağlıksız olursa yedek: ayrı küçük VM (2 vCPU / 4 GB), IP'si `db-vm.env` → `ACTIVEGATE_IP` |
| OneAgent modu | APP_VM **full-stack**, DB_VM **infrastructure** | APP_VM'de Java code module (servis, JDBC, JVM) gerekli; DB_VM'de host metrikleri yeterli, PostgreSQL iç metrikleri extension + collector'dan gelir |
| Micrometer yolu | **A: SaaS Metrics API v2** | OneAgent yerel ingest ucunun container'dan erişimi doğrulanmadı; A yolu yalnız HTTPS çıkışı ister |
| PostgreSQL extension | `com.dynatrace.extension.postgres` (Hub: "PostgreSQL"), ActiveGate grubu `airlab`, endpoint `172.20.233.108:5432` | Resmî gereksinim: `pg_monitor`, `pg_stat_statements`, `dynatrace` şeması — `create-roles.sql`/`postgresql.lab.conf` bunları zaten yapıyor |
| MCP | Remote MCP, `https://mfi74785.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp`, `Authorization: Bearer <platform token>` | Resmî doküman (2026-11-09). Araç adları sabitlenmez, `tools/list` ile okunur |

> **IP planı (güncel):** VM'ler bu Windows makinesinde Hyper-V **Default Switch** ağında (DHCP/NAT, 172.20.224.0/20).
> APP_VM `172.20.229.26` (`app-vm`), DB_VM `172.20.233.108` (`db-vm`). Default Switch IP'leri Windows yeniden başlayınca
> değişebilir → değişirse `db-vm.env`/`.env` IP'leri güncellenip `install-postgres17.sh` (pg_hba/firewall) yeniden çalıştırılır.

### ActiveGate'i yavaş bağlantıda kurma (doğrulandı 2026-11-21)

```bash
# 1) Bileşensiz kur (indirme beklemez, geri alma olmaz)
sudo env LANG=C LC_ALL=C /bin/bash Dynatrace-ActiveGate-Linux-x86.sh --set-group=airlab --no-modules
# 2) Bileşeni aç; ActiveGate arka planda indirir (kopma kurulumu bozmaz, ~15 dk)
printf '
[rpm]
rpm_enabled = true

[extension_controller]
extension_controller_enabled = true
'   | sudo tee -a /var/lib/dynatrace/gateway/config/custom.properties
sudo systemctl restart dynatracegateway
# 3) /var/lib/dynatrace/packages/modules.downloaded "RemotePluginModule, OK" olunca dosyayı HEMEN yedekle
#    (installer güncelleme kipinde klasörü temizleyip yeniden indirir) ve parametresiz çalıştır:
sudo sh -c 'install -d -m700 /root/ag-pkg && cp -p /var/lib/dynatrace/packages/Dynatrace-Remote-Plugin-Module-*.sh* /root/ag-pkg/'
sudo sh -c 'cd /root/ag-pkg && env LANG=C LC_ALL=C /bin/bash ./Dynatrace-Remote-Plugin-Module-Linux-x86-*.sh'
systemctl is-active dynatracegateway extensionsmodule
```
Yapmayın: aynı installer'ı güncelleme kipinde yeniden çalıştırmak — indirilmiş bileşeni silip yeniden indirir.

### Gerçek kurulumda karşılaşılanlar (2026-11-21) ve çözümleri

| Sorun | Çözüm |
|---|---|
| Hyper-V Dynamic Memory: APP_VM 4,8 GB görüyordu | Sabit bellek: APP_VM 8 GB, DB_VM 6 GB (`Set-VMMemory -DynamicMemoryEnabled $false`) |
| VM saatleri dakikalarca kayıyor (chrony yavaş kapatıyor) | `chrony.conf`: `makestep 1 -1` (kurulum betiklerine eklendi) |
| OneAgent/ActiveGate kurulumu: `platform not supported: <LiNUX>` | Türkçe yerel ayar (tr_TR) "i" dönüşümünü bozuyor → kurulumları `env LANG=C LC_ALL=C` ile çalıştırın |
| Yavaş/kopan internet (≈400–600 KB/sn): 280–330 MB kurulum dosyaları yarıda kesiliyor | Kurulum dosyalarını Windows'ta `curl --retry` ile indirip imzayı doğrulayın, `scp` ile VM'lere kopyalayın |
| ActiveGate: `RemotePluginModule` (Extensions Module, 354 MB) indirmesi kopuyor; kurulum 15 dk bekleyip **tamamen geri alıyor** (3 deneme) | Çözüm aşağıda: "ActiveGate'i yavaş bağlantıda kurma" |
| Lab uçları APP_VM'den 403 | Docker 29 host isteğinde kaynak IP'yi korur → `LAB_ALLOWED_CIDRS`'e APP_VM IP'si (/32) |
| `PSQL="psql 'host=… user=…'"` çalışmıyor | Bağlantı bilgisi boşluksuz URI olmalı: `psql postgresql://lab_owner@172.20.233.108:5432/airlab -Atc` |
| `verify-db.sh` servis oturumlarını göstermiyor | `lab_owner` başka rollerin oturumlarını göremez → `PSQL_MON` (lab_monitor) |
| `install-docker.sh` sözdizimi hatası | Düzeltildi (`${1:?...}` içindeki kesme işareti) |

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
| **APP_VM (karar: 8 GB / 4 vCPU)** | toplam limit ≈ 3,2 GB container + 2–4 GB ActiveGate + ~0,5 GB OneAgent → **sıkı**; `free -m` ile izleyin, gerekirse `APP_MEM_LIMIT=512m` | 4 vCPU; /opt ≥ 12 GB, /var ≥ 15 GB |

DB_VM: ≥ 2 vCPU / 4 GB (`db-vm.env` varsayılanları: `shared_buffers=1GB`, `effective_cache_size=3GB`).

## 1. Dynatrace tarafında hazırlanacak token'lar

Hepsini kendi ortamınızda saklayın; sohbete/repoya yazmayın.

| # | Token | Nerede üretilir | Kapsam | Kullanan |
|---|---|---|---|---|
| T1 | OneAgent kurulum | *Deploy OneAgent → Linux* (UI token üretir) | UI varsayılanı (InstallerDownload) | iki VM, yalnız kurulum anı |
| T2 | ActiveGate kurulum | *Deploy ActiveGate → Linux → Environment ActiveGate* | UI varsayılanı | APP_VM, yalnız kurulum anı |
| T3 | Ingest (platform token, `dt0s16.`) | *Platform tokens* | **`openpipeline:metrics:ingest`, `openpipeline:logs:ingest`** (tenant'ın 403 yanıtı bunları istiyor; okuma kapsamları ingest için işe yaramaz) | `.env`: `DT_INGEST_TOKEN`, `DT_METRICS_TOKEN` |
| T4 | MCP (platform token) | *Account Management → Identity & access management → Platform tokens* | `mcp-gateway:servers:invoke`, `mcp-gateway:servers:read`, `storage:buckets:read`, `storage:metrics:read`, `storage:logs:read`, `storage:spans:read`, `storage:events:read`, `storage:entities:read`, `storage:system:read`, **`storage:smartscape:read`** | `scripts/mcp-probe.py` (ve Aşama 2) |

### Tenant'ta doğrulananlar (2026-11-21, `mfi74785`)

| Kontrol | Sonuç |
|---|---|
| T1 OneAgent installer metainfo | ✅ 200 — `latestAgentVersion 1.349.123` |
| T2 ActiveGate installer metainfo | ✅ 200 — `latestGatewayVersion 1.349.93` |
| T3 ingest (yenilendi) | ✅ test metriği 202, test logu 200/204; MCP ile geri okundu |
| Log alanı `event.type` | ⚠ logs ingest bu alanı **siler** → collector `airlab.event` kullanıyor; kilit kenarı biçimindeki test logu tüm `waiter.*`/`blocker.*` alanlarıyla okundu |
| T4 Remote MCP initialize + tools/list | ✅ `dynatrace-mcp 1.0`, protokol 2025-06-18, 21 araç, DQL aracı `execute-dql(dqlQueryString)` |
| T4 kapı DQL'leri (yenilendi) | ✅ 13/13 izin hatasız; hepsi `BOS` (henüz kurulum yok) |
| Tenant modeli | **Smartscape on Grail**: `dt.entity.*` ve `entityName()` yok → `smartscapeNodes`, `dt.smartscape.*`, `getNodeName()` |

**Bucket izni:** platform token, sahibi kullanıcının IAM izinleriyle sınırlıdır. Token kapsamı doğru olsa da kullanıcı/grup
politikasında Grail okuma izni yoksa sorgu boş döner (`MISSING_BUCKET_PERMISSIONS`). Token sahibinin grubuna şu politikayı
(veya eşdeğeri yerleşik "okuma" politikalarını) atayın:
```
ALLOW storage:buckets:read;
ALLOW storage:metrics:read, storage:logs:read, storage:spans:read, storage:events:read,
      storage:entities:read, storage:system:read, storage:smartscape:read;
```
Kapsam/izin eklendikten sonra yeni token gerekir (mevcut token'ın kapsamı değiştirilemez):
`python3 scripts/mcp-probe.py --env mfi74785 gate --since 1h` → `IZIN_HATASI` kalmamalı (veri yokken `BOS` normal).

Tenant ayarı: *Settings → Log Monitoring* açık olmalı (PostgreSQL extension top queries log olarak gelir;
container stdout logları OneAgent ile alınır — *Log ingest rules* altında `airlab-*` container'larının
dahil olduğunu kontrol edin).

## 2. Env dosyalarını üret (geliştirme makinesinde veya bir VM'de, bir kez)

```bash
# Git Bash / Linux; openssl gerekir
scripts/gen-vm-env.sh --app-ip 172.20.229.26 --db-ip 172.20.233.108 --dt-env mfi74785
scp deploy/out/db-vm.env  root@172.20.233.108:/opt/airlab/db-vm/db-vm.env
scp deploy/out/app-vm.env root@172.20.229.26:/opt/airlab/app-vm/.env
scp deploy/out/db-vm-oneagent.sh root@172.20.233.108:/root/
scp deploy/out/app-vm-oneagent.sh deploy/out/app-vm-activegate.sh root@172.20.229.26:/root/
```
`deploy/out/*.sh` kurulum token'larını içerir (git dışı): installer'ı tenant'tan indirir, Dynatrace imzasını
`openssl cms -verify` ile doğrular, kurar, kendini silmeniz için uyarır. Kurulumdan sonra `shred -u /root/*-oneagent.sh /root/*-activegate.sh`.
Parolalar rastgele ve iki dosyada aynıdır; `deploy/out/` git dışıdır. DB_VM RAM'i 4 GB değilse
`PG_SHARED_BUFFERS`/`PG_EFFECTIVE_CACHE_SIZE`'ı (RAM/4, RAM×3/4) düzeltin.

## 3. Repo'yu iki VM'e getir

```bash
sudo dnf install -y git && sudo git clone <repo-url> /opt/airlab     # veya: scp/rsync ile kopyalayın
cd /opt/airlab && mkdir -p deploy/evidence
./scripts/vm-precheck.sh db  mfi74785     # DB_VM'de
./scripts/vm-precheck.sh app mfi74785     # APP_VM'de
```
**Kabul:** iki VM'de de `SONUÇ: kuruluma geçilebilir`. Çıktıyı `deploy/evidence/precheck-{app,db}.txt` olarak saklayın.

## 4. DB_VM (172.20.233.108)

```bash
cd /opt/airlab/db-vm && chmod 600 db-vm.env
sudo ./install-postgres17.sh ./db-vm.env     # PGDG EL-10, initdb, conf.d, pg_hba (yalnız 172.20.229.26), firewalld, systemd
sudo ./bootstrap-db.sh ./db-vm.env           # roller, airlab DB, pg_stat_statements, dynatrace şeması
sudo ./verify-db-vm.sh ./db-vm.env | tee ../deploy/evidence/verify-db-vm.txt
```
**Kabul (kriter 1, DB tarafı):** `postgresql-17` active; `systemd-detect-virt --container` → none;
yalnız 172.20.229.26 için 5432 rich rule; SELinux Enforcing; `shared_preload_libraries=pg_stat_statements`.

OneAgent (infrastructure modu, host grubu `airlab`):
```bash
sudo bash /root/db-vm-oneagent.sh      # log: /root/db-vm-oneagent.log
```

## 5. APP_VM (172.20.229.26)

```bash
cd /opt/airlab
sudo ./app-vm/install-docker.sh 172.20.224.0/20      # Docker CE (Rocky deposu), chrony, firewalld: 8080–8084 yalnız LAN'a
cd app-vm && chmod 600 .env
docker compose build                                # JDK21 + Maven container içinde, ~3–5 dk
docker compose --profile tools run --rm db-migrator migrate
docker compose --profile tools run --rm db-migrator seed --scale=1
docker compose --profile tools run --rm db-migrator check     # tüm satırlar 0
```

### 5.1 OneAgent (full-stack) — container'lar başlamadan ÖNCE
```bash
sudo bash /root/app-vm-oneagent.sh     # fullstack, host grubu airlab; log: /root/app-vm-oneagent.log
```
Ardından uygulamaları başlatın (OneAgent'tan sonra başlayan süreçler enjekte edilir):
```bash
docker compose up -d
docker compose --profile observability up -d pg-lock-collector
```

### 5.2 ActiveGate (Environment, APP_VM)
Environment ActiveGate, grup **`airlab`**:
```bash
sudo bash /root/app-vm-activegate.sh   # log: /root/app-vm-activegate.log
systemctl list-units --type=service | grep -Ei 'dynatrace|extension'
```
Log dosyalarını `deploy/evidence/` altına kopyalayın. Kabul: UI'da *Deployment status → ActiveGates* altında `airlab` grubunda **Extensions**
modülü etkin ve bağlı.

### 5.3 PostgreSQL extension
UI → *Hub → PostgreSQL → Add to environment* → *Add monitoring configuration*:
- ActiveGate grubu: `airlab`
- Endpoint: host `172.20.233.108`, port `5432`, database `airlab`, kimlik doğrulama basic, kullanıcı `dynatrace`,
  parola = `db-vm.env` → `DYNATRACE_PW` (credential vault önerilir), SSL kapalı (lab iç ağ)
- Feature set: varsayılanlar + **Queries** + **Locks** + **Activity**
- Query interval 1 dk (varsayılan). Index Recommendations kapalı (hypopg kurulmadı)

İsteğe bağlı execution plan: `dynatrace.dynatrace_execution_plan` fonksiyonunun SQL'i resmî dokümanda
(*PostgreSQL extension → Prerequisites*); buraya kopyalanmadı. Gerekirse oradan alıp `airlab`
veritabanında `postgres` kullanıcısıyla çalıştırın.

**Kabul:** 5 dk içinde `postgres.numbackends` DQL'de döner; *Extensions → PostgreSQL* sayfasında
monitoring configuration durumu **OK**.

### 5.4 Uygulama metrikleri ve collector'ı Dynatrace'e aç
`deploy/out/app-vm.env`'de zaten dolu (T3 doğrulandı): `COLLECTOR_DT_MODE=both`, `DT_METRICS_EXPORT=true`,
`DT_INGEST_TOKEN`/`DT_METRICS_TOKEN`. Bu nedenle §5.1'deki `docker compose up -d` ile gönderim başlar; aşağıdaki
komutlar yalnız `.env` değişirse gerekir:
```bash
docker compose up -d --force-recreate
docker compose --profile observability up -d --force-recreate pg-lock-collector
docker logs --tail 20 airlab-pg-lock-collector    # 'HTTP 202' / hata yok
```

## 6. Uçtan uca doğrulama (APP_VM'den)

```bash
cd /opt/airlab
cat > ~/.pgpass <<EOF
172.20.233.108:5432:airlab:lab_owner:<OWNER_DB_PASSWORD>
172.20.233.108:5432:airlab:lab_monitor:<MONITOR_DB_PASSWORD>
EOF
chmod 600 ~/.pgpass; sudo dnf install -y postgresql   # yalnız psql istemcisi
BASE_HOST=172.20.229.26 PSQL="psql postgresql://lab_owner@172.20.233.108:5432/airlab -Atc" scripts/smoke.sh | tee deploy/evidence/smoke.txt
PSQL="psql postgresql://lab_owner@172.20.233.108:5432/airlab -Atc" PSQL_MON="psql postgresql://lab_monitor@172.20.233.108:5432/airlab -Atc" scripts/verify-db.sh | tee deploy/evidence/verify-db.txt
set -a; . app-vm/.env; set +a
BASE_HOST=172.20.229.26 PSQL="psql postgresql://lab_monitor@172.20.233.108:5432/airlab -Atc" scripts/lock-chain-demo.sh | tee deploy/evidence/lock-chain.txt
```
**Kabul:** smoke 31/31 (kriter 2–4 VM'de), lock zinciri waiter=checkin-service / blocker=reservation-service (kriter 10).

## 7. Deneyler (E0 → E2 → E1×3)

```bash
export CHAOS_URL=http://172.20.229.26:8080 CHAOS_USER=chaos CHAOS_PASSWORD=<app-vm.env'deki>
python3 scripts/run-experiment.py E0-normal-baseline --baseline 300 --json-out deploy/evidence/E0.json
python3 scripts/run-experiment.py E2-hot-row-lock    --baseline 300 --load 300 --recovery 300 --json-out deploy/evidence/E2.json
for i in 1 2 3; do python3 scripts/run-experiment.py E1-independent-baggage-read --baseline 300 --load 300 --recovery 300 --json-out deploy/evidence/E1-$i.json; done
```
5 dakikalık pencereler: extension 1 dk örnekler (≥ 5 örnek/pencere), top queries 5 dk'da bir gelir.
Deney kayıtlarındaki UTC faz zamanlarını not edin.

## 8. Telemetri / MCP kapısı

```bash
export DT_PLATFORM_TOKEN=<T4>      # komut satırına yazmayın; read -s ile girin
python3 scripts/mcp-probe.py --env mfi74785 list                 # araçlar + sunucu bilgisi
python3 scripts/mcp-probe.py --env mfi74785 gate --since 2h      # kapı sorgularının tamamı (E2 dahil pencere)
```
Probe her sinyal için `VERI_VAR / BOS / HATA / IZIN_HATASI` yazar ve token'sız kanıt dosyasını
`deploy/evidence/mcp-*.json` altına kaydeder. `docs/acceptance-gate.md` "Telemetri / MCP kapısı" tablosunu
bu çıktı + UI kontrolü ile doldurun. Gate sorgularındaki yerleşik metrik anahtarları
(`dt.service.request.*`, `dt.host.*`, `dt.containers.*`) **DOĞRULANMADI**; `BOS`/`HATA` dönerse doğru
anahtar UI'daki *Metrics browser*'dan bulunup `GATE_QUERIES` güncellenir.

Bunlar bitince `deploy/evidence/` çıktılarını bana iletin; kabul kapısını ve `PROJECT_STATE.md`'yi
gerçek sonuçlarla güncelleyeyim. **Aşama 2'ye bu kapı dolmadan geçilmez.**
