# Chaos Controller v2: mimari, API ve çalıştırma

Chaos Controller v2, AIR lab ortamında **gerçek** ve **ölçülebilir** arıza senaryoları üretir. Her deney:

1. Seçilen uygulamalarda yük üretir (workload). Yük, uygulamanın kendi DB rolüyle çalışır.
2. Etkiyi bağımsız kaynaklardan ölçer: `actuator/prometheus`, `pg_stat_*` ve `/proc`.
3. Yükü kontrol hedefine göre ayarlar (adaptive control).
4. Güvenlik sınırları aşılırsa deneyi keser.
5. Cleanup sonrasında recovery'yi doğrular.
6. Beklenen ve gözlenen etkiyi rapor eder.

Backend tek doğruluk kaynağıdır; UI yalnızca gösterir. Ölçülemeyen değerler "—" olarak görünür, hiçbir değer uydurulmaz.

## 1. Mimari

```
                        ┌─────────────────────────── APP_VM (172.20.229.26) ───────────────────────────┐
  Tarayıcı ──Basic──▶   │  chaos-controller :8080                                                       │
  /ui/ (React SPA)      │   ├─ V2ApiController  /api/v2/*                                                │
  /classic (v1 panel)   │   ├─ Orchestrator  (validate → resolve(seed) → queue → dispatch, RunGate)     │
                        │   │    └─ ExperimentRunner (state machine, 1 aktif)                           │
                        │   │         ├─ Actuators: Loop ─HTTP▶ /lab/chaos/* (uygulama içinde workload) │
                        │   │         │             Netem ─HTTP▶ netem-<app>:9190 (tc qdisc, TTL)       │
                        │   │         │             Traffic (kullanıcı simülasyonu, X-Chaos-* başlıklar)│
                        │   │         ├─ AdaptiveController (P-kontrol, hedef metrik)                   │
                        │   │         ├─ SafetyGuard (budget, disk, DB conn, erişilemezlik, timeout)    │
                        │   │         └─ ImpactAnalyzer + ReportWriter                                  │
                        │   ├─ MetricsCollector (2 s): actuator/prometheus ◀── 4 uygulama               │
                        │   │                          /proc (APP_VM), disk                             │
                        │   └─ V2Store (/data/v2/*.json, audit.jsonl)                                   │
                        │                                                                               │
                        │  reservation:8081  checkin:8082  flightops:8083  baggage:8084                 │
                        │   └─ ChaosLabController /lab/chaos/* → ChaosWorkloads (uygulamanın svc_* rolü)│
                        │  netem-<app> (network_mode: service:<app>, cap_add NET_ADMIN, read_only)      │
                        └───────────────────────────────┬───────────────────────────────────────────────┘
                                                        │ JDBC (svc_* rolleri, lab_monitor read-only)
                        ┌──────────── DB_VM (172.20.233.108) ─────────────┐
                        │ PostgreSQL 17 (max_connections 100)              │
                        │  schema chaos.* (synthetic_data, shared_records, │
                        │   deadlock_pairs, write_sink)                    │
                        │  lab_os.host_stats()  SECURITY DEFINER → /proc   │
                        │  pg_stat_activity/locks/database/io/statements   │
                        └──────────────────────────────────────────────────┘
```

**Rol ayrımı.** Her deneyde üç rol ayrı tutulur:

- **Initiator:** yükü başlatan uygulama.
- **Direct target:** chaos'un doğrudan uygulandığı kaynak.
- **Observed affected:** ölçümle etkilendiği görülen uygulamalar.

Initiator ve direct target plandan gelir. Observed affected ise ölçümden çıkar: p95, error rate veya pool pending değerinin baseline'a göre anlamlı artmasıyla belirlenir.

**Dynatrace korelasyonu.**

