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
  actions[]   LOOP (path, params, minWorkers..maxWorkers, controlled)   — senaryolar yalnız LOOP (DB workload) kullanır;
              NETEM ve TRAFFIC eylem türleri motorda durur (arka plan trafiği TRAFFIC ile üretilir)
  control     {metric, target, gain} | null   (adaptive)
  criteria[]  {metric, scope, agg, expected(atLeast/rise/stays), required}
  parameters  kalibre edilmiş değerler (rapor için)
  expectedAffected[]
}
```

Yoğunluk seviyeleri şunlardır: LIGHT, MEDIUM, HIGH, CRITICAL ve CUSTOM.

- Değerler sabit kodlanmaz, ortamdan kalibre edilir: pool boyutu, `max_connections`, role CONNECTION LIMIT, DB vCPU sayısı, heap max ve `lab.chaos.maxConcurrentOps`.
- CUSTOM aşağıdaki alanları kabul eder: workers, dbConnections, txDurationMs, queryFrequency, targetValue, errorPct, maxAffectedServices ve budget (DB CPU, DB conn, app CPU, heap, DB mem).

Senaryoların hepsi **yalnız paylaşılan PostgreSQL'de** yük oluşturur; uygulama CPU'su, JVM heap'i, ağ (netem) ya da iş trafiği üzerinden yük veren senaryo yoktur.

- S01–S16 ve S18 birbirinden farklı tipte **tek** bir DB yükü üretir (17 farklı tip). S17, S19 ve S20 **birden fazla** yük tipini birleştirir.
- Yükü, initiator ile birlikte seçilen **her uygulama** kendi DB rolü ve bağlantılarıyla başlatır (MULTI / ALL / RANDOM modları; workload adı `<tip>-<uygulama>`). AUTO modda senaryonun önerdiği uygulamalar katılır.
- Etki paylaşılan DB üzerinden diğer uygulamalara da yansıyabilir; bu, arka plan iş trafiğinin p95 / hata oranı üzerinden ölçülür ve raporda "etkilenen uygulamalar" olarak gösterilir.

| ID | Senaryo | Kategori | Yük tipi (lab ucu) | Yükün bindiği yer |
|---|---|---|---|---|
| S01 | Table-level lock queue | lock | `table-lock` | relation lock kuyruğu (`chaos.lock_target`, ACCESS EXCLUSIVE) |
| S02 | Row lock contention | lock | `contend` | paylaşılan satırlar (`chaos.shared_records`) |
| S03 | Long-running transactions | lock | `long-tx` | transaction yaşı + tutulan row lock'lar (sıcak uçuş satırı ve chaos satırları) |
| S04 | CPU-intensive queries | db-cpu | `cpu-query` | DB_VM CPU |
| S05 | Bulk insert / WAL volume | db-io | `write-burst` | WAL hacmi / disk write (`chaos.write_sink`) |
| S06 | Sequential scan reads | db-io | `heavy-read` | buffer cache / disk read (`chaos.synthetic_data`) |
| S07 | Temporary file pressure | db-io | `temp-spill` | temp dosyaları (`work_mem` küçük, `temp_file_limit` 2 GB) |
| S08 | Serialization conflicts | lock | `serialization` | MVCC / REPEATABLE READ çakışmaları (40001, rollback) |
| S09 | Deadlock | lock | `deadlock` | `chaos.deadlock_pairs` (40P01) |
| S10 | Connection storm | connection | `connection-storm` | `max_connections` / auth / backend fork |
| S11 | Commit storm | db-io | `commit-storm` | commit yolu / WAL flush (tek satırlık transaction'lar) |
| S12 | Update churn / dead tuples | db-maintenance | `update-churn` | dead tuple, indeks bakımı, autovacuum (`chaos.churn`) |
| S13 | Advisory lock contention | lock | `advisory-lock` | advisory lock alanı (tablo / satır kilitlenmez) |
| S14 | Idle-in-transaction sessions | connection | `idle-in-tx` | bağlantı slotları + en eski snapshot (xmin) |
| S15 | Parallel query fan-out | db-cpu | `parallel-query` | parallel worker havuzu + CPU çekirdekleri |
| S16 | Point-query flood | db-cpu | `point-queries` | sorgu işleme hattı (salt okuma TPS) |
| S17 | CPU + scan saturation | combined | `cpu-query` + `heavy-read` | DB CPU + buffer cache + active sessions |
| S18 | In-memory sort pressure | db-memory | `memory-sort` | backend belleği (DB_VM RAM), temp dosyası yok |
| S19 | Vacuum blocked by old snapshots | combined | `idle-in-tx` + `update-churn` | xmin horizon + dead tuple birikimi (bloat) |
| S20 | Randomized mixed database load | combined | seed ile seçilen 2–3 tip | S02, S04, S06, S07, S10, S11, S12, S13, S15, S16 havuzundan |

Yeni yük tiplerinin nesneleri `V7__chaos_db_load_types.sql` migration'ı ile gelir (`chaos.lock_target`, `chaos.churn`); kurulumdan önce `db-migrator` çalıştırılmalıdır.

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
- **Ekranlar:** Genel durum → Senaryolar → Yeni deney ("Planı kontrol et", sonra "Deneyi başlat") → deney izleme → Geçmiş (tekrar çalıştırma). Durum, yoğunluk ve sonuç kodlarının ekrandaki karşılıkları `ui/src/labels.ts` içindedir; API değerleri değişmez.
- **Klasik panel:** `/classic` adresinden erişilir.

Uçtan uca doğrulama (APP_VM'de):

```bash
python3 scripts/chaos-e2e.py --quick                  # 20 senaryo, kanıt: deploy/evidence/chaos-v2/
python3 scripts/chaos-e2e.py --acceptance             # S17 HIGH (CPU + scan) acceptance
```

Testler:

- `mvn test -pl chaos-controller` (V2UnitTest, EngineTest);
- `mvn verify -pl flightops-service -Dit.test=ChaosLabIT`;
- `pytest observability/netem-agent`;
- `lab/chaos-controller/ui` içinde `npx vitest run`.

## 7. Bilinen sınırlamalar

- **DB_VM disk boş alanı doğrudan ölçülmez.** Koruma `temp_file_limit` (2 GB/rol), DB boyut artış limiti ve `chaos.write_sink` trim ile sağlanır.
- **Tek uygulamalı connection storm (S10) role limitine takılır.** Bu limit 40 bağlantıdır. `max_connections` doygunluğu için senaryonun ALL/MULTI modunda çalıştırılması gerekir.
- **Senaryolar netem sidecar'ı kullanmaz.** Ağ gecikmesi / paket kaybı senaryoları kaldırıldı; netem altyapısı (sidecar, `Action.netem`) motorda durur ama hiçbir senaryo planına girmez. Sidecar'lar çalışmasa da deneyler başlar.
- **Dynatrace UI bağlantılarında query parametre biçimi garanti değildir.** Bu yüzden raporda kopyalanabilir DQL de verilir.
- **Lock ve oturum senaryolarının (S01, S02, S08, S09, S13, S14) etkisi yalnız DB ve workload seviyesinde görünür.** Bu senaryolar yalnız `chaos.*` nesnelerinde (ya da boşta duran oturumlarla) çalışır, iş endpoint'leri etkilenmez; raporda "etkilenmesi beklenen uygulama" boş gelir. İş trafiğini doğrudan etkileyen lock senaryosu S03'tür (sıcak uçuş satırları).
- **Hız hedefli senaryolarda (S05, S11, S12, S16, S19) worker başına çağrı hızı sınırlıdır.** Tüm uygulamaların tüm worker'ları açıkken toplam hız hedefin yaklaşık 2 katıdır; controller hedefi worker sayısıyla tutturur. Hedefler (TPS, satır/sn) DB_VM diskine göre kalibre edilmemiştir; ulaşılamıyorsa CUSTOM `targetValue` ile düşürülmelidir.
- **Birleşik planlarda (S20, paralel random) alt senaryolar bir seviye düşük yoğunlukla çalışır.** Aksi halde toplam yük budget sınırını aşar.
- **Chaos CPU ve okuma sorguları (S04, S06, S17, S18) parallel query kapalı çalışır** (`SET LOCAL max_parallel_workers_per_gather = 0`, yalnız o transaction'da). Böylece 1 worker yaklaşık 1 vCPU'ya karşılık gelir ve adaptive controller hedefe ince adımlarla yaklaşabilir. Parallel query'yi bilinçli olarak açan tek senaryo S15'tir (yalnız kendi transaction'ında; sunucu ayarı değişmez).
- **S18 (in-memory sort) open-loop çalışır.** Bellek sert bir kaynaktır; üst sınır = uygulama × worker × sort boyutu olarak planda hesaplanır (`estimatedPeakMb`, en fazla ≈ 1,3 GB) ve CUSTOM worker sayısıyla aşılamaz. DB_VM belleği %95'i geçerse SafetyGuard deneyi durdurur.
- **S14 ve S19'daki idle-in-transaction oturumları en fazla 25 s tutulur** (rolün `idle_in_transaction_session_timeout` değeri 30 s). Oturumlar havuz dışıdır (`application_name = <servis>-lab-idle`) ve rol başına CONNECTION LIMIT (40) içinde kalır.
- **S12 ve S19 `chaos.churn` tablosunda bloat bırakır.** Satır sayısı sabittir (50 000); dead tuple'ları autovacuum temizler, ayrılan alan sonraki deneylerde yeniden kullanılır.
- **Metrik örnekleme aralığı 2 saniyedir.** p95 değeri Prometheus histogram bucket'larından hesaplanır, bu yüzden bucket çözünürlüğü kadar yaklaşıktır.
