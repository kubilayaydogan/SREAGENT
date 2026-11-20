# PROJECT_STATE

Son güncelleme: 2026-11-21 · Aşama: **1 (kod tamam, hedef VM + Dynatrace doğrulaması bekliyor)** · Aşama 2: **başlanmadı**

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

## Hedef ortam (kullanıcıdan, 2026-11-21)

| | Değer |
|---|---|
| APP_VM | 192.168.1.1, Rocky Linux 10.3 |
| DB_VM | 192.168.1.2, Rocky Linux 10.3 |
| Dynatrace | SaaS (güncel platform); OneAgent iki VM'e kurulacak; PostgreSQL extension kurulu değil |
| ActiveGate | Karar: **APP_VM** (DB_VM ölçülen kaynak). ⚠ Rocky 10.3 ActiveGate destek listesinde yok (10.1/10.2 var) → sorun çıkarsa ayrı Rocky 9.8/10.2 VM |
| MCP | Remote MCP, `https://<env>.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp`, platform token (Bearer) |
| VM / CPU / RAM | **bilinmiyor** — `scripts/vm-precheck.sh` ölçer (APP_VM ≥ 8 GB / 4 vCPU önerilir) |

Bu ortam için hazırlananlar (kod; VM'de çalıştırılmadı):
- `db-vm/install-postgres17.sh`, `app-vm/install-docker.sh`: EL-10 desteği (PGDG EL-10 deposu, Docker Rocky deposu, EL10'da modül yok).
- `scripts/gen-vm-env.sh`: iki VM için tutarlı rastgele parolalı env dosyaları → `deploy/out/` (git dışı). Yerelde test edildi.
- `scripts/vm-precheck.sh`: işletim sistemi, x86-64-v3, RAM/disk, SELinux, saat, Dynatrace erişimi, ağ geçidi IP çakışması.
- `scripts/mcp-probe.py`: Remote MCP initialize → tools/list → kabul kapısı DQL'leri; sonucu VERI_VAR/BOS/HATA/IZIN_HATASI olarak ayırır,
  token'sız kanıt JSON'u yazar. **Yalnız sahte MCP sunucusuna karşı test edildi** (SSE + JSON yanıt, oturum başlığı, 403, redaksiyon).
- `docs/deploy-plan.md`: token listesi (T1–T4 ve kapsamları), sıralı kurulum, kabul satırları, deney ve kanıt komutları.

## Sıradaki adım (kullanıcı ortamında)

`docs/deploy-plan.md` §2–§8'i uygulayın ve `deploy/evidence/` çıktılarını iletin. Ardından kabul kapısı ve bu belge
gerçek sonuçlarla güncellenir. **Kapı dolmadan Aşama 2'ye başlanmayacak.**

## Aşama 2

Başlanmadı. Başlangıç girdisi: bu belge, `docs/architecture.md`, `docs/telemetry-contract.md`, doldurulmuş MCP kapısı.
