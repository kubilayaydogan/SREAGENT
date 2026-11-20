# Dynatrace gözlemlenebilirlik hazırlığı (Aşama 1)

> Durum: **tasarım + yapılandırma hazır, tenant üzerinde DOĞRULANMADI.** Bu laboratuvarın geliştirildiği
> ortamda Dynatrace tenant'ına, OneAgent'a veya ActiveGate'e erişim yoktu. Aşağıdaki her adımın sonunda
> "kabul kontrolü" vardır; sonuçları `docs/acceptance-gate.md` tablosuna işleyin.

## 1. Yerleşim

```
                 Dynatrace SaaS (Grail / DQL)
                     ▲             ▲                ▲
     OneAgent (full) │             │ Extensions 2.0 │ Metrics API v2 / Logs API v2
                     │             │                │  (metrics.ingest, logs.ingest)
 ┌───────────────────┴──┐   ┌──────┴────────┐   ┌───┴──────────────────────────┐
 │ APP_VM (Rocky 10.3)  │   │ Environment   │   │ pg-lock-collector (APP_VM)   │
 │  OneAgent full-stack │   │ ActiveGate    │   │ Micrometer Dynatrace export  │
 │  Docker: 4 app +     │   │ (APP_VM'de    │   │ (opsiyonel, uygulamalardan)  │
 │  chaos + collector   │   │  varsayılan)  │   └──────────────────────────────┘
 └──────────────────────┘   └──────┬────────┘
                                   │ 5432 (dynatrace rolü, pg_monitor)
 ┌──────────────────────┐          ▼
 │ DB_VM (Rocky 10.3)   │◄─────────┘
 │  OneAgent (host/     │
 │  infra modu yeterli) │
 │  postgresql-17       │
 └──────────────────────┘
```

| Bileşen | Yer | Neden | Kaynak notu |
|---|---|---|---|
| OneAgent full-stack | APP_VM | Java code module: servis, PurePath, JDBC çağrıları, JVM/GC/thread; Docker container metrikleri; container stdout logları | Docker'da otomatik enjeksiyon resmi destek tablosunda var (`Auto-injection in Docker container (Deep monitoring)`) |
| OneAgent (Infrastructure/Foundation modu) | DB_VM | Host CPU/RAM/disk/ağ, postgres process'leri. PostgreSQL iç metriklerini OneAgent'a bırakmıyoruz | Rocky Linux 8/9/10 x86-64 destek tablosunda |
| Environment ActiveGate | **APP_VM** (karar: `docs/deploy-plan.md` §0). ⚠ ActiveGate destek tablosu Rocky 10.1/10.2 listeler, **10.3 listede yok**; sorun çıkarsa ayrı Rocky 9.8/10.2 VM (B planı) | PostgreSQL extension uzaktan izleme ActiveGate grubunda koşar | Düşük kaynaklı lab'da ayrı VM yoksa APP_VM; ActiveGate bellek tüketimi APP_VM ölçümlerine **eklenir** — raporda ayrı gösterin. Ayrı VM varsa `ACTIVEGATE_IP` ile DB_VM firewall'una eklenir |
| PostgreSQL extension (Extensions 2.0) | ActiveGate grubu | `postgres.*` metrikleri, top queries/longest queries **log** olarak | Varsayılan sorgu aralığı 1 dk, long query 5 dk |
| pg-lock-collector | APP_VM container (değiştirilebilir) | 5 sn aralıkla blocking zinciri + rol/app oturumları + rol bazlı statement deltaları | Agent değildir, salt-okur |

## 2. Kurulum adımları

### 2.1 OneAgent
1. Dynatrace → *Deploy OneAgent* → Linux, **x86-64**; kurulum komutunu kopyalayın (token içerir, repoya yazmayın).
2. APP_VM: full-stack mod. Kurulumdan **sonra** container'ları yeniden başlatın: `docker compose up -d --force-recreate`.
3. DB_VM: infrastructure (veya foundation) modu yeterli.
4. Her iki hostta host grubu: `--set-host-group=airlab`. Saat: `chronyc tracking` (her iki VM, sapma < 100 ms).
5. Container'lar `DT_TAGS="airlab airlab_service=<ad>"` ortam değişkeniyle gelir (compose'da).
   **DOĞRULANMADI:** bu değişkenin Docker container süreçlerinde etiket olarak uygulandığı tenant'ta kontrol edilmeli.

**Kabul:** Dört Java servisi ayrı process group/servis olarak görünür; her serviste JDBC (PostgreSQL) çağrıları
ve veritabanı servisi görünür; iki host görünür.

