# airlab SRE Operations Platform

Mevcut Python SRE Agent'ın (`sre-agent/`, Dynatrace Remote MCP üzerinden salt-okunur analiz) üstüne kurulan çok
kullanıcılı operasyon platformu: kalıcı PostgreSQL hafızası, FastAPI backend, SSE ile canlı ajan adımları, React arayüz,
incident / RCA / bilgi bankası / onay akışları, PostgreSQL durumlu sürekli izleme (watch) ve zamanlanmış kontroller.

Ajan çekirdeği **yeniden yazılmadı**: sinyal kataloğu, puanlama, atıf ve LangGraph akışı `airlab_agent` paketinden aynen
kullanılır. Platform yalnız çekirdeğin olay kancalarını (`on_event`, `on_node`, `checkpointer`) kullanır.

## Mimari

```
tarayıcı ──:8090──► sre-frontend (nginx: SPA + /api ters vekil, SSE tamponsuz)
                         │
                         ▼
                    sre-backend (FastAPI, tek uvicorn worker) ──► Dynatrace Remote MCP (execute-dql)
                         │      └─ InvestigationRunner → airlab_agent.run_analysis (thread) ──► Claude (yalnız açıklama/özet)
                         ▼
                    sre-postgres (ağ: airlab-sre-data, internal; port yayımlanmaz)
                         ▲
                    sre-worker (iş kiralama: FOR UPDATE SKIP LOCKED + lease)
                         ├─ watch        60 sn — airlab_agent.watch durum makinesi, durum PG'de → incident + araştırma
                         ├─ health_check 15 dk — LLM'siz tam analiz → araştırma (yüksek/kritik → incident)
                         └─ retention    1 sa  — eski kayıtlar, süresi dolan onaylar
```

- **Ajan salt-okunurdur.** Değişiklik gerektiren her adım (SQL_WRITE, RESTART, DB_CONFIG, INFRA) onay talebi olarak
  açılır; ajan hiçbir şeyi uygulamaz (human-in-the-loop).
- **Sahte veri yok.** Dynatrace boş dönerse arayüz "veri yok" gösterir; kanıt satırı `EMPTY`/`UNAVAILABLE` olarak kalır.
- Roller: `viewer` (okur) < `sre` (araştırma, incident, geri bildirim) < `admin` (kullanıcı, iş, onay kararı).

## Dosyalar

| Yol | İçerik |
|---|---|
| `backend/sre_platform/db/` | SQLAlchemy 2 modelleri, Alembic göçleri (`migrations/versions/0001`), async oturum |
| `backend/sre_platform/repositories/` | tablo başına veri erişimi (incident durum makinesi, dedup, iş kiralama …) |
| `backend/sre_platform/agent/` | çalışma zamanı, `InvestigationRunner`, hafıza (LangGraph PG checkpointer + Store), sohbet orkestrasyonu |
| `backend/sre_platform/services/` | SSE run kaydı, RCA üretimi, telemetri özeti, `watch_pg` (PG durumlu watch), `correlation` |
| `backend/sre_platform/api/` | FastAPI uygulaması, şemalar, rotalar |
| `backend/sre_platform/worker.py` | zamanlanmış işler |
| `backend/sre_platform/seed/` | bilgi bankası tohumu (6 bilinen sorun, 5 runbook) + eski rapor içe aktarımı |
| `frontend/src/` | React 19 + TS + Vite + Tailwind; sayfalar, SSE istemcisi, grafikler (Recharts) |
| `../app-vm/docker-compose.yml` | `platform` profili: sre-postgres, sre-migrate, sre-backend, sre-worker, sre-frontend |
| `.env.example` | tüm `SRE_*` değişkenleri |

## Veritabanı şeması (`sre` şeması)

`users`, `applications`, `conversations`, `messages`, `investigations`, `investigation_evidence`,
`agent_tool_executions`, `incidents`, `incident_investigations`, `incident_events`, `rca_reports`, `agent_feedback`,
`known_issues`, `runbooks`, `approval_requests`, `scheduled_jobs`, `job_runs`, `watch_conditions`, `watch_windows`,
`watch_events`, `kv`. LangGraph checkpointer ve Store tabloları ayrı (`langgraph` şeması) — `sre-platform migrate` kurar.