- Chaos tarafından gönderilen tüm istekler şu başlıkları taşır: `X-Chaos-Experiment-Id`, `X-Chaos-Scenario`, `X-Chaos-Initiator`, `X-Chaos-Target`, `X-Chaos-Intensity` ve `X-Chaos-Phase`.
- Uygulamalar bu başlıkları MDC'ye `chaos.*` alanları olarak yazar ve log'larda görünürler.
- Deney raporu hazır DQL sorgularını içerir.
- SRE Agent'ın redaction katmanı `chaos.*` alanlarını siler. Böylece agent'a "cevap" verilmez.

## 2. State machine

```
CREATED → VALIDATING → BASELINING → RAMPING_UP → RUNNING → RECOVERING → COMPLETED
     \________\____________\___________\___________\__________\→ FAILED | ABORTED | TIMED_OUT
```

| Faz | Ne olur |
|---|---|
| VALIDATING | Kontroller: ortam lab mı (`CHAOS_V2_ENVIRONMENT`), allowlist, uygulamalar UP mı, DB ölçülebiliyor mu, APP_VM disk boş alanı, prerequisite'ler (chaos şeması, netem agent erişimi). |
| BASELINING | Yalnız arka plan trafiği çalışır; baseline metrikleri toplanır. |
| RAMPING_UP | Workload ve netem seviyesi doğrusal olarak hedefe yükselir. |
| RUNNING | Adaptive controller hedef metriği izler; SafetyGuard her örnekte budget kontrolü yapar. |
| RECOVERING | Cleanup çalışır: `/lab/chaos/cleanup`, `netem clear`, trafik durur. Ardından recovery doğrulanır: üst üste 3 örnekte p95 ≤ baseline×2.5+40 ms, error ≤ %2, pool pending = 0, lock waiters = 0, DB active ≤ baseline+3 ve DB CPU koşulu sağlanmalıdır. |
| Terminal | Rapor yazılır; deney JSON dosyası kalıcı hâle gelir. |

Diğer davranışlar:

- **Stop:** Deney RECOVERING fazına geçer; cleanup her zaman çalışır.
- **Emergency stop:** Kuyruk boşaltılır, aktif deney ABORTED olur ve tüm uygulamalarda cleanup çalışır.
- **`maxDurationSec` aşımı:** Deney TIMED_OUT olur.
- **Crash recovery:** Controller yeniden başlarken terminal olmayan deneyler ABORTED olarak işaretlenir ve cleanup çalışır.
- **RunGate:** Klasik (v1) ve v2 motorları aynı anda tek deney çalıştırabilir.

## 3. Senaryo şeması

Her senaryo (`ScenarioDef`) şu alanları içerir:

```
id, name, category, description
prerequisites[]           doğrulamada kontrol edilir
initiatorRole, directTarget
minApps, defaultAllTargets
expectedImpact[]          insan-okur açıklama
abortConditions[]         SafetyGuard'a ek koşullar
cleanup                   açıklama (cleanup her zaman genel /lab/chaos/cleanup + netem clear)
plan(ctx) → Plan {
  actions[]   LOOP (path, params, minWorkers..maxWorkers, controlled) | NETEM (target, delay, jitter, loss, TTL) | TRAFFIC
  control     {metric, target, gain} | null   (adaptive)
  criteria[]  {metric, scope, agg, expected(atLeast/rise/stays), required}
  parameters  kalibre edilmiş değerler (rapor için)
  expectedAffected[]
}
```

Yoğunluk seviyeleri şunlardır: LIGHT, MEDIUM, HIGH, CRITICAL ve CUSTOM.

- Değerler sabit kodlanmaz, ortamdan kalibre edilir: pool boyutu, `max_connections`, role CONNECTION LIMIT, DB vCPU sayısı, heap max ve `lab.chaos.maxConcurrentOps`.
- CUSTOM aşağıdaki alanları kabul eder: workers, rps, dbConnections, txDurationMs, queryFrequency, targetValue, errorPct, networkLatencyMs, packetLossPct, maxAffectedServices ve budget (DB CPU, DB conn, app CPU, heap, DB mem).

