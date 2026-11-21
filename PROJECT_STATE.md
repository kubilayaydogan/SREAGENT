# PROJECT_STATE

Son güncelleme: 2026-11-21 · Aşama: **1 — VM'lere kuruldu; deneyler ve PostgreSQL extension açık** · Aşama 2: **başlanmadı**

## Özet

| Durum | Kapsam | Sonuç |
|---|---|---|
| Kaynak kod tamamlandı | 4 iş servisi, chaos-controller, db-migrator, lab-common, pg-lock-collector, VM betikleri, belgeler | ✅ derleme + 29 test |
| Yerelde uçtan uca doğrulandı | Docker Desktop: PG17 + 5 Java container | ✅ smoke 31/31, E1–E8 tek koşu |
| İki VM üzerinde kuruldu ve doğrulandı | `app-vm` 172.20.229.26 + `db-vm` 172.20.233.108 (Hyper-V, Rocky 10.1) | ✅ PG17 systemd, Docker, 5 servis + collector, smoke 31/31, gerçek kilit zinciri |
| Gerçek Dynatrace + MCP | tenant `mfi74785`, Remote MCP `execute-dql` | ✅ 13/15 kapı sinyali veriyle okundu; ❌ PostgreSQL extension (ActiveGate hazır; extension 3.x bu ActiveGate'te INVALID_CONFIG_ERROR) |
| VM'de kontrollü deneyler | E0 → E2 → E1×3 | **AÇIK** |

Ayrıntı ve kanıt: `docs/acceptance-gate.md`, `deploy/evidence/vm/`, `deploy/evidence/mcp-gate-*.json`.

## Aşama 1 kabul kriterleri (kısa)

| # | Kontrol | Durum |
|---|---|---|
| 1–4 | Yerleşim, ortak DB (rol + application_name), işlevsellik, veri bütünlüğü | **VM ✅** |
| 5–8 | Yük seçimi, ölçüm, normal trafik, deney pencereleri | YEREL ✅; VM'de **AÇIK** |
| 9 | Etkisiz yük | YEREL tek koşu; VM E1×3 **AÇIK** |
| 10 | Kilit kanıtı | **VM ✅ + TENANT ✅** (collector kenarı + PG `log_lock_waits` Dynatrace'te) |
| 11 | Durdurma | YEREL ✅; VM **AÇIK** |
| 12 | Telemetri | **TENANT ✅**: servisler, p95, hostlar, container, Hikari, HTTP, collector, loglar. ❌ `postgres.*` extension |
| 13 | MCP veri yolu | **TENANT ✅** 13/15 |
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

1. **PostgreSQL extension çalışmıyor (Dynatrace tarafı uyumsuzluk).** `com.dynatrace.extension.postgres` 3.2.10, 3.1.0 ve 3.0.10
   ActiveGate 1.349 / sqlPostgres veri kaynağı `1.349.20261013` üzerinde `INVALID_CONFIG_ERROR` (kod 32) veriyor:
   `Verification failed! Invalid attribute type string` — `blocking_statements` (Locks, `ingest: log`) grubu. Denetim tanımın
   tamamına uygulandığı için Locks özellik setini kapatmak sonucu değiştirmiyor; DB'ye hiç bağlanılmıyor. Kanıt (APP_VM):
   `/var/lib/dynatrace/remotepluginmodule/log/extensions/datasources/com.dynatrace.extension.postgres/dynatracesourcesql.jar_fastcheck.log`.
   Etki: `postgres.*` metrikleri ve extension top-queries yok. DB sinyalleri collector (`airlab.pg.*`, `blocking_edge`, `pg_stat_statements` özeti),
   PostgreSQL sunucu logları ve OneAgent DB çağrılarıyla karşılanıyor. ActiveGate/extension güncellenince yeniden denenebilir.
2. **VM'de deneyler:** E0 → E2 → E1×3 (kriter 5–9, 11'in VM kanıtı).
3. JVM heap/GC sorgusu ve `dt.trace_id` ile trace ↔ log korelasyonu doğrulanmadı.
4. Default Switch IP'leri Windows yeniden başlayınca değişebilir.

## Bilinen bulgular / kalibrasyon notları (yerel)

- E2: kilit beklemesi Check-in'in kendi Hikari havuzunu doldurarak kilitsiz `lookup_booking`'i de yavaşlatıyor (ikincil mekanizma).
- E4: ana havuzlar mevcut bağlantılarını koruduğu için arka plan etkilenmedi; hata üretmek için concurrency/hold artırılmalı.
- E6: disk I/O Docker Desktop page cache nedeniyle ölçülmedi — VM'de ölçülmeli.

## Aşama 2

Başlanmadı. Girdi: bu belge, `docs/architecture.md`, `docs/telemetry-contract.md` (§J dahil), `docs/acceptance-gate.md`.
