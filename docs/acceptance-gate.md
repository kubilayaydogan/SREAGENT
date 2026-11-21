# Aşama 1 kabul kapısı

Bu ortam için sıralı kurulum ve kanıt dosyaları: `docs/deploy-plan.md`. Güncel ortam: APP_VM `app-vm` 172.20.229.26,
DB_VM `db-vm` 172.20.233.108 (Hyper-V, Rocky 10.1), Dynatrace SaaS `mfi74785`. VM kanıtları: `deploy/evidence/vm/`.

Durum değerleri: **VM ✅** = kullanıcının VM'lerinde çalıştırılarak doğrulandı (2026-11-21) · **YEREL** = yalnız yerel Docker'da
doğrulandı · **TENANT ✅** = gerçek tenant'ta MCP ile okundu · **AÇIK** = henüz doğrulanmadı.

| # | Kontrol | Kabul kanıtı | Durum | Kanıt / komut |
|---|---|---|---|---|
| 1 | Yerleşim | APP_VM'de 4 ayrı Java container; DB_VM'de PG systemd | **VM ✅** | `vm/verify-db-vm.txt` (systemd active, container değil, SELinux Enforcing, 5432 yalnız APP_VM); `docker ps` 6 container |
| 2 | Ortak DB | 4 servis aynı DB'ye ayrı rol + application_name ile | **VM ✅** | `vm/verify-db.txt`, `vm/db-sessions-by-role.txt` |
| 3 | İşlevsellik | 4 UI'dan işlem → API + kalıcı DB değişimi | **VM ✅** (31/31) | `vm/smoke.txt` |
| 4 | Veri bütünlüğü | iptal/check-in/koltuk/kapasite tutarlı; tekrar gönderim bozmaz | **VM ✅** | `vm/smoke.txt`, `vm/migrate-seed-check.txt`, `vm/verify-db.txt` (integrity 0) |
| 5 | Yük seçimi | tek / iki / dört hedef, ayrı operasyon/yoğunluk | **VM ✅** tek (E1) + iki hedef (E2); dört hedef yalnız YEREL (E4/E8) | `vm/experiments/E1-*.json`, `E2.json` |
| 6 | Ölçüm | hedef/gönderilen/tamamlanan/hatalı/dropped ayrı | **VM ✅** | `vm/experiments/experiments.log` (hedef/gönd./yanıt/409/5xx/t-o/drop/p50/p95/p99 ayrı) |
| 7 | Normal trafik | stres dışı servislerde sürekli düşük trafik | **VM ✅** | tüm VM deneylerinde `source=background` 4 servis × tüm fazlar |
| 8 | Kontrollü deney | warm-up/baseline/yük/recovery UTC kayıtlı | **VM ✅** | `vm/experiments/*.json` → `phases` (5 dk pencereler) |
| 9 | Etkisiz yük | bağımsız yükte diğerleri etkilenmiş görünmez | **VM ✅ + TENANT ✅** (E1×3) | aşağıdaki deney tablosu; `mcp-experiment-windows.json` |
| 10 | Kilit kanıtı | kaynak/etkilenen session + servis eşlemesi ile gerçek zincir | **VM ✅ + TENANT ✅** | `vm/lock-chain.txt`; aynı kenar Dynatrace'te `airlab.event == "airlab.pg.blocking_edge"` + PG `log_lock_waits` |
| 11 | Durdurma | yeni yük durur; sunucu işleri sınırlı sürede biter | **VM ✅** | `vm/experiments/stop-test*.{log,txt}`, `E-stop.json`: stop-all 16:02:03.77 → 16:02:04'te aktif servis sorgusu 0, kilit bekleyen 0 (öncesi 6–12 / 3–10); reservation 4 backend iptal; havuzlar açık kaldı |
| 12 | Telemetri | 4 servis, 2 host, JDBC, Hikari, PG sinyalleri Dynatrace'te | **TENANT ✅** (extension hariç) | aşağıdaki tablo; `postgres.*` extension ❌ (Dynatrace extension/ActiveGate uyumsuzluğu) |
| 13 | MCP veri yolu | agent sinyalleri gerçek MCP aracıyla sorgulanabilir | **TENANT ✅** | `mcp-gate-*.json` (13/15 kapı sorgusu), `mcp-experiment-windows.json` (deney pencereleri) — `execute-dql` |
| 14 | Eksikler | doğrulanamayanlar açık | bu belge + PROJECT_STATE.md | |

## Telemetri / MCP kapısı (gerçek tenant `mfi74785`, 2026-11-21)

MCP aracı: `execute-dql` (Remote MCP `dynatrace-mcp 1.0`). "UI" sütunu Dynatrace arayüzünde ayrıca kontrol edilmedi.
Sorgular: `scripts/mcp-probe.py` → `GATE_QUERIES`; anahtar/biçim ayrıntısı `docs/telemetry-contract.md` §J.