## API (hepsi `/api` altında; `health`, `ready`, `auth/login` hariç JWT gerekir)

| Alan | Uç noktalar |
|---|---|
| Sistem | `GET health`, `GET ready` (şema sürümü), `GET system/info` |
| Kimlik | `POST auth/login`, `GET auth/me`, `GET/POST users`, `PATCH users/{id}` |
| Katalog | `GET applications`, `GET applications/{id}/health`, `GET applications/{id}/metrics`, `GET dashboard/summary` |
| Ajan | `POST agent/chat` (202), `POST agent/investigate`, `GET agent/stream/{run_id}` (SSE, `Last-Event-ID`), `GET agent/runs[/{id}]`, `POST agent/runs/{id}/cancel` |
| Sohbet | `GET/POST conversations`, `GET/PATCH conversations/{id}`, `GET conversations/{id}/messages`, `POST conversations/{id}/resume` |
| Araştırma | `GET investigations`, `GET investigations/{id}` + `/evidence` `/tools` `/similar` `/recommendations` `/feedback` |
| Incident | `GET/POST incidents`, `GET/PATCH incidents/{id}`, `POST incidents/{id}/notes`, `POST incidents/{id}/investigations` |
| RCA | `GET rca`, `POST rca/{inv_id}/generate`, `GET rca/{id}`, `GET rca/{id}/markdown` |
| Geri bildirim | `POST feedback`, `GET feedback/summary` |
| Bilgi bankası | `GET/POST known-issues`, `GET known-issues/{id}`, `GET/POST runbooks`, `GET runbooks/{id}` |
| Onay | `GET/POST approvals`, `POST approvals/{id}/decision`, `POST approvals/{id}/cancel` |
| İşler | `GET jobs`, `GET jobs/runs`, `POST jobs/{name}/enabled` |

SSE olayları: `investigation_started`, `agent_thinking`, `tool_started`, `tool_completed`, `tool_failed`,
`evidence_found`, `message_delta`, `investigation_completed`, `rca_ready`, `error`, `done` (+ keepalive).
OpenAPI şeması: `http://<APP_VM>:8090/api/openapi.json` (Swagger UI `/api/docs` CDN kullandığı için arayüzün CSP politikasında açılmayabilir).

## Hafıza

- **Kısa vadeli:** sohbet mesajları PG'de; LangGraph `PostgresSaver` her analiz çalışmasının durumunu saklar
  (yarım kalan çalışma `resume` ile sürdürülür).
- **Uzun vadeli:** LangGraph `PostgresStore` — tamamlanan araştırmaların özeti (mekanizma, uygulama, kök neden) ad
  alanlarına yazılır; yeni araştırmada benzer geçmiş kayıtlar, incident'lar ve runbook'lar önerilir.
- **Kurumsal:** bilinen sorunlar / runbook'lar (tohum + kullanıcı), geri bildirim (doğru/yanlış/kısmen + düzeltme).

## Kurulum (APP_VM)

```bash
# Windows/yerel: gizli değerleri üret (repoya girmez)
scripts/gen-sre-platform-env.sh deploy/out/sre-platform.env
scp deploy/out/sre-platform.env airlab-app:/opt/airlab/app-vm/sre-platform.env   # sonra chmod 600

# APP_VM
cd /opt/airlab/app-vm
docker compose --profile platform build sre-migrate sre-frontend
docker compose --profile platform up -d          # sre-migrate: alembic upgrade + tohum, sonra servisler
docker compose exec sre-worker sre-platform import-reports /legacy/reports   # kaldırılan eski agent'ın raporları (idempotent)
```

Göç komutları: `docker compose --profile platform run --rm sre-migrate` (= `sre-platform migrate --seed`);
kullanıcı: `docker compose exec -e SRE_NEW_USER_PASSWORD=... sre-backend sre-platform create-user <ad> sre`.

Arayüz: **http://172.20.229.26:8090** — ilk giriş `admin`, parola `sre-platform.env` içindeki `SRE_ADMIN_PASSWORD`.

