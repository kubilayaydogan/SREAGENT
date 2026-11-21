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
| 5 | Yük seçimi | tek / iki / dört hedef, ayrı operasyon/yoğunluk | YEREL; VM **AÇIK** | E1/E3, E2/E7, E4/E8 yerel kayıtları |
| 6 | Ölçüm | hedef/gönderilen/tamamlanan/hatalı/dropped ayrı | YEREL; VM **AÇIK** | EngineTest; deney kayıtları |
| 7 | Normal trafik | stres dışı servislerde sürekli düşük trafik | YEREL; VM **AÇIK** | `source=background` |
| 8 | Kontrollü deney | warm-up/baseline/yük/recovery UTC kayıtlı | YEREL; VM **AÇIK** | `phases` alanı |
| 9 | Etkisiz yük | bağımsız yükte diğerleri etkilenmiş görünmez | YEREL (tek koşu); VM E1×3 **AÇIK** | |
| 10 | Kilit kanıtı | kaynak/etkilenen session + servis eşlemesi ile gerçek zincir | **VM ✅ + TENANT ✅** | `vm/lock-chain.txt`; aynı kenar Dynatrace'te `airlab.event == "airlab.pg.blocking_edge"` + PG `log_lock_waits` |
| 11 | Durdurma | yeni yük durur; sunucu işleri sınırlı sürede biter | YEREL; VM **AÇIK** | E4 stop-all |
| 12 | Telemetri | 4 servis, 2 host, JDBC, Hikari, PG sinyalleri Dynatrace'te | **TENANT ✅** (extension hariç) | aşağıdaki tablo; `postgres.*` extension **AÇIK** (UI yapılandırması) |
| 13 | MCP veri yolu | agent sinyalleri gerçek MCP aracıyla sorgulanabilir | **TENANT ✅** 13/15 | `deploy/evidence/mcp-gate-*.json` (`execute-dql`) |
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
| JVM heap/GC/thread | ☐ | ☐ | | sorgulanmadı (**AÇIK**) |
| `airlab.app.hikaricp.connections.*` | ☐ | ✅ | | `by: service, pool` |
| `airlab.app.http.server.requests` | ☐ | ✅ | | `rollup: count`; `.percentile` anahtarı **yok** |
| `postgres.*` (extension) | ☐ | ❌ | | ActiveGate + Extensions Module hazır; extension UI yapılandırması (**AÇIK**) |
| `airlab.pg.*` metrikleri (collector) | ☐ | ✅ | | |
| `airlab.pg.blocking_edge` logları | ☐ | ✅ | | gerçek kilit kenarı okundu; alan `airlab.event` |
| PostgreSQL sunucu logları (lock wait) | ☐ | ✅ | | `db-vm`, `log_lock_waits` |
| Uygulama ECS logları (`request_id`, `experiment_id`) | ☐ | ✅ | | `Container Output` + `parse content, "JSON:j"` |
| trace ↔ log korelasyonu (`dt.trace_id`) | ☐ | ☐ | | örnek kayıtlarda `dt.trace_id` görülmedi (**AÇIK**) |

Kalan yöntem: VM'de E0 → E2 → E1×3 çalıştırıp her sinyalin E2 penceresinde değiştiğini, E1'de değişmediğini gösterin.
