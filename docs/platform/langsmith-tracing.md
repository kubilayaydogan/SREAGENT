# LangSmith tracing: production debugging

Bu doküman `LangSmith_Tracebility.md`'deki gereksinimlerin nasıl karşılandığını anlatır. Kapsam: trace ağacı, MCP ↔ agent ilişkilendirmesi, LangSmith'e giden verinin kontrolü ve doğrulama.

Kurulu sürümler (`sre-agent/requirements.lock`, backend imajı ile aynı):

| Paket | Sürüm |
|---|---|
| langsmith | 0.16.0 |
| langchain-core | 1.8.4 |
| langgraph | 1.2.24 |
| langchain-anthropic | 1.7.12 |

Bu sürümlerde kullanılan API'ler:
- `langsmith.Client(hide_inputs=, hide_outputs=, hide_metadata=)`
- `LangChainTracer` alt sınıfı: `_get_stacktrace`, `_persist_run_single`, `_update_run_single`, `_start_trace`, `_llm_run_with_token_event`
- `CallbackManager.on_tool_start(..., run_id=)`

## 1. Mevcut akış

Dört giriş noktası da `InvestigationRunner.run` → `airlab_agent.graph.run_analysis` üzerinden aynı `StateGraph`'ı çalıştırır. Instrumentation bu ortak akıştadır.

