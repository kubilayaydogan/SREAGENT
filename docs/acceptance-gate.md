# Aşama 1 kabul kapısı

Durum değerleri: **YEREL** = bu repoda yerel Docker ortamında çalıştırılarak doğrulandı ·
**KOD** = kod/yapılandırma hazır, çalıştırılmadı · **VM** = kullanıcının VM'lerinde doğrulanmalı ·
**TENANT** = Dynatrace tenant'ında doğrulanmalı · **MCP** = gerçek MCP aracıyla doğrulanmalı.

| # | Kontrol | Kabul kanıtı | Durum | Kanıt / komut |
|---|---|---|---|---|
| 1 | Yerleşim | APP_VM'de 4 ayrı Java container; DB_VM'de PG systemd | Container'lar YEREL; systemd **VM** | `docker ps`; `db-vm/verify-db-vm.sh` |
| 2 | Ortak DB | 4 servis aynı DB'ye ayrı rol + application_name ile | YEREL | smoke §7, `verify-db.sh` |
| 3 | İşlevsellik | 4 UI'dan işlem → API + kalıcı DB değişimi | YEREL (31/31) | `scripts/smoke.sh` |
| 4 | Veri bütünlüğü | iptal/check-in/koltuk/kapasite tutarlı; tekrar gönderim bozmaz | YEREL | IT testleri + smoke + `lab.integrity_check` deneyler sonrası 0 |
| 5 | Yük seçimi | tek / iki / dört hedef, ayrı operasyon/yoğunluk | YEREL | E1/E3 (tek), E2/E7 (iki), E4/E8 (dört) |
| 6 | Ölçüm | hedef/gönderilen/tamamlanan/hatalı/dropped ayrı | YEREL | EngineTest; deney kayıtları |
| 7 | Normal trafik | stres dışı servislerde sürekli düşük trafik | YEREL | tüm deneylerde `source=background` 4 servis × tüm fazlar |
| 8 | Kontrollü deney | warm-up/baseline/yük/recovery UTC kayıtlı | YEREL | `phases` alanı |
| 9 | Etkisiz yük | bağımsız yükte diğerleri etkilenmiş görünmez | YEREL (tek koşu) | E1 sonuçları; tekrarlı koşu **VM** |
| 10 | Kilit kanıtı | kaynak/etkilenen session + servis eşlemesi ile gerçek zincir | YEREL | ReservationIT, collector `blocking_edge`, `lock-chain-demo.sh` |
| 11 | Durdurma | yeni yük durur; sunucu işleri sınırlı sürede biter | YEREL | E4 stop-all: pressure=0, aktif lab sorgusu=0 |
| 12 | Telemetri | 4 servis, 2 host, JDBC, Hikari, PG sinyalleri Dynatrace'te | Hikari/HTTP export **STUB**; geri kalanı **TENANT** | `observability/dynatrace/README.md` |
| 13 | MCP veri yolu | agent sinyalleri gerçek MCP aracıyla sorgulanabilir | **MCP** | aşağıdaki tablo |
| 14 | Eksikler | doğrulanamayanlar açık | bu belge + PROJECT_STATE.md | |

## Telemetri / MCP kapısı (Aşama 2 öncesi doldurun)

| Sinyal | UI'da görünüyor | DQL dönüyor | MCP aracı ile dönüyor (araç adı) | Ingest gecikmesi | Not |
|---|---|---|---|---|---|
| 4 servis (OneAgent) yanıt süresi/hata/throughput | ☐ | ☐ | ☐ | | |
| APP_VM ve DB_VM host CPU/RAM/disk | ☐ | ☐ | ☐ | | |
| Container CPU/RAM (app, chaos, collector, ActiveGate) | ☐ | ☐ | ☐ | | |
| JVM heap/GC/thread | ☐ | ☐ | ☐ | | |
| `airlab.app.hikaricp.connections.*` | ☐ | ☐ | ☐ | | |
| `airlab.app.http.server.requests` (+percentile) | ☐ | ☐ | ☐ | | |
| `postgres.*` (extension) | ☐ | ☐ | ☐ | | |
| `airlab.pg.*` metrikleri (collector) | ☐ | ☐ | ☐ | | |
| `airlab.pg.blocking_edge` logları | ☐ | ☐ | ☐ | | |
| Uygulama ECS logları (`request_id`, `experiment_id`) | ☐ | ☐ | ☐ | | |
| trace ↔ log korelasyonu (`dt.trace_id`) | ☐ | ☐ | ☐ | | |

Yöntem: E0 → E2 → E1 sırasıyla çalıştırın; her sinyal için E2 penceresinde değişimi ve E1'de değişmemeyi görün.