| ID | Senaryo | Kategori | Direct target |
|---|---|---|---|
| S01 | Connection pool exhaustion | connection | initiator'ın Hikari pool'u |
| S02 | Lock contention | lock | paylaşılan satırlar (`chaos.shared_records`) |
| S03 | Long-running transactions | lock | `flightops.flight_inventory` sıcak satırları (NO-OP UPDATE) |
| S04 | CPU-intensive queries | db-cpu | DB_VM CPU |
| S05 | High-frequency writes | db-io | WAL / disk write (`chaos.write_sink`) |
| S06 | Heavy read workloads | db-io | buffer cache / disk read (`chaos.synthetic_data`) |
| S07 | Temporary file pressure | db-io | temp dosyaları (`work_mem` küçük, `temp_file_limit` 2 GB) |
| S08 | Transaction contention (multi-app) | lock | paylaşılan satır kümesi |
| S09 | Deadlock | lock | `chaos.deadlock_pairs` |
| S10 | Connection storm | connection | `max_connections` |
| S11 | Application CPU saturation | app-resource | container CPU (duty-cycle) |
| S12 | Application memory pressure | app-resource | JVM heap (sınırlı ballast) |
| S13 | Database network latency | network | app → DB yolu (netem delay) |
| S14 | Database packet loss | network | app → DB yolu (netem loss) |
| S15 | App-to-app dependency latency | network | app → peer HTTP (netem) |
| S16 | Retry storm | amplification | yavaşlatılan peer |
| S17 | Cascading database saturation | cascade | DB CPU + active sessions → tüm uygulamalar |
| S18 | Multi-application concurrent load | load | tüm uygulamalar + DB |
| S19 | Slow database + retry amplification | amplification | netem + istemci retry politikası |
| S20 | Randomized cascading failure | cascade | seed ile seçilen alt senaryoların bileşimi |

## 4. REST API (`/api/v2`, HTTP Basic)

| Method | Yol | Açıklama |
|---|---|---|
| GET | `/overview` | Uygulama kartları, DB, APP_VM, aktif deney, kuyruk, kalibrasyon |
| GET | `/metrics/history?max=N` | Son N örnek |
| GET | `/scenarios`, `/scenarios/{id}` | Senaryo kütüphanesi |
| GET | `/intensity-presets?initiator=` | Senaryo × yoğunluk için kalibre parametreler |
| POST | `/experiments/preview` | Dry-run: hedefler, eylemler, kontrol, criteria (deney başlamaz) |
| POST | `/experiments` | Kuyruğa al (`clientRequestId` ile idempotent); doğrulama hatasında 400 `{errors:[]}` |
| GET | `/experiments` | Geçmiş + kuyruk + aktif deney |
| GET | `/experiments/{id}?samples=bool` | Tam kayıt |
| GET | `/experiments/{id}/live?since=tMs` | Artımlı örnekler, durum, log, rapor |
| GET | `/experiments/{id}/report.md` | Markdown rapor |
| POST | `/experiments/{id}/stop` | Kontrollü durdurma (cleanup + recovery) |
| POST | `/experiments/{id}/rerun` | Aynı istek ve seed ile yeni deney |
| POST | `/emergency-stop` | Kuyruğu boşalt, aktif deneyi ABORTED yap, her yerde cleanup |
| POST | `/random-plans` | `{seed, allowed/excludedScenarios, allowed/excludedApps, maxIntensity, count, parallel, cooldownSec}` |
| GET | `/audit` | Denetim kaydı |
| GET | `/status` | `{state, busy, active, queued}` |

Örnek istek:

```json
POST /api/v2/experiments
{"scenarioId":"S17","initiator":"baggage","targetMode":"AUTO","targets":[],"intensity":"HIGH",
 "durations":{"baselineSec":180,"rampSec":30,"durationSec":120,"recoverySec":90,"maxDurationSec":0},
 "traffic":{"enabled":true,"rpsPerApp":6,"users":8},"seed":42,"clientRequestId":"ui-abc"}
```

`targetMode` şu değerleri alabilir: SINGLE, MULTI, ALL, SHARED_DB, RANDOM ve AUTO (senaryonun doğal hedefleri).