| Giriş | Kod | trigger / mode | Trace kökü |
|---|---|---|---|
| Sohbet | `agent/chat.py` → `investigate` düğümü | `chat` | `chat-turn` (run id = kullanıcı mesajı id'si). Analiz `airlab-chat` olarak altında |
| Manuel investigation | `api/routes/agent.py` | `manual` | `airlab-manual` (run id = investigation id) |
| Zamanlanmış health check | `worker.py` `health_check` | `scheduled` | `airlab-scheduled` |
| Watch | `services/watch_pg.py` | `watch` | `airlab-watch` |

Akışların ayrıldığı yerler:
- **Kök:** Sohbette kök `chat-turn`'dür. Analiz ağacı onun altına yerleşir; aynı trace'te route, recall ve respond da bulunur. Diğer girişlerde analiz kendisi köktür.
- **Analiz aralığının kaynağı:**
  - Manuel: kullanıcı girdisi (`user`).
  - Sohbet: sorudan yorumlanır (`user_question`); açık bir süre yoksa `default`.
  - Scheduler: `scheduler`.
  - Watch: `watch`.

Mevcut otomatik trace'ler yeniden kullanıldı. LangGraph düğümleri ve LangChain model çağrıları zaten run üretiyor; yeni span eklenmedi, bunlar zenginleştirildi. Yeni span yalnız LangChain dışında kalan MCP çağrıları için var (`mcp:<query_key>`). `child_config` sayesinde iç içe çağrılar üst trace'e yerleşir; ayrı kök açılmaz, aynı graf, düğüm ya da model çağrısı iki kez görünmez.

## 2. Trace ağacı

Örnek ağaçlar fixture'dan alındı (`sre-agent/tests/tracecap.py`, gerçek `langsmith.Client` gönderim yolu). Kısaltıldı:

```
manual  — metadata: platform_run_id=rep-…, thread_id=rep-…, mode=manual, trigger=manual, environment=lab, investigation_id=…
airlab-manual [chain]  {"status": "complete", "partial_reasons": [], "report_id": "rep-…"}
  plan [chain]            {"window_source": "user", analysis_window, baseline_window, baseline_source}
  collect [chain]         {"outcomes": {"success": 15}, "mcp_failure": null, signals_requested, queries_used}
    mcp:http [tool]       outcome=success attempts=2 (1. deneme timeout, 2. success) records=8
    mcp:spans [tool]      outcome=success attempts=1 records=8
    ... (+13 mcp:* span, paralel)
  analyze [chain]         {"risk": {"score": 65.0, "band": "yüksek", "coverage": 1.0, …}, "mechanisms": ["row_lock", …],
                           evidence_ids, data_quality, components}
    needs_extra [chain]   (LangGraph conditional edge; çıktı yalnız sonraki düğümün adı)
  extra_evidence [chain]  {"decision": "collect", "reason": "row_lock → lock edge ayrıntısı", "iteration": 1, "outcomes": {"empty": 1}}
    mcp:edges_detail [tool] outcome=empty
  explain [chain]         {"llm_called": true, "model": "…", "outcome": "ok", "duration_ms": …, "input_tokens": …, "output_tokens": …}
    explain_findings [llm]   (içerik yok; model adı + usage_metadata)
  finalize [chain]        {"report_id": "rep-…", "status": "complete", "partial_reasons": [], elapsed_s, queries_used, llm_calls}

scheduled (bir MCP çağrısı timeout) — trigger=scheduled
airlab-scheduled [chain]  {"status": "partial", "partial_reasons": ["mcp_timeout", "no_usable_signal"]}
  plan [chain]            {"window_source": "scheduler"}
  collect [chain]         {"outcomes": {"unavailable": 15}, "mcp_failure": "timeout"}
    mcp:spans [tool]      ERROR  TraceSafeError: outcome=timeout attempts=2 elapsed_ms=601 mcp_call_id=…
    mcp:http [tool]       outcome=success
  analyze [chain]         {"risk": {"score": null, "coverage": 0.0}, "status": "partial"}
  explain [chain]         {"outcome": "llm_disabled"}
  finalize [chain]        {"status": "partial", "partial_reasons": ["mcp_timeout", "no_usable_signal"]}
```

Sohbette aynı analiz ağacı `chat-turn` kökünün altında `airlab-chat` olarak yer alır.

| Adım | Görünen (güvenli) özet |
|---|---|
| `plan` | Analiz ve baseline aralıkları; aralığın kaynağı (`window_source`, `baseline_source`) |
| `collect` | İstenen signal'ler, sonuç sayıları, MCP hatası türü, kullanılan query sayısı. Outcome değerleri: success, empty, timeout, permission_denied, tool_error, unavailable, skipped_budget, rejected |
| `analyze` | Kullanılan evidence id'leri, signal başına veri kalitesi, risk skoru ve band, coverage, sayısal skor bileşenleri, mechanism'ler |
| `extra_evidence` | Karar (`collect`/`skip`), nedeni, iterasyon numarası, toplanan anahtarlar ve sonuçları; ayrıca bir sonraki karar. `analyze` çıktısındaki `extra_decision` atlama kararını ve nedenini gösterir |
| `explain` | LLM çağrıldı mı; model; süre; sonuç (ok, llm_disabled, time_budget, refusal, empty_response, budget_exceeded, llm_unavailable); token kullanımı. Structured output kullanan `interpret_question` çağrısında `validation: ok/failed` görünür |
| `finalize` | Rapor id'si, nihai durum, kısmi kalma nedenleri (`mcp_timeout`, `mcp_unavailable`, `no_usable_signal`, `signals_permission`…) |

## 3. MCP ↔ agent ilişkilendirmesi

**Kök metadata:**
- `platform_run_id` ve `thread_id`
- `mode`, `trigger`, `environment` (Settings › Observability › environment_tag)
- `attempt`
- `investigation_id`, `conversation_id`, `config_hash`
- Tag'ler: `trigger:<x>`, `env:<x>`

**Platform run_id ↔ LangSmith eşlemesi:** Yeni tablo `sre.trace_links` (migration `0011`). Her çalıştırma girişimi bir satırdır; `platform_run_id`, `langsmith_run_id` ve `langsmith_trace_id` ayrı alanlardır.
- `trace_id` tracer'ın gördüğü kök trace'tir. Sohbetteki analizde bu, sohbet turunun trace'idir.

**Checkpoint sonrası devam:** Yarıda kalan sohbet turu `resume` ile sürdürülürse yeni bir trace açılır.
- Bu trace aynı `platform_run_id`'ye (turun kullanıcı mesajı) `attempt=2, resumed=true` olarak bağlanır.
- Önceki girişimin satırı korunur.
- Kök metadata'sında da `platform_run_id`, `attempt` ve `resumed` vardır. LangSmith'te `platform_run_id` ile filtrelenince iki trace de bulunur.

**MCP çağrısı:**
- Her mantıksal çağrı için gateway bir `mcp_call_id` üretir. Bu değer hem platformdaki çağrı kaydının (`agent_tool_executions.id`) hem de LangSmith tool span'inin kimliğidir.
- Span girdileri: connector, tool, query_key, time_range, mcp_call_id.
- Span çıktıları: outcome, result_status, records, elapsed_ms, evidence_id, dynatrace_query_id, `attempts[]`.
- Retry'lar aynı span'in `attempts` listesindedir; her denemenin outcome'u ayrı görünür.
- Paralel çağrılar, çağıran düğümün (`collect` / `extra_evidence`) altındadır.

**Hata ve fallback ayrımı:**
- Yetki hatası, tool hatası ve timeout span'i hatalı (ERROR) olarak işaretler; hata metni yalnız sınıflandırmadır.
- Agent fallback ile devam ettiğinde MCP hatası `collect.mcp_failure` alanında, run'ın nihai durumu `finalize.status` / `partial_reasons` alanında görünür.
- Platform çağrı kaydında timeout `result_status=TIMEOUT` olarak tutulur; SSE olayında `outcome` ve `mcp_call_id` alanları var.

**Arayüz:**
- Investigation detayındaki "LangSmith'te aç" bağlantısının üzerine gelince platform run_id ve trace_id görünür.
- "Tool executions" sekmesinde span'i yazılmış her MCP çağrısının satırında "LangSmith span" bağlantısı vardır.
- Uç: `GET /api/investigations/{id}/trace`. Yetki `record:read` (mevcut trace ucuyla aynı). Dönen alanlar: `url`, `platform_run_id`, `links[]`, `call_url_template`.

## 4. LangSmith'e giden verinin kontrolü

Güvenli varsayılan: yalnız allowlist'teki alanlar gider. Uygulama yeri `airlab_agent/tracing.py`.

| Katman | Kontrol |
|---|---|
| Her run'ın inputs/outputs'u (otomatik nested trace'ler dahil) | `Client(hide_inputs=trace_safe, hide_outputs=trace_safe)`. Yalnız izinli skaler alanlar ve kodun ürettiği `step_summary` / `extra_decision` özetleri kalır. Atılan alanların yalnız adları görünür (`_omitted`) |
| Metadata | `hide_metadata=safe_metadata`: izinli anahtarlar (platform kimlikleri, `langgraph_*`, `ls_*`) + `usage_metadata`; liste/dict değerler atılır |
| `serialized` (model repr), `extra.invocation_params` | `hide_*` bunları görmez. `SafeTracer` gönderimden önce süzer; yalnız model adı ve sayısal parametreler kalır |
| Hata / stack trace | `SafeTracer._get_stacktrace`: yalnız exception tipi (+ `kind`). Ham mesaj ve traceback gitmez. MCP span hataları kodun ürettiği `TraceSafeError` özetidir |
| Streaming token olayları | Kaydedilmez |
| Callback'ler | Ortamdaki otomatik izleme bayrakları kapatılır; yalnız `SafeTracer` kullanılır |

Gönderilmeyenler:
- Ham agent state'i
- Kullanıcı sorusu
- Prompt ve model çıktısı
- Ham DQL
- MCP payload'ları
- Dynatrace log/kayıtları
- Token ve credential'lar

Tracing kapalıysa (`langsmith_enabled=false`, sampling dışı ya da `without_tracing`) hiçbir run gönderilmez. LangSmith erişilemezse gönderim arka planda başarısız olur (`timeout_ms=5000`). Analiz, skorlama, checkpoint ve rapor akışı etkilenmez.

**Ortam ↔ proje:**
- Proje Settings › Observability › LangSmith project'ten ya da `LANGSMITH_PROJECT` ortam değişkeninden gelir.
- Test ve production için ayrı proje adı verilir, örneğin `airlab-sre-agent-staging` ve `airlab-sre-agent`.
- `environment_tag` ve `SRE_ENVIRONMENT` metadata/tag olarak her köke yazılır.

## 5. Doğrulama

| # | Senaryo | Test | Sonuç |
|---|---|---|---|
| 1 | Manuel ve scheduler tek kök trace, doğru trigger | `sre-agent/tests/test_trace_observability.py::test_tree_parallel_mcp_retry_and_no_leak`, `::test_mcp_timeout_marks_span_and_partial_report`; platform `tests/test_trace_links.py::test_manual_and_scheduled_single_root_trigger_and_call_ids` | Geçti |
| 2 | Paralel MCP doğru run/düğüm altında; retry aynı `mcp_call_id`'de ayırt edilir | `test_tree_parallel_mcp_retry_and_no_leak`. Eşzamanlılık > 1 doğrulandı; span id = olaydaki mcp_call_id = platform kaydı id | Geçti |
| 3 | MCP timeout hatalı span; kısmi rapor ayrıca anlaşılır | `test_mcp_timeout_marks_span_and_partial_report` | Geçti |
| 4 | Checkpoint sonrası devam aynı platform run_id ile bulunur | `test_trace_links.py::test_resumed_chat_turn_keeps_platform_run_id` | Geçti |
| 5 | Sentetik gizli değerler payload'a sızmaz | Kalıba uymayan düz metin secret, Anthropic key, parola, e-posta, ham DQL, soru, model çıktısı (nested LLM run, metadata, invocation_params, serialized ve hata metni dahil): `test_tree_parallel_mcp_retry_and_no_leak`, `test_exception_detail_is_not_sent`, `test_trace_safe_allowlist_shapes` | Geçti |
| 6 | Tracing kapalı / LangSmith erişilemez iken çıktı korunur | `test_output_preserved_when_tracing_off_or_unreachable` (kapalı port) | Geçti |

Test 5 ilk koşuda gerçek bir sızıntı yakaladı. `hide_*` fonksiyonları LLM run'ının `serialized` ve `extra.invocation_params` alanlarını görmüyor; metadata'daki liste değerleri de geçiyordu. Bu yollar `SafeTracer.scrub_run` ve metadata anahtar allowlist'iyle kapatıldı.

**LangSmith arayüzünde ayrıca doğrulanmalı.** Bu oturumda gerçek LangSmith ortamına erişilmedi; yukarıdakiler fixture ile doğrulandı.
- Tool span linkinin (`/r/{mcp_call_id}`) trace bağlamında açılması.
- Kök metadata filtreleri: `platform_run_id`, `trigger`, `environment`.
- `explain_findings` run'ında token/maliyet sütunlarının `usage_metadata`'dan dolması.
- `_omitted` alanlarının okunabilirliği.
- Threads görünümünde `conversation_id` birleştirmesi.

## 6. Değişen dosyalar

- `sre-agent/src/airlab_agent/tracing.py`: allowlist, `SafeTracer`, güvenli hata metni, trace_id eşlemesi, `ToolSpan(run_id=)`.
- `sre-agent/src/airlab_agent/mcp_gateway.py`:
  - `mcp_call_id`, outcome sınıflandırması, retry denemeleri ve `time_range`.
  - `McpUnavailable.kind`.
  - Son denemeden sonra gereksiz backoff kaldırıldı.
- `sre-agent/src/airlab_agent/graph.py`: düğüm `step_summary`'leri, tek kaynaklı `extra_decision`, `window_source`, kök metadata.
- `sre-platform/backend/sre_platform/agent/investigation.py`: kök metadata (environment, attempt, window_source), çağrı kaydı id = mcp_call_id, `TIMEOUT`, trace eşlemesi.
- `sre-platform/backend/sre_platform/agent/chat.py`: tur metadata'sı, tur/resume eşlemesi.
- `sre-platform/backend/sre_platform/services/langsmith_trace.py`: `record_link`, `trace_info`, `has_trace`.
- `sre-platform/backend/sre_platform/db/models.py` ve `migrations/versions/0011_trace_links.py`: `TraceLink`.
- `sre-platform/backend/sre_platform/api/routes/records.py`: trace ucu.
- `sre-platform/backend/sre_platform/repositories/investigations.py`: `tool_started(id=)`.
- `sre-platform/backend/sre_platform/services/retention.py`: sohbet turu eşlemeleri için retention.
- Frontend: `components/RecordTables.tsx`, `hooks/queries.ts`, `pages/InvestigationDetailPage.tsx`, `features/settings/sections.tsx`.
- Testler:
  - `sre-agent/tests/test_trace_observability.py`, `sre-agent/tests/tracecap.py`
  - `sre-platform/backend/tests/test_trace_links.py`, `tests/test_settings.py` (migration head 0011)
  - `frontend/src/test/account.test.ts`

## 7. Kalan riskler

- **R-LS1:** Bir MCP çağrısı timeout ya da erişim hatası verirse mevcut davranış tüm batch'i `unavailable` sayar. Tamamlanmış çağrıların span'leri success görünür ama `collect` özeti `unavailable` der. Fallback semantiği değiştirilmedi.
- **R-LS2:** LangGraph conditional edge'i (`needs_extra`) kendi küçük run'ını üretir. Çıktısı yalnız sonraki düğüm adı; karar ve nedeni `extra_decision`'dadır. Bastırmak LangGraph'ın private API'sini gerektirir.
- **R-LS3:** Analiz sonrası correlation query'leri (`_correlate`: yavaş trace'ler, Davis problem'leri) graf dışında çalışır; span'leri yoktur ve platform kaydında `langsmith_span` işareti taşımazlar.
- **R-LS4:** Allowlist yeni bir alana genişletilirse (`SAFE_SCALARS`/`SAFE_NESTED`/`SAFE_META`) sızıntı testleri güncellenmelidir. Varsayılan "gönderme"dir.

## 8. Deploy (2026-11-25)

APP_VM'ye deploy edildi. Rollback image'ları `:pre-ls`. `alembic_version` = 0011, servisler healthy. Kimliksiz `GET /api/investigations/{id}/trace` → 401. Frontend bundle'ında "LangSmith span" bağlantısı var. Container'da `SafeTracer` yükleniyor ve allowlist çalışıyor (`trace_safe({"question": …})` → yalnız `_omitted`). Gerçek LangSmith arayüzündeki kontroller (bölüm 5) açık.
