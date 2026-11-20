# Telemetri sözleşmesi (Aşama 1 → Aşama 2 girdisi)

Bu belge, Aşama 2'deki SRE Agent'ın **Dynatrace MCP üzerinden** okuyacağı sinyallerin kaynağını, gerçek
alan/metrik adını, birimini, periyodunu, sorgu yolunu, uygulama eşlemesini ve eksik veri davranışını tanımlar.

**Doğrulama durumu etiketleri**

| Etiket | Anlamı |
|---|---|
| `YEREL-DOĞRULANDI` | Bu repoda, yerel Docker ortamında (PG17 container + 5 Java container) gerçekten çalıştırılıp çıktısı görüldü |
| `STUB-DOĞRULANDI` | Format, sahte bir Dynatrace ingest ucuna gönderilerek görüldü; gerçek tenant'ta değil |
| `DOKÜMAN` | Adlar resmî Dynatrace dokümanından alındı (19-20 Kasım 2026); tenant'ta görülmedi |
| `DOĞRULANMADI` | Tasarım/varsayım; kullanıcının tenant'ında ve MCP aracıyla kanıtlanmalı |

> UI'da görünen her verinin MCP ile okunabileceği varsayılmaz. Aşama 2 başlamadan önce §I'daki kabul kapısı
> gerçek tenant + gerçek MCP aracıyla doldurulmalıdır.

---

## 0. Kimlik ve eşleme kuralları

| Kavram | Değer | Nerede | Durum |
|---|---|---|---|
| Servis adı | `reservation-service`, `checkin-service`, `flightops-service`, `baggage-service`, `chaos-controller` | `spring.application.name`; ECS log `service.name`; Micrometer `service` boyutu | YEREL-DOĞRULANDI |
| DB rolü | `svc_reservation`, `svc_checkin`, `svc_flightops`, `svc_baggage` (+ `lab_monitor`, `dynatrace`, `lab_owner`) | `pg_stat_activity.usename`, `pg_stat_statements.userid` | YEREL-DOĞRULANDI |
| JDBC application_name | Servis adı; lab baskı havuzu `<servis>-lab-pressure`, cleanup `<servis>-lab-cleanup` | `pg_stat_activity.application_name` | YEREL-DOĞRULANDI |
| Hikari pool | `reservation-pool`, `checkin-pool`, `flightops-pool`, `baggage-pool`; lab: `<servis>-pressure` | Micrometer `pool` boyutu | YEREL-DOĞRULANDI |
| operation | Düşük cardinality iş operasyonu (ör. `create_booking`, `checkin`, `lab_hold_booking`) | `http.server.requests{operation}`, log `operation`, SQL yorumu `op=` | YEREL-DOĞRULANDI |
| request_id | `X-Request-Id` (yoksa UUID) | Log `request_id`, SQL yorumu `rid=`, yanıt başlığı | YEREL-DOĞRULANDI |
| experiment_id | `exp-YYYYMMDD-HHMMSS-xxxx` (chaos-controller), `X-Experiment-Id` | Log `experiment_id`, SQL yorumu `exp=`, collector log alanları | YEREL-DOĞRULANDI |
| trace_id | OneAgent PurePath / log enrichment (`dt.trace_id`) | OneAgent | DOĞRULANMADI |

**Kurallar**
1. `pg_stat_statements`'ta application_name yoktur → DB tarafı servis eşlemesi **rol** ile yapılır (rol = servis, 1:1).
2. `request_id`, `experiment_id`, `pid`, `query_id` **yalnız log/span** alanıdır; hiçbir metrikte boyut değildir.
3. `experiment_id` deneyleri ilişkilendirmek içindir; nedensellik kanıtı değildir. Senaryo etiketi (`scenario`)
   yalnız chaos-controller kaydında durur ve agent gözlem girdisine **verilmemelidir**.
4. Saatler UTC; tüm VM'lerde chrony zorunlu. Deney pencereleri chaos-controller kaydındaki UTC zamanlardır.

---

## A. pg-lock-collector (salt-okur, `lab_monitor` rolü)

Periyot: `INTERVAL_S=5` sn (blocking, oturum, kapasite, pg_stat_database); `STATEMENTS_INTERVAL_S=60` sn (pg_stat_statements).
Gönderim: Metrics API v2 `POST {DT_URL}/api/v2/metrics/ingest` (line protocol, `metrics.ingest`),
Logs API v2 `POST {DT_URL}/api/v2/logs/ingest` (JSON dizi, `logs.ingest`). Ortak boyut: `db.host`.

### A.1 Metrikler

