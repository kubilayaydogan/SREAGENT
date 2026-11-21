# SRE Agent örnek raporları

Bu dizindeki raporlar **gerçek** veriden üretildi: 2026-11-21 VM deneyleri, tenant `mfi74785`, Dynatrace MCP ve
Claude `claude-sonnet-5-5`. Sentetik (fixture) değildir. Fixture raporları `"fixture": true` alanını ve Markdown'da
"FIXTURE / SENTETİK VERİ" uyarısını taşır; bu dizinde fixture rapor yoktur.

| Dosya | Pencere (UTC) | Baseline | Agent sonucu |
|---|---|---|---|
| `real-E2-row-lock.*` | 14:52–14:56 | 14:47–14:51 | `row_lock`; kaynak reservation-service, etkilenen checkin-service; güven yüksek |
| `real-E1-1-independent-load.*` | 15:07–15:11 | 15:02–15:06 | `independent_load`; kaynak baggage-service, yayılma yok; güven orta |

Agent'a yalnız zaman aralıkları verildi. Deney adı ve etiketi girdi değildir.
Değerlendirme özetleri: `deploy/evidence/sre-agent-eval-real.json` (5/5) ve `deploy/evidence/sre-agent-eval-synthetic.json` (10/10, fixture).