Uygulama tarafındaki uçlar `/lab/chaos/*`. Bu uçlar yalnızca `LAB_TOKEN` (≥16 karakter) ile çağrılabilir. Uçlar şunlardır: `contend`, `long-transaction`, `cpu-query`, `heavy-read`, `temp-spill`, `write-burst`, `deadlock`, `connection-storm`, `cpu-burn`, `memory`, `dependency-call`, `cleanup` ve `status`.

## 5. Güvenlik

- **Ortam kilidi:** `CHAOS_V2_ENVIRONMENT` değeri `lab` değilse hiçbir deney başlamaz. Hedefler yalnız compose içindeki 4 uygulamadan oluşan allowlist'ten seçilebilir.
- **Ağ kapsamı:** netem yalnız lab CIDR'larındaki hedeflere uygulanır. Her kural için TTL ≤ 900 s'dir; süre dolunca kural kendiliğinden silinir. Ayrıca token gerekir.
- **Workload limitleri:**
  - `maxConcurrentOps` 32, `maxLongTxMs` 60 s;
  - CPU thread sayısı ≤ `LAB_CHAOS_MAX_CPU_THREADS`, heap ballast ≤ max heap'in `LAB_CHAOS_MAX_MEMORY_FRACTION` oranı;
  - `svc_*` rolleri için CONNECTION LIMIT 40 ve `temp_file_limit` 2 GB.
- **Budget:**
  - Varsayılan sınırlar: DB CPU %92, DB conn %90, app CPU %100, heap %92, DB mem %95.
  - Ek koşullar: APP_VM'de en az `min-app-vm-disk-free-gb` boş alan; DB boyut artışı en fazla `max-db-growth-mb`.
  - Bir sınır üst üste aşılırsa deney ABORTED olur ve cleanup çalışır.
- **Restart tespiti:** `process_start_time_seconds` deney sırasında değişirse deney ilk örnekte ABORTED olur ve gerekçeye "olası OOM / crash" yazılır.
- **Heap budget'ı GC sonrası live set ile değerlendirilir** (`jvm_gc_live_data_size_bytes`). "used" değeri toplanmamış garbage'ı da içerdiği için G1'de doğal olarak max'a yaklaşır. Bu yüzden UI'da baskı göstergesi olarak gösterilir, safety kararlarında kullanılmaz.
- **Yıkıcı işlem yok:**
  - İş verisinde yalnız NO-OP UPDATE yapılır (`updated_at=updated_at`).
  - Yazma, silme ve sıkıştırma yalnız `chaos.*` şemasında yapılır.
  - DDL yoktur; process kill yoktur.
- **Least privilege:**
  - Controller DB'yi yalnız `lab_monitor` rolüyle okur.
  - `lab_os.host_stats()` SECURITY DEFINER'dır ve yalnız `/proc/stat`, `/proc/meminfo` ve `/proc/loadavg` dosyalarını okur.
  - netem sidecar'ları `cap_drop ALL` + `NET_ADMIN` ile ve read-only filesystem üzerinde çalışır.
- **Audit:** Her submit, stop, emergency-stop, rerun ve random-plan işlemi `audit.jsonl` dosyasına aktör bilgisiyle yazılır.

## 6. Dağıtım ve çalıştırma

```bash
# DB_VM (bir kez, superuser)
sudo -u postgres psql -d airlab -f db-vm/chaos-v2-superuser.sql
# APP_VM
cd /opt/airlab/app-vm
docker compose --profile tools run --rm db-migrator migrate          # V6 chaos şeması
docker compose --profile tools run --rm db-migrator chaos-seed --rows=400000
docker compose build && docker compose up -d                         # uygulamalar, chaos, netem-<app>
```

- **Arayüz:** `http://172.20.229.26:8080/ui/` adresinden açılır. Kimlik bilgileri `app-vm/.env` dosyasındaki `CHAOS_USER` ve `CHAOS_PASSWORD` değerleridir.
- **Ekranlar:** Overview → Scenario Library → Experiment Builder (Önizle, sonra Başlat) → Live Experiment → History (tekrar çalıştırma).
- **Klasik panel:** `/classic` adresinden erişilir.