| Sinyal | UI | DQL / MCP (`execute-dql`) | Ingest gecikmesi | Not |
|---|---|---|---|---|
| 4 servis (OneAgent) yanıt süresi / istek sayısı | ☐ | ✅ | ≤ ~1 dk (kaba) | `smartscapeNodes`, `dt.smartscape.service` |
| Servis p95 | ☐ | ✅ | | OneAgent `percentile(dt.service.request.response_time, 95)`; Micrometer yüzdelik göndermiyor |
| APP_VM ve DB_VM host CPU/RAM | ☐ | ✅ | | `app-vm`, `db-vm` |
| Container CPU/RAM | ☐ | ✅ | | `dt.containers.cpu.usage_*_time`, `memory.resident_set_bytes` |
| JVM heap/GC/thread | ☐ | ✅ | | Micrometer `airlab.app.jvm.memory.used`, `.gc.pause`, `.threads.*` (`by: service`); OneAgent `dt.process.memory.usage` |
| `airlab.app.hikaricp.connections.*` | ☐ | ✅ | | `by: service, pool` |
| `airlab.app.http.server.requests` | ☐ | ✅ | | `rollup: count`; `.percentile` anahtarı **yok** |
| `postgres.*` (extension) | ☐ | ❌ | | extension 2.9.8/3.0.10/3.1.0/3.2.10 ActiveGate 1.349'da `INVALID_CONFIG_ERROR` (`blocking_statements` grubu) — Dynatrace tarafı uyumsuzluk; PROJECT_STATE açık madde 1 |
| `airlab.pg.*` metrikleri (collector) | ☐ | ✅ | | |
| `airlab.pg.blocking_edge` logları | ☐ | ✅ | | gerçek kilit kenarı okundu; alan `airlab.event` |
| PostgreSQL sunucu logları (lock wait) | ☐ | ✅ | | `db-vm`, `log_lock_waits` |
| Uygulama ECS logları (`request_id`, `experiment_id`) | ☐ | ✅ | | `Container Output` + `parse content, "JSON:j"` |
| trace ↔ log korelasyonu (`dt.trace_id`) | ☐ | ✅ | ≤ ~1 dk | 2026-11-21 16:42'den itibaren: OneAgent "Java – Trace/span context enrichment for logs" açıldı + servisler yeniden başlatıldı. ECS JSON'da `dt.trace_id`/`dt.span_id` var; log ↔ span eşleşmesi 13/13 (aynı span 13/13), `mcp-trace-log-join.json`. Kimlik ayrı öznitelik değil, JSON içinde → sorguda `parse content, "JSON:j"`; UI'daki log→trace bağlantısı için OpenPipeline ile `dt.trace_id` çıkarılmalı (yapılmadı). Önceki deney logları kimliksiz |

## VM deneyleri ve Dynatrace'teki karşılığı (2026-11-21)

Deney kayıtları: `deploy/evidence/vm/experiments/` (E0, E2, E1-1/2/3, E-stop; `experiments.log` faz özetleri).
Dynatrace tarafı: `deploy/evidence/mcp-experiment-windows.json` — `scripts/mcp-windows.py`, MCP `execute-dql`;
p95 = OneAgent server span'ları (`/actuator`, `/lab` hariç), ms. Deney sonrası `lab.integrity_check`: 5 kontrolün hepsi 0
(`verify-db-after-experiments.txt`).

| Pencere (UTC) | checkin p95 | reservation p95 | flightops p95 | baggage p95 | kilit kenarı logu | PG lock-wait logu | collector waiters maks |
|---|---|---|---|---|---|---|---|
| E2 baseline 14:46–14:51 | 30 | 33 | 14 | 25 | 0 | 0 | 10¹ |
| **E2 yük 14:51–14:56** | **4702** | 28 | 15 | 25 | **363** | **1200** | **20** |
| E2 recovery 14:56–15:01 | 28 | 20 | 23 | 28 | 3 | 8 | 9¹ |
| E1-1 baseline / yük | 30 / 28 | 25 / 16 | 18 / 13 | 30 / **5** (n=4301) | 0 / 0 | 0 / 0 | 0 / 0 |
| E1-2 baseline / yük | 28 / 23 | 28 / 23 | 25 / 23 | 30 / **4** (n=12127) | 0 / 0 | 0 / 0 | 0 / 0 |
| E1-3 baseline / yük | 28 / 25 | 25 / 23 | 23 / 23 | 30 / **5** (n=12131) | 0 / 0 | 0 / 0 | 0 / 0 |

¹ 1 dk'lık timeseries kovası faz sınırını kapsıyor (yük başlangıcı / bitişi).

Okuma: E2'de etki check-in'de (kilit bekleyen taraf) yoğunlaşıyor ve kilit kanıtları (collector kenarı + PG `log_lock_waits` +
waiters) yalnız yük penceresinde var; flightops/baggage değişmiyor. E1'de baggage'ın kendi p95'i hedef okuma yüküyle düşüyor
(ucuz okumalar), diğer servislerde artış yok ve hiçbir kilit sinyali yok → "etkisiz yük" Dynatrace'te de ayırt edilebiliyor.
Yük üreticisinin kendi ölçümünde (arka plan trafiği) E2 yükünde check-in `checkin` p95 33→803 ms, reservation
`create_booking` 39→1262 ms; Dynatrace p95'i check-in'e giden tüm istekleri (hedef yük dahil) kapsadığı için daha yüksek.

Gözlemler:
- Span sayıları (n) OneAgent Adaptive Traffic Management / trial giriş sınırlarına tabi: 5 dk'lık kovalarda span sayısı
  15:45'te 345'e, 16:00'da 9'a düştü (normalde 1.500–16.000). E-stop penceresinde span yok. Aşama 2 agent'ı span
  sayısına tek başına güvenmemeli; `dt.service.request.*` metrikleri ve collector/PG logları ile çapraz kontrol etmeli.
- Durdurma testi kanıtı doğrudan DB örneklemesidir (`stop-test-db.txt`, 1 sn aralık), Dynatrace'e bağlı değildir.
