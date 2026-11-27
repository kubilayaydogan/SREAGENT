# SRE Agent runbook (Aşama 2)

Kod: `sre-agent/` (ayrıntı `sre-agent/README.md`). Karar kaydı: `docs/lab/decisions/ADR-007-sre-agent.md`.
Ajan yalnız SRE Agent Management Platform üzerinden çalışır (ayrı komut satırı aracı/container yoktur).

## 1. Kurulum / güncelleme

`sre-platform/README.md` → Kurulum. Ajan çekirdeği `sre-migrate` imaj derlemesine dahildir:

```bash
cd /opt/airlab/app-vm
docker compose --profile platform build sre-migrate sre-frontend
docker compose --profile platform run --rm sre-migrate
docker compose --profile platform up -d sre-backend sre-worker sre-frontend
```

Bağlantı kontrolü: arayüzde sistem durumu (`GET /api/system/info`: MCP, Claude, LangSmith) ve LLM Monitoring (`/llm`).

## 2. Arıza durumları

| Belirti | Anlamı | Eylem |
|---|---|---|
| Rapor `status: partial`, kanıtlar `UNAVAILABLE` | MCP'ye ulaşılamadı. Başka kaynağa geçilmedi | Sistem durumu (`/api/system/info`). Platform token süresi/kapsamı, tenant URL'si (trial bitti mi?), APP_VM dış ağı |
| Bir sinyal `PERMISSION` | Token kapsamı eksik | Platform token'a `storage:*:read` kapsamlarını ekleyin |
| `STALE` / `EMPTY` (http, hosts, collector) | Telemetri hattı sorunlu | OneAgent, `pg-lock-collector` (`docker logs airlab-pg-lock-collector`), ingest token |
| `explanation_source: deterministik (Claude erişilemedi)` | Anthropic API'ye ulaşılamadı | Rapor yine geçerli; anahtar ve kota kontrolü |
| Watch olayında `telemetry_gap` | 5 dk boyunca o koşul için veri yok | Sıfır değil, **bilinmiyor** demektir; hattı kontrol edin |
| Watch `mcp_unavailable` | MCP kesintisi | Pencereler ilerletilmez. MCP dönünce kaçırılan pencereler (≤10) değerlendirilir |
| `app_vm_cpu` mekanizmasında `self_is_source: true` | Agent'ın kendi CPU tüketimi artışın kaynağı | `watch.poll_seconds`'ı büyütün; sre-worker kaynak sınırı compose'da |

## 3. Yeni tenant

`secrets.env` içinde `DT_ENV` ve `T_MCP` değerlerini değiştirin, ardından `gen-agent-env.sh` çalıştırıp `agent.env`'i VM'ye kopyalayın ve sre-backend/sre-worker'ı yeniden başlatın.
Metrik anahtarları (`airlab.*`) ve log alanları Aşama 1 telemetri sözleşmesinden gelir; yeni tenant'ta da aynı
ingest ayarlarıyla oluşur.