Kaynak sınırları: postgres 384 MB / 0.5 CPU, backend 640 MB / 1 CPU, worker 640 MB / 0.75 CPU, frontend 96 MB.

## Geliştirme ve test

```bash
# backend (PostgreSQL gerekir; SRE_TEST_DATABASE_URL yoksa DB testleri atlanır)
cd sre-platform/backend && pip install -e ../../sre-agent -e .[test]
SRE_TEST_DATABASE_URL=postgresql+psycopg://sre:...@127.0.0.1:55432/sre pytest
# frontend
cd sre-platform/frontend && npm ci && npx tsc -b --noEmit && npx vitest run && npm run build
```

## Slack bildirimi

Risk score'u `SRE_SLACK_RISK_THRESHOLD` (varsayılan 75, "kritik" band) değerine eşit veya üstünde olan tamamlanmış
(veya kısmi) RCA'lar Slack'e tek mesaj olarak gider: başlık, risk/severity, mechanism, kaynak ve etkilenen uygulamalar,
İstanbul saatiyle pencere, RCA özeti ve araştırma linki. Sohbet, manuel, zamanlanmış ve watch araştırmalarının hepsi
kapsanır. Aynı araştırma bir kez bildirilir; gönderim hatası araştırmayı etkilemez ve araç kaydında (`slack:notify`)
görünür. Kurulum: Slack'te bir app oluşturup Incoming Webhook ekleyin, URL'yi `sre-platform.env` içindeki
`SRE_SLACK_WEBHOOK_URL`'ye yazın, `sre-backend` ve `sre-worker`'ı yeniden başlatın.

## LangSmith ve LLM Monitoring

- **Tracing:** her sohbet turu tek trace'tir (`chat-turn`; run id = kullanıcı mesajının id'si). Analiz grafı, Dynatrace
  DQL sorguları (tool run), cevap ve Slack bildirimi altındadır. `conversation_id` metadata'sı LangSmith Threads
  görünümünde turları birleştirir. Investigation'ın run id'si investigation id'sidir; risk, severity, mechanism,
  kapsam ve kısmi veri koşu sonunda feedback olarak yazılır. Detay sayfasında "LangSmith'te aç" bağlantısı vardır.
- **LLM Monitoring (`/llm`, sre/admin):** worker işi `langsmith-sync` (`SRE_LANGSMITH_SYNC_MINUTES`, varsayılan 5)
  LangSmith'ten kök trace ve LLM çağrısı özetlerini `sre.llm_runs`'a çeker. Prompt/cevap içeriği **tutulmaz**;
  yalnız token, maliyet (LangSmith fiyat tahmini), latency, ilk token süresi, durum ve kimlikler. Pano
  (`GET /api/observability/llm?range=24h|7d|30d`) yalnız bu tablodan okur; LangSmith kapalıyken de çalışır.
- İmleç `kv['langsmith_sync']`. Yeniden doldurmak için bu anahtarı silin; sonraki tur `SRE_LANGSMITH_BACKFILL_DAYS`
  geriye gider (upsert idempotent). Kayıtlar `SRE_RETENTION_DAYS` sonra silinir.

## Bilinen sınırlar

- SSE çalıştırma kaydı süreç içidir → backend tek uvicorn worker; yeniden başlatmada canlı akış kesilir (araştırma
  kaydı PG'de kalır, yarım çalışma `resume` ile sürdürülür).
- JWT `localStorage`'da ve iptal listesi yok (süre: 8 saat); kullanıcı pasifleştirilince sonraki istekte reddedilir.
- Benzerlik araması PostgreSQL `simple` metin araması + mekanizma eşleşmesi (Türkçe kök bulma yok).
- Kategorik renk paletinde check-in ↔ baggage CVD ayrımı düşük (ΔE ≈ 10.6, tüm çiftler kontrolünde); grafiklerde
  lejant ve tooltip seri adı ikincil kodlama olarak var.
- APP_VM'de bellek sınırlı (~2 GB boş); derlemeyi deney çalışırken yapmayın.