| Metrik anahtarı | Tür / birim | Boyutlar | Kaynak SQL | Durum |
|---|---|---|---|---|
| `airlab.pg.connections.used` | gauge / adet | db.host | `count(*) pg_stat_activity WHERE backend_type='client backend'` (tüm DB'ler) | YEREL-DOĞRULANDI |
| `airlab.pg.connections.normal_slots` | gauge / adet | db.host | `max_connections - superuser_reserved - reserved` | YEREL-DOĞRULANDI |
| `airlab.pg.connections.used_ratio_normal` | gauge / oran 0..1+ | db.host | used / normal_slots (rezerve slotlar paydada YOK) | YEREL-DOĞRULANDI |
| `airlab.pg.sessions` | gauge / adet | db.role, app, state, wait_type | pg_stat_activity grup | YEREL-DOĞRULANDI |
| `airlab.pg.lock.waiters` | gauge / adet | waiter.db_role, waiter.app, blocker.db_role, blocker.app | `pg_blocking_pids()` kenarları | YEREL-DOĞRULANDI |
| `airlab.pg.lock.wait_max_seconds` | gauge / saniye | (aynı) | `now() - state_change` | YEREL-DOĞRULANDI |
| `airlab.pg.lock.waiters_total` | gauge / adet | db.host | kenar sayısı; **sorgu başarılıysa 0 yazılır** | YEREL-DOĞRULANDI |
| `airlab.pg.db.{xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_bytes,conflicts}` | count delta / adet, bayt | db.host | pg_stat_database delta | YEREL-DOĞRULANDI |
| `airlab.pg.statements.{calls,exec_time_ms,shared_blks_read,shared_blks_hit,rows}` | count delta / adet, ms, blok | db.host, db.role | pg_stat_statements rol toplamı, 60 sn delta | YEREL-DOĞRULANDI |
| `airlab.pg.collector.up` | gauge 0/1 | db.host | toplama turu başarılı mı | YEREL-DOĞRULANDI |
| `airlab.pg.collector.scrape_ms` | gauge / ms | db.host | tur süresi (üretici maliyeti) | YEREL-DOĞRULANDI |

Line protocol örneği (yerel çıktıdan):
```
airlab.pg.statements.shared_blks_read,db.host="local-postgres",db.role="svc_baggage" count,delta=140148 1795123938518
```
Dynatrace'te bu satırların kabul edildiği (HTTP 202, metrik anahtarı oluştu) **DOĞRULANMADI**.

### A.2 Log olayları

`event.type = "airlab.pg.blocking_edge"` (her örnekte her waiter→blocker kenarı için bir kayıt) — YEREL-DOĞRULANDI

| Alan | Örnek | Not |
|---|---|---|
| `timestamp` | `2026-11-19T21:14:58.547Z` | örnek zamanı (UTC) |
| `content` | `checkin-service(svc_checkin) pid 550 waits Lock/transactionid for reservation-service(svc_reservation) pid 563 wait=3.32s ...` | insan okunur |
| `waiter.pid`, `blocker.pid` | 550, 563 | yüksek cardinality → yalnız log |
| `waiter.db_role` / `waiter.app` | svc_checkin / checkin-service | eşleme anahtarı |
| `waiter.wait_event_type` / `waiter.wait_event` | Lock / transactionid | satır kilidi beklemesi |
| `waiter.wait_seconds`, `waiter.xact_age_seconds` | 3.315 | saniye |
| `waiter.query_id`, `blocker.query_id` | `252833788663058488` | pg_stat_statements queryid ile birleşir (fingerprint) |
| `waiter.op`, `waiter.request_id`, `waiter.experiment_id` | checkin, `exp-...-723`, `exp-...` | SQL yorumundan |
| `blocker.state`, `blocker.wait_event_type` | active / Timeout | blocker `pg_sleep` içindeyse `Timeout` |
| `blocker.xact_age_seconds` | 3.458 | blocker transaction yaşı |
| `blocker.op`, `blocker.lab_hold` | lab_hold_booking / hold-inventory-lock | `lab_hold` doluysa blocker sentetik tutma işlemidir |

Ek olaylar: `airlab.pg.top_statement` (60 sn penceresinde rol×queryid bazlı en pahalı 10 sorgu: `calls_delta`,
`exec_ms_delta`, `mean_ms`, `shared_blks_read_delta`, `query_text`), `airlab.pg.stats_reset`, `airlab.pg.collector_error`.

**Önemli yorum notları (yerel ölçümle görüldü)**
- Zincirlerde `checkin→checkin` kenarları da görülür: PostgreSQL tuple-lock kuyruğunda bekleyenler birbirini bekler.
  Kök blocker, zincirin ucundaki **beklemeyen** (state=active/idle in transaction, wait_event_type≠Lock) oturumdur.
- `pg_stat_statements.total_exec_time` **kilit bekleme süresini içerir**. E2 sonrası `svc_checkin` exec_time yüksek
  görünür; bu CPU değildir. CPU/I/O ayrımı için `shared_blks_read`, host CPU ve wait_event_type birlikte okunmalıdır.

---

## B. Dynatrace PostgreSQL extension (ActiveGate) — `DOKÜMAN`

Periyot: varsayılan 1 dk (long query 5 dk). DB_VM için ana iç kaynak; collector ile **birlikte** kullanılır.

| Metrik | Birim | Kullanım |
|---|---|---|
| `postgres.numbackends` | adet | DB bağlantı sayısı (instance) |
| `postgres.activity.active`, `postgres.activity.active_waiting`, `postgres.activity.idle_in_transaction` | adet | bekleyen oturumlar (kilit/IO ayrımı yok) |
| `postgres.locks` | adet | kilit sayısı |
| `postgres.xact_commit.count`, `postgres.xact_rollback.count` | adet/dk | throughput, rollback (lock_timeout sonrası artar) |
| `postgres.blks_read.count`, `postgres.blks_hit.count` | blok/dk | I/O vs cache |
| `postgres.deadlocks.count`, `postgres.temp_bytes.count` | adet, bayt | |
| `postgres.sessions.count`, `postgres.sessions_killed.count` | adet | cleanup/terminate etkisi |
| Top queries (log, `event.group = top_queries`, 5 dk) | — | fingerprint; rol eşlemesi alanı **DOĞRULANMADI** |

Extension metriklerinde rol/application_name boyutu **yoktur** (DOĞRULANMADI); servis bazlı ayrım için collector A.1 kullanılır.

## C. Uygulama metrikleri (Micrometer → Dynatrace) — `STUB-DOĞRULANDI`

Ayar: `DT_METRICS_EXPORT=true` (bkz. `observability/dynatrace/README.md` §2.3). Periyot `DT_METRICS_STEP=30s`.
Ön ek `airlab.app.`, ortak boyutlar `service`, `db_role`, `dt.metrics.source=micrometer`.
Aynı metrikler `/actuator/prometheus` üzerinde de vardır (YEREL-DOĞRULANDI).

| Metrik | Tür | Ek boyutlar | Anlam |
|---|---|---|---|
| `airlab.app.hikaricp.connections.active` / `.idle` / `.pending` / `.max` / `.min` | gauge | pool | **Tek pool doluluğu** (payda: `.max`) |
| `airlab.app.hikaricp.connections.acquire` | summary (sn) | pool | connection bekleme süresi |
| `airlab.app.hikaricp.connections.timeout` | count | pool | `connectionTimeout` aşımı |
| `airlab.app.hikaricp.connections.usage` | summary | pool | bağlantının tutulma süresi |
| `airlab.app.http.server.requests` | summary (ms: min/max/sum/count) | operation, uri, method, status, outcome, exception, error | throughput, ortalama, max |
| `airlab.app.http.server.requests.percentile` | gauge | + `phi` (0.5/0.95/0.99) | **servis başına** p50/p95/p99 (DOĞRULANMADI: anahtar adı, stub'da yalnız summary görüldü) |
| `airlab.app.jvm.memory.used`, `jvm.gc.pause`, `jvm.threads.live` | gauge/summary | area, id / action, cause | JVM |

Kurallar: Servislerin p95'leri **ortalanmaz**; histogram/ham veri olmadan "global p95" üretilmez.
Pool doluluğu (`active/max`) ile DB instance doluluğu (`airlab.pg.connections.used_ratio_normal`) farklı paydalara sahiptir.

## D. OneAgent yerleşik sinyaller — `DOĞRULANMADI`

| Sinyal | Beklenen kaynak | Not |
|---|---|---|
| Servis yanıt süresi / hata / throughput | OneAgent servis metrikleri (`builtin:service.*` veya Grail `dt.service.request.*`) | Tenant'ın Grail metrik adlarıyla MCP üzerinden doğrulanmalı |
| JDBC/DB çağrı süresi | PurePath database calls | DB bekleme nedenini ayırmaz |
| Host CPU / RAM / disk (APP_VM, DB_VM ayrı) | `dt.host.cpu.usage`, `dt.host.memory.usage`, `dt.host.disk.*` | APP_VM ortak kaynak etkisini DB'den ayırmak için zorunlu |
| Container CPU/RAM (4 app + chaos + collector + ActiveGate) | OneAgent container metrikleri | Üretici/izleme bileşenlerinin tüketimi raporda ayrı gösterilmeli |
| Process (postgres) CPU/IO | OneAgent process metrikleri (DB_VM) | |

## E. Uygulama logları (ECS JSON, stdout → OneAgent log ingest) — `YEREL-DOĞRULANDI` (format), ingest `DOĞRULANMADI`

Logger `airlab.access`: her istekte bir satır (5xx veya ≥1000 ms ise WARN). Alanlar: `@timestamp`, `log.level`,
`service.name`, `request_id`, `experiment_id`, `operation`, `db_role`, `http.method`, `http.route`, `http.status`,
`duration_ms`. Hata satırı: `message = "db failure kind=<KIND> ..."`, KIND ∈ `LOCK_TIMEOUT, STATEMENT_TIMEOUT,
POOL_TIMEOUT, DB_CONNECTION_REFUSED, DB_CONNECTION_LOST, DB_UNAVAILABLE, DEADLOCK, SERIALIZATION, INTEGRITY, DB_ERROR`.
REST hata gövdesi aynı `code` alanını taşır.

## F. chaos-controller — `YEREL-DOĞRULANDI`

- Deney kaydı: `GET /api/experiments/{id}` (JSON), volume `/data/experiments/<id>.json`.
  `phases.{WARMUP,BASELINE,LOAD,RECOVERY} = [startUtc, endUtc]`, `results[]` (faz×kaynak×app×op: intended, sent,
  completed, ok, conflict, clientError, serverError, timeout, ioError, dropped, noData, p50/p95/p99/max ms, achievedRps,
  errorRate), `producer` (chaos JVM CPU %, heap, thread), `cleanup`.
- Metrikler (`/actuator/prometheus`): `chaos_requests_total{app,operation,source,phase,outcome}`,
  `chaos_request_latency_seconds{app,source,phase}`, `chaos_experiment_active`, `chaos_experiment_phase`.
- `source` değerleri: `background` (normal trafik), `target` (stres), `probe` (etki ölçer).
- Bu kayıtlar **değerlendirme verisidir**; Aşama 2'de agent'ın canlı kanıt girdisi Dynatrace MCP'dir.

## G. Eksik / sıfır / gecikmiş veri davranışı

| Durum | Collector | Uygulama metrikleri | Agent'ın yorumlaması gereken |
|---|---|---|---|
| Gerçek sıfır | `waiters_total=0` + `collector.up=1` | count=0 | "örnek anlarında bekleyen yok" (kısa kilitler kaçabilir) |
| Toplama hatası (izin, timeout, DB yok) | metrik yazılmaz, `collector.up=0`, `collector_error` logu | — | **bilinmiyor**, sağlıklı değil |
| Collector/ingest kapalı | hiçbir `airlab.pg.*` noktası yok | — | veri yok ≠ 0 |
| Stats reset / restart | o tur delta yazılmaz, `airlab.pg.stats_reset` logu | — | pencere kısmi |
| Ingest gecikmesi | — | — | DOĞRULANMADI; tenant'ta ölçülüp pencere sonuna tampon eklenmeli |
| Yetki hatası | `collector_error` (42501) | — | kapsam eksik |

## H. Örnek DQL (tamamı `DOĞRULANMADI`)

```dql
// Blocking zinciri kenarları (E2 penceresi)
fetch logs, from: "2026-11-19T21:14:10Z", to: "2026-11-19T21:15:00Z"
| filter event.type == "airlab.pg.blocking_edge"
| summarize edges = count(), max_wait = max(toDouble(`waiter.wait_seconds`)),
            by: { `waiter.app`, `blocker.app`, `blocker.lab_hold` }

// Rol bazlı bekleyen oturumlar
timeseries waiters = max(airlab.pg.lock.waiters), by: { `waiter.app`, `blocker.app` }, interval: 1m

// Tek pool doluluğu (servis başına payda kendi max'ı)
timeseries active = avg(airlab.app.hikaricp.connections.active), pending = max(airlab.app.hikaricp.connections.pending),
           by: { service, pool }

// Global DB bağlantı oranı (rezerve slotlar hariç payda)
timeseries ratio = max(airlab.pg.connections.used_ratio_normal)

// PostgreSQL extension
timeseries backends = avg(postgres.numbackends), waiting = avg(postgres.activity.active_waiting)
```

## I. Aşama 2 öncesi kabul kapısı (gerçek veriyle doldurulacak)

Ayrıntılı tablo: `docs/acceptance-gate.md`. Özet: her satır için (1) Dynatrace UI'da görünüyor, (2) DQL ile dönüyor,
(3) **MCP aracıyla** dönüyor, (4) ingest gecikmesi ölçüldü, (5) gerekli token/MCP yetkisi belgelendi.
Bu kapı geçilmeden Aşama 2 sonuçları "gerçek Dynatrace kanıtı" olarak sunulmaz.
