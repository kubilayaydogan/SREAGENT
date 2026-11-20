# PROJECT_STATE

Son güncelleme: 2026-11-20 · Aşama: **1 (kod tamam, hedef VM + Dynatrace doğrulaması bekliyor)** · Aşama 2: **başlanmadı**

## Özet

| Durum | Anlamı | Kapsam |
|---|---|---|
| Kaynak kod tamamlandı | derleniyor, testler geçiyor | 4 iş servisi, chaos-controller, db-migrator, lab-common, pg-lock-collector, VM betikleri, belgeler |
| Yerelde uçtan uca doğrulandı | Docker Desktop: PG17 container + 5 Java container | smoke 31/31, E1–E8 tek koşu, lock zinciri, stop-all cleanup |
| İki VM üzerinde doğrulandı | Rocky 9 APP_VM + DB_VM (systemd PG17) | **HAYIR** — VM erişimi yok |
| Gerçek Dynatrace + MCP üzerinde doğrulandı | tenant UI / DQL / MCP aracı | **HAYIR** — tenant/MCP erişimi yok |

## Derleme ve test (gerçekten çalıştırıldı)

- `scripts/build.sh` → `BUILD SUCCESS` (2026-11-19 23:00 UTC), 24 JVM testi 0 hata:
  lab-common 3, MigratorIT 2, ReservationIT 4, CheckinIT 3, FlightOpsIT 3, BaggageIT 3, EngineTest 6
  (IT'ler Testcontainers PG17 + gerçek `create-roles.sql` ile).
- pg-lock-collector pytest: 5 test (komut README'de).
- `scripts/smoke.sh`: 31/31 (4 UI → API → kalıcı DB değişimi).
- Deney kayıtları: `data/runs/E1..E8.json` (git dışı, yerel); özetleri `docs/experiments.md`.

## Aşama 1 kabul kriterleri

Ayrıntılı tablo: `docs/acceptance-gate.md`. Kısa durum:

| # | Kontrol | Durum |
|---|---|---|
| 1 | Yerleşim | Container'lar YEREL; DB_VM systemd **VM'de doğrulanmalı** |
| 2–8 | Ortak DB, işlevsellik, bütünlük, yük seçimi, ölçüm, normal trafik, deney pencereleri | YEREL |
| 9 | Etkisiz yük | YEREL tek koşu; tekrarlı koşu **VM** |
| 10–11 | Kilit kanıtı, durdurma | YEREL |
| 12 | Telemetri (4 servis, 2 host, JDBC, Hikari, PG) | Micrometer export yalnız **STUB**; gerisi **TENANT** |
| 13 | MCP veri yolu | **MCP** — hiçbir sinyal henüz MCP ile sorgulanmadı |
| 14 | Eksikler | bu belge |

## Bilinen bulgular / kalibrasyon notları

- E2: kilit beklemesi Check-in'in kendi Hikari havuzunu doldurarak kilitsiz `lookup_booking`'i de yavaşlatıyor (ikincil mekanizma).
- E4: ana havuzlar mevcut bağlantılarını koruduğu için arka plan etkilenmedi; hata üretmek için concurrency/hold artırılmalı veya `maxLifetime` beklenmeli.
- E6: disk düzeyi I/O Docker Desktop page cache nedeniyle ölçülmedi — VM'de ölçülmeli.
- Tüm sayılar Docker Desktop'ta, kısa pencerelerle; hedef donanımda yeniden kalibre edilmeli.

## Sıradaki adım: VM + Dynatrace doğrulaması (kullanıcı ortamında)

Girdi gerekli (secret değil): APP_VM / DB_VM IP, Rocky sürümü, CPU/RAM; Dynatrace tenant türü, OneAgent/ActiveGate yerleşimi,
PostgreSQL extension durumu, MCP türü ve endpoint'i. Parolalar/token'lar yalnız `app-vm/.env` ve `db-vm/db-vm.env` içinde.

1. DB_VM: `docs/runbook.md` §1 → `verify-db-vm.sh` çıktısı (kriter 1).
2. APP_VM: runbook §2 → migrate / seed / check / up.
3. Doğrulama: runbook §3 (`smoke.sh`, `verify-db.sh`, `lock-chain-demo.sh`) APP_VM'den.
4. Dynatrace: `observability/dynatrace/README.md`; OneAgent (iki host), ActiveGate + PG extension, collector `COLLECTOR_DT_MODE`.
5. Deneyler E0 → E2 → E1 (E1 en az 3 tekrar); her sinyal için `docs/acceptance-gate.md` "Telemetri / MCP kapısı" tablosu
   ve `docs/telemetry-contract.md` §I doldurulur (UI / DQL / MCP araç adı / ingest gecikmesi).
6. Kapı dolunca Aşama 2'ye geçilir. Kapı dolmadan Aşama 2'ye başlanmayacak.

## Aşama 2

Başlanmadı. Başlangıç girdisi: bu belge, `docs/architecture.md`, `docs/telemetry-contract.md`, doldurulmuş MCP kapısı.
