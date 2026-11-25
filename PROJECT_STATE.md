# PROJECT_STATE

Son güncelleme: 2026-11-24 · Aşama: **2 — SRE Agent yazıldı, testlendi, APP_VM'e dağıtılıyor** · Aşama 1: **tamam**

## Chaos Controller v2 (2026-11-24)

Ayrıntı: `docs/chaos-v2-architecture.md` · API: `docs/openapi/chaos-controller.yaml` (`/api/v2/*`, canlı springdoc çıktısı).

| Konu | Durum |
|---|---|
| Dağıtım | ✅ APP_VM — UI `http://172.20.229.26:8080/ui/`, klasik panel `/classic`, 4 netem sidecar; DB_VM'de `chaos.*` şeması (V6) + `lab_os.host_stats()` |
| Senaryolar | 20 senaryo (S01–S20): connection, lock, db-cpu, db-io, app-resource, network, amplification, cascade, load |
| E2E (`scripts/chaos-e2e.py --quick`) | ✅ **20/20** COMPLETED + IMPACT_CONFIRMED, cleanup OK, recovered (`deploy/evidence/chaos-v2/summary.json` + senaryo başına rapor) |
| Cascading acceptance (S17 HIGH, initiator baggage) | ✅ DB CPU 2.0 → 83.3 % (hedef 80), query mean 0.19 → 36 ms; checkin p95 22.6 → 86 ms (etkilendi); reservation/flightops beklenip **gözlenmedi** (rapor bunu açıkça yazıyor); recovery doğrulandı |
| Testler | `mvn test -pl chaos-controller` (V2UnitTest, EngineTest), `ChaosLabIT`, netem-agent pytest, UI vitest |
| Dynatrace korelasyonu | `X-Chaos-*` başlıkları → MDC `chaos.*` log alanları; raporda hazır DQL. SRE Agent redaction `chaos.*` alanlarını siler |