### 2.2 PostgreSQL extension
1. DB tarafı hazır: `db-vm/create-roles.sql` `dynatrace` rolünü `pg_monitor` + `pg_use_reserved_connections`
   ile, `dynatrace` şemasını ve `pg_stat_statements`'ı oluşturur. Ayarlar `db-vm/postgresql.lab.conf`
   (`shared_preload_libraries`, `track_activity_query_size=4096`, `pg_stat_statements.track_utility=off`, `track_io_timing=on`).
2. Hub'dan *PostgreSQL* extension'ı etkinleştirin; monitoring configuration:
   - ActiveGate grubu: APP_VM ActiveGate'inin grubu
   - Endpoint: `DB_VM_IP:5432`, database `airlab` (ek olarak `postgres`), kullanıcı `dynatrace`
   - Feature set: varsayılan + top queries (log monitoring açık olmalı)
3. Yapılandırma JSON şeması tenant'a/extension sürümüne bağlıdır; burada uydurulmamıştır. UI'dan oluşturup
   dışa aktarılan JSON'u `observability/dynatrace/postgres-extension.monitoring.json` olarak (parolasız) saklayın.

**Kabul:** `postgres.numbackends`, `postgres.activity.active_waiting`, `postgres.locks`, `postgres.xact_commit.count`
metrikleri DQL ile okunur (bkz. `docs/telemetry-contract.md` §B).

### 2.3 Uygulama metrikleri (Hikari / HTTP p95 / JVM)
İki yol; birini seçin, ikisini aynı anda açmayın (çift sayım):

| Yol | Ayar | Not |
|---|---|---|
| A. SaaS Metrics API v2 (önerilen başlangıç) | `.env`: `DT_METRICS_EXPORT=true`, `DT_METRICS_URI=https://<env-id>.live.dynatrace.com/api/v2/metrics/ingest`, `DT_METRICS_TOKEN=<metrics.ingest>` | Container'dan internete HTTPS çıkışı gerekir |
| B. OneAgent yerel ingest | `DT_METRICS_URI=http://<host-gateway>:14499/metrics/ingest`, token gerekmez | Ayarlar → *Extension Execution Controller* → "local HTTP Metric, Log and Event Ingest API" açık olmalı. Uç noktanın container'dan erişilebilirliği **DOĞRULANMADI** (yalnız yerel istemciler kabul edilir) |

Yerelde sahte bir ingest ucuna gönderim doğrulandı: `airlab.app.hikaricp.connections.{active,idle,pending,acquire,timeout,usage}`,
`airlab.app.http.server.requests` (`service`, `db_role`, `operation`, `uri`, `status`, `outcome` boyutları),
`airlab.app.jvm.*`. Ayrıntı: `docs/telemetry-contract.md` §C.

### 2.4 pg-lock-collector
```
# .env
COLLECTOR_DT_MODE=both          # dosya + Dynatrace
DT_URL=https://<env-id>.live.dynatrace.com
DT_INGEST_TOKEN=<metrics.ingest + logs.ingest kapsamlı Api-Token>
docker compose --profile observability up -d pg-lock-collector
```
**Kabul:** `fetch logs | filter event.type == "airlab.pg.blocking_edge"` E2 deneyi sırasında kayıt döndürür;
`airlab.pg.lock.waiters_total` metriği görünür.

## 3. Token kapsamları (en az yetki)

| Token | Kapsam | Kullanan |
|---|---|---|
| Ingest token | `metrics.ingest`, `logs.ingest` | pg-lock-collector, Micrometer (yol A) |
| OneAgent kurulum | PaaS / installer token (UI'nın ürettiği) | yalnız kurulum anı |
| MCP (platform token) | `mcp-gateway:servers:invoke`, `mcp-gateway:servers:read`, `storage:{metrics,logs,entities,events,buckets,smartscape,spans}:read` | `scripts/mcp-probe.py`, Aşama 2 |

## 4. Bilinen sınırlar
- PostgreSQL extension varsayılan 1 dk aralıkla örnekler; 2–4 sn'lik kilitleri kaçırabilir → collector 5 sn.
  Daha sık ingest DDU/metrik tüketimini artırır: `airlab.pg.*` satır sayısı ≈ (sabit 8 + rol×app×state kombinasyonu + bekleme kenarı) / 5 sn.
  Gerçek tüketimi tenant'ta *Account Management → Usage* üzerinden ölçün.
- Collector kendi örnekleri arasında oluşup biten kilitleri göremez; "kayıt yok" ≠ "kilit yok".
- OneAgent JDBC izleri DB bekleme süresini kapsar fakat bekleme nedenini (lock/CPU/I/O) ayırmaz; neden kanıtı collector + extension'dan gelir.