Uçtan uca doğrulama (APP_VM'de):

```bash
python3 scripts/chaos-e2e.py --quick                  # 20 senaryo, kanıt: deploy/evidence/chaos-v2/
python3 scripts/chaos-e2e.py --acceptance             # S17 HIGH cascading acceptance
```

Testler:

- `mvn test -pl chaos-controller` (V2UnitTest, EngineTest);
- `mvn verify -pl flightops-service -Dit.test=ChaosLabIT`;
- `pytest observability/netem-agent`;
- `chaos-controller/ui` içinde `npx vitest run`.

## 7. Bilinen sınırlamalar

- **DB_VM disk boş alanı doğrudan ölçülmez.** Koruma `temp_file_limit` (2 GB/rol), DB boyut artış limiti ve `chaos.write_sink` trim ile sağlanır.
- **Tek uygulamalı connection storm (S10) role limitine takılır.** Bu limit 40 bağlantıdır. `max_connections` doygunluğu için senaryonun ALL/MULTI modunda çalıştırılması gerekir.
- **Network senaryoları (S13–S16, S19) netem sidecar'a bağlıdır.** Etki container ağ arayüzünde oluşur; DB_VM tarafında ağ kesintisi simüle edilmez.
  Sidecar uygulamanın network namespace'ini paylaşır (`network_mode: service:<app>`). Uygulama container'ı yeniden oluşturulur veya yeniden başlatılırsa sidecar eski namespace'te kalır ve deney ön kontrolde "netem sidecar erişilemiyor" hatasıyla reddedilir. Çözüm: `docker compose up -d --force-recreate --no-deps netem-<app>`.
- **Dynatrace UI bağlantılarında query parametre biçimi garanti değildir.** Bu yüzden raporda kopyalanabilir DQL de verilir.
- **Lock senaryolarının (S02, S08, S09) etkisi yalnız DB ve workload seviyesinde görünür.** Bu senaryolar yalnız `chaos.*` satırlarında çalışır, iş endpoint'leri etkilenmez. İş trafiğini etkileyen lock senaryosu S03'tür (sıcak uçuş satırları).
- **S18 (multi-app load) iş endpoint'lerine gerçek trafik gönderir.** Bu endpoint'ler hafif (catalog sorguları) olduğu için DB CPU genelde birkaç yüzdeyi aşmaz. Zorunlu ölçütler RPS ve TPS artışıdır; DB CPU yalnızca bilgi amaçlı gösterilir. DB CPU doygunluğu için S04 veya S17 kullanılmalıdır.
- **Birleşik planlarda (S20, paralel random) alt senaryolar bir seviye düşük yoğunlukla çalışır.** Aksi halde toplam yük budget sınırını aşar.
- **Chaos CPU ve okuma sorguları (S04, S06, S17) parallel query kapalı çalışır** (`SET LOCAL max_parallel_workers_per_gather = 0`, yalnız o transaction'da). Böylece 1 worker yaklaşık 1 vCPU'ya karşılık gelir ve adaptive controller hedefe ince adımlarla yaklaşabilir. Parallel query açıkken 1–2 worker DB CPU'yu %96'ya çıkarıp budget abort'una yol açtı. DB'nin global ayarları değiştirilmez.
- **S12 heap ballast open-loop çalışır** (hız × 8 MB × 20 s). Ballast en fazla max heap'in %50'sidir (`LAB_CHAOS_MAX_MEMORY_FRACTION`) ve 256 KB'lık parçalar hâlinde ayrılır. 1 MB'lık parçalar G1'de humongous object olup iki region kapladı ve geliştirme sırasında reservation'da OutOfMemoryError'a yol açtı.
- **Metrik örnekleme aralığı 2 saniyedir.** p95 değeri Prometheus histogram bucket'larından hesaplanır, bu yüzden bucket çözünürlüğü kadar yaklaşıktır.