Chaos v2 açık maddeleri:
1. Frontend ekran görüntüsü üretilmedi.
2. `chaos.*` alanları log'da JSON içinde; Dynatrace'te ayrı öznitelik olarak aranabilmesi için OpenPipeline kuralı yok (query'de `parse` gerekir).
3. SRE Agent kör değerlendirmesi (2026-11-24, `docs/chaos-v2-agent-eval.md`): **6/8** — row_lock, db_cpu, pool_saturation doğru; S10 (connection storm, ratio 0.63 < eşik 0.80) ve S11 (container CPU) kaçtı; network (S13) katalog dışı ama yanlış atıf yok. Ardından 4 kural eklendi (597e04f): regresyon 7/8, **bağımsız doğrulama seti 6/7** (V13 network kaçtı; `network_path_latency` kırılgan). Platform yeniden derlendi.
4. Bilinen sınırlamalar: `docs/chaos-v2-architecture.md` §7 (S10 tek uygulamada role limiti, DB_VM disk ölçülmez, lock senaryoları yalnız `chaos.*` satırları vb.).

## SRE Operations Platform (2026-11-21)

| Bileşen | Durum |
|---|---|
| `sre-platform/` backend + frontend + worker, compose `platform` profili | ✅ APP_VM'de çalışıyor — UI `http://172.20.229.26:8090` |
| sre-postgres 17.7 (internal ağ), Alembic 0001 + LangGraph checkpointer/Store | ✅ |
| Watch: `sre-worker` (PG durumlu). Eski tek başına agent container'ı ve CLI 2026-11-25'te kaldırıldı | ✅ gerçek MCP ile çalışıyor |
| Eski 7 JSON rapor `import` araştırması olarak içe aktarıldı | ✅ |
| Testler | backend 37/37 (PG), ajan çekirdeği 38/38, frontend 17/17 + tsc + build; gerçek MCP E2E (login → araştırma → SSE → kanıt) ✅ |
| Trace/Davis ilişkilendirmesi canlı veriyle | ⏳ dağıtım anında iş trafiği yoktu (sorgular doğru, sonuç EMPTY) — deney sırasında doğrulanmalı |

## Özet

| Durum | Kapsam | Sonuç |
|---|---|---|
| Kaynak kod tamamlandı | 4 iş servisi, chaos-controller, db-migrator, lab-common, pg-lock-collector, VM betikleri, belgeler | ✅ derleme + 29 test |
| Yerelde uçtan uca doğrulandı | Docker Desktop: PG17 + 5 Java container | ✅ smoke 31/31, E1–E8 tek koşu |
| İki VM üzerinde kuruldu ve doğrulandı | `app-vm` 172.20.229.26 + `db-vm` 172.20.233.108 (Hyper-V, Rocky 10.1) | ✅ PG17 systemd, Docker, 5 servis + collector, smoke 31/31, gerçek kilit zinciri |
| Gerçek Dynatrace + MCP | tenant `mfi74785`, Remote MCP `execute-dql` | ✅ 13/15 kapı sinyali veriyle okundu; ❌ PostgreSQL extension (ActiveGate hazır; extension 3.x bu ActiveGate'te INVALID_CONFIG_ERROR) |
| VM'de kontrollü deneyler | E0 → E2 → E1×3 + durdurma testi | ✅ hepsi COMPLETED, 5xx/timeout/drop 0, integrity 0; Dynatrace'te E2 etkisi var, E1'de yok |

Ayrıntı ve kanıt: `docs/acceptance-gate.md`, `deploy/evidence/vm/`, `deploy/evidence/mcp-gate-*.json`.

## Aşama 1 kabul kriterleri (kısa)

| # | Kontrol | Durum |
|---|---|---|
| 1–4 | Yerleşim, ortak DB (rol + application_name), işlevsellik, veri bütünlüğü | **VM ✅** |
| 5–8 | Yük seçimi, ölçüm, normal trafik, deney pencereleri | **VM ✅** (dört hedefli deney yalnız YEREL) |
| 9 | Etkisiz yük | **VM ✅ + TENANT ✅** E1×3 |
| 10 | Kilit kanıtı | **VM ✅ + TENANT ✅** (collector kenarı + PG `log_lock_waits` Dynatrace'te) |
| 11 | Durdurma | **VM ✅** stop-all → 1 sn içinde aktif sorgu/kilit bekleyen 0 |
| 12 | Telemetri | **TENANT ✅**: servisler, p95, hostlar, container, Hikari, HTTP, collector, loglar. ❌ `postgres.*` extension |
| 13 | MCP veri yolu | **TENANT ✅** kapı sorguları + deney pencereleri (`mcp-experiment-windows.json`) |
| 14 | Eksikler | aşağıda |

## Kurulu durum (2026-11-21)

| VM | Bileşen | Durum |
|---|---|---|
| db-vm | PostgreSQL 17.12 (PGDG, systemd), roller, `pg_stat_statements`, firewalld (5432 yalnız app-vm), SELinux Enforcing | ✅ |
| db-vm | OneAgent 1.349.123 infra-only, host grubu `airlab` | ✅ bağlı |
| app-vm | Docker 29.10 / Compose 5.8, 7 imaj, şema v4 + seed scale=1 | ✅ |
| app-vm | OneAgent 1.349.123 fullstack (5 JVM'e enjekte) | ✅ bağlı |
| app-vm | 4 iş servisi + chaos-controller + pg-lock-collector (`COLLECTOR_DT_MODE=both`, Micrometer → Dynatrace) | ✅ çalışıyor |
| app-vm | Environment ActiveGate 1.349 (grup `airlab`) + Extensions Module | ✅ çalışıyor (`--no-modules` + arka plan indirme yolu, deploy-plan) |

Erişim: `ssh airlab-app` / `ssh airlab-db` (kullanıcı `kubilay`, anahtar `~/.ssh/airlab`). Repo VM'lerde `/opt/airlab`.

## Kurulumda bulunan ve düzeltilen sorunlar

`docs/deploy-plan.md` "Gerçek kurulumda karşılaşılanlar" tablosu: Hyper-V Dynamic Memory, kayan VM saati (`makestep 1 -1`),
tr_TR yerel ayarında OneAgent/ActiveGate kurulumunun `<LiNUX>` hatası (`LC_ALL=C`), `install-docker.sh` sözdizimi hatası,
Docker 29'un kaynak IP'yi koruması (lab uçları 403 → `LAB_ALLOWED_CIDRS`), psql bağlantı bilgisinin kelime bölünmesi,
`verify-db.sh`'in `lab_owner` ile diğer rollerin oturumlarını görememesi, logs ingest'in `event.type`'ı silmesi.

## Telemetri bulguları (Aşama 2 için bağlayıcı) — `docs/telemetry-contract.md` §J

- Tenant **Smartscape on Grail**: `dt.entity.*`/`entityName()` yok → `smartscapeNodes`, `dt.smartscape.*`, `getNodeName()`.
- Servis p95 kaynağı **OneAgent** (`percentile(dt.service.request.response_time, 95)`); Micrometer yüzdelik göndermiyor.
- Container stdout ECS JSON'u ayrıştırılmadan gelir → sorguda `parse content, "JSON:j"`.
- Kilit kanıtı: collector `airlab.event == "airlab.pg.blocking_edge"` + PostgreSQL sunucu logu (`log_lock_waits`).

## Açık maddeler

1. **PostgreSQL extension çalışmıyor (Dynatrace tarafı uyumsuzluk).** `com.dynatrace.extension.postgres` 3.2.10, 3.1.0, 3.0.10 ve 2.9.8
   ActiveGate 1.349 / sqlPostgres veri kaynağı `1.349.20261013` üzerinde `INVALID_CONFIG_ERROR` (kod 32) veriyor:
   `Verification failed! Invalid attribute type string` (`SqlGroup.validateLogIngest`). İlk takılan grup `blocking_statements`
   (Locks); Locks kapatılınca hata `host_configurations` (`pg_settings`, **featureSet `default`**, kapatılamaz) grubuna geçiyor →
   veri kaynağı **tüm `ingest: log` gruplarını** reddediyor; hiçbir özellik seçimiyle çalışmaz, DB'ye hiç bağlanılmıyor. Kanıt (APP_VM):
   `/var/lib/dynatrace/remotepluginmodule/log/extensions/datasources/com.dynatrace.extension.postgres/dynatracesourcesql.jar_fastcheck.log`.
   Etki: `postgres.*` metrikleri ve extension top-queries yok. DB sinyalleri collector (`airlab.pg.*`, `blocking_edge`, `pg_stat_statements` özeti),
   PostgreSQL sunucu logları ve OneAgent DB çağrılarıyla karşılanıyor. ActiveGate/extension güncellenince yeniden denenebilir.
2. **Trace ↔ log:** ✅ OneAgent Java log zenginleştirmesi açıldı (2026-11-21 16:42); ECS loglarında `dt.trace_id`/`dt.span_id`,
   MCP ile log ↔ span eşleşmesi 13/13 (`deploy/evidence/mcp-trace-log-join.json`). Kalan: kimlik ayrı log özniteliği değil
   (sorguda JSON parse gerekiyor); Dynatrace UI'daki log→trace bağlantısı için OpenPipeline kuralı gerekir (yapılmadı).
3. **Span verisi seyrek gelebiliyor** (ATM/trial sınırları); agent metrik + log ile çapraz kontrol etmeli.
4. Default Switch IP'leri Windows yeniden başlayınca değişebilir.

## Bilinen bulgular / kalibrasyon notları (yerel)

- E2: kilit beklemesi Check-in'in kendi Hikari havuzunu doldurarak kilitsiz `lookup_booking`'i de yavaşlatıyor (ikincil mekanizma).
- E4: ana havuzlar mevcut bağlantılarını koruduğu için arka plan etkilenmedi; hata üretmek için concurrency/hold artırılmalı.
- E6: disk I/O Docker Desktop page cache nedeniyle ölçülmedi — VM'de ölçülmeli.

## Aşama 2 — SRE Agent (2026-11-21)

Kod `sre-agent/`; mimari kararlar `docs/decisions/ADR-007-sre-agent.md`; işletim `docs/sre-agent-runbook.md`.

| Konu | Durum |
|---|---|
| Yığın | Python 3.13, LangGraph 1.2.24 (checkpoint: platformda PostgresSaver), langchain-anthropic 1.7.12 (`claude-sonnet-5-5`), langchain-mcp-adapters 0.3.2 / mcp 1.30.0, LangSmith 0.16 (redakte) |
| Veri kaynağı | Yalnız Dynatrace Remote MCP `execute-dql`. 21 araç keşfedildi; sabit katalog (15 sinyal + 2 ek kanıt). DB bağlantısı yok, sessiz fallback yok |
| Arayüz | Yalnız SRE Operations Platform (CLI 2026-11-25'te kullanıcı kararıyla kaldırıldı) |
| Puan / atıf | Deterministik. Risk, data coverage ve causality confidence ayrı. Mechanism'ler: row_lock (kök engelleyici), pool_saturation, global_connection_pressure (mutlak veya göreli), db_cpu, app_vm_cpu (agent'ın kendi CPU'su `self_is_source`), app_container_cpu, network_path_latency, independent_load; tail latency (mean ×5) |
| Testler | ✅ 38/38 (`pytest`): puan formülü, veri kalitesi, 10 senaryo, graf (MCP/Claude kesintisi, checkpoint, redaksiyon, prompt injection), watch durum makinesi |
| Değerlendirme | ✅ sentetik 10/10 (`deploy/evidence/sre-agent-eval-synthetic.json`, fixture). ✅ **gerçek VM deneyleri 5/5** (`deploy/evidence/sre-agent-eval-real.json`): E0 sorun yok; E2 row_lock reservation → checkin (yüksek güven); E1×3 baggage bağımsız yük, yayılma yok |
| Örnek raporlar | `docs/examples/sre-agent/` (gerçek veri + Claude açıklaması) |
| Dağıtım | Ayrı container yok: çekirdek `sre-platform` imajına paket olarak kurulur. Ağ `airlab-agent` (dışa HTTPS). Gizli bilgiler `app-vm/agent.env` (gitignored, `scripts/gen-agent-env.sh`). `agent-data` volume'u yalnız eski raporlar için (salt-okur) |

Aşama 2 açık maddeleri:
1. Eşikler 2026-11-21 deneylerine göre kalibre edildi. E3–E8 (havuz, global bağlantı, DB/APP CPU) VM'de koşulmadı; bu
   mekanizmalar yalnız sentetik senaryolarla doğrulandı.
2. `postgres.*` extension metrikleri yok: DB CPU'nun nedeni (sorgu ya da bakım) ayrıştırılamaz.
3. Trial (~2026-12-06) bitince yeni tenant için `DT_ENV`/`T_MCP` değiştirilip `doctor` çalıştırılmalı.
4. Çalışma bitince tüm token ve anahtarlar (Dynatrace, Anthropic, LangSmith) döndürülmeli.
