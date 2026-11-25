# Agent Trace sayfası

Ana menü › Platform › **Agent Trace** (`/agent-trace`, detay `/agent-trace/<run_id>`). Bir agent çalıştırmasının
LangGraph node / state / stream olaylarını, LLM çağrılarını ve Pydantic doğrulamasını tek zaman çizelgesinde gösterir.
LangSmith'ten bağımsızdır: LangSmith'e allowlist gider, burada platform DB'sinde **maskeli** tam kopya tutulur.

## 1. Mimari

| Katman | Dosya | Görev |
|---|---|---|
| Kaydedici (DB'siz) | `sre-agent/src/airlab_agent/agent_trace.py` | `TraceRecorder`, `AgentTraceCallback`, `TracedChatAnthropic`, `safe_copy`, `state_diff` |
| Graph entegrasyonu | `graph.py` `run_analysis(trace_recorder=…)`, `chat.py` `_run_graph` | Kaydedici varsa `stream_mode=["updates","debug"]`; yoksa davranış aynı (`invoke` / `ainvoke`) |
| Kalıcı kayıt | `sre_platform/services/agent_trace.py` | Thread-safe kuyruk → 0.3 sn'de bir toplu insert; `finish` kalan her şeyi yazar |
| Şema | migration `0014_agent_trace.py` | `sre.agent_trace_runs`, `sre.agent_trace_events` |
| API | `sre_platform/api/routes/agent_traces.py` | liste, detay, olay, node çalıştırması, LLM çağrısı, SSE |
| UI | `frontend/src/pages/AgentTracePage.tsx`, `components/JsonView.tsx` | liste, zaman çizelgesi, Before/After/Node Update/Diff, LLM/Pydantic panelleri |

Run türleri: `analysis` (investigation; `run_id` = investigation `run_id`) ve `chat_turn` (`chat-<kullanıcı mesajı id>`;
sürdürülen tur `-a<n>` ekiyle yeni girişim). Sohbet turunda başlayan analiz, `parent_run_id` ile sohbet run'ına bağlanır.

## 2. Hangi veri hangi katmandan

| Görünen veri | Kaynak | Not |
|---|---|---|
| State In (Before) | LangGraph `debug` stream `task.payload.input` | Node'a verilen gerçek girdi |
| Node Update | `debug` `task_result.payload.result` | Node'un döndürdüğü değer (reducer öncesi) |
| State Out (After) | `debug` `checkpoint.payload.values` | Graph adımı sonu, reducer'lar uygulanmış. Paralel adımda **"Bu adımın birleşik state'i"** |
| Geçiş | `checkpoint.payload.next` | Gözlemlenen sonraki node'lar |
| Routing kararı | Koşullu kenar fonksiyonunun callback çıktısı (`needs_extra`, `after_route`) | Node run'ının çocuğu, `seq:step` etiketli zincir |
| Stream olayları | `updates` + `debug` chunk'ları (mode, namespace, tür) | Payload tekrar edilmez; `data_ref` hangi kayda karşılık geldiğini söyler |
| LLM Request (Framework payload) | `on_chat_model_start`: roller + mesajlar, invocation params, structured output şeması | |
| Provider Request (Provider payload) | `TracedChatAnthropic._get_request_payload` dönüşü | API anahtarı client'tadır, payload'da yoktur |
| LLM Response (Framework) | `on_llm_end`: mesaj, tool_calls, finish_reason, usage | |
| Provider Response | `TracedChatAnthropic._format_output` girdisi (`Message.model_dump`) | Yalnız streaming olmayan çağrı |
| Streaming chunk'ları | `on_llm_new_token` | Sıralı, en fazla 400 chunk; fazlası sayılır |
| Parser Input / Validated Output | `PydanticOutputParser` runnable'ının callback girdi/çıktısı | `with_structured_output(..., method="json_schema")` |
| Validation Error | Parser hatasının exception zincirindeki `pydantic.ValidationError.errors()` | Alan yolu (`loc`) korunur; sonraki retry/fallback bağlantısı |
| Fallback | Açık işaret (`agent_trace.mark`) | Sohbet yönlendirmesi LLM'den kural tabanlıya düştüğünde |

## 3. Kaydedilemeyen / gözlemlenemeyen veri

* **Streaming çağrının sağlayıcı yanıtı** (sohbet cevabı `answer`): sağlayıcı SSE olayları tek JSON oluşturmaz →
  "Kaydedilemedi"; Framework chunk'ları kaydedilir.
* **SDK içi HTTP retry'leri** (anthropic client `max_retries`): görünmez; uygulama düzeyindeki retry'ler ayrı deneme
  numarasıyla görünür.
* **Sahte / sağlayıcı dışı model** (testlerdeki `FakePort`, kural tabanlı yönlendirme): LLM olayı yoktur.
* **Routing**: koşullu kenar fonksiyonu callback üretmezse (ör. sabit kenar) yalnız `next` gösterilir; kaynak node'un
  kararı "Gözlemlenemedi" değildir, geçişin kendisi gözlemlenir.
* **Ham telemetri** (`results.*.records`): bilinçli olarak kopyalanmaz → `{_records_omitted, count, fields}`.
  MCP Çağrıları sayfasıyla aynı karar; agent'ın gerçek state'i değişmez.

## 4. Maskeleme ve boyut

`safe_copy` olay oluşturulurken (depoya, canlı yayına ve loga ulaşmadan önce) çalışır ve **kopya** üzerinde çalışır:

1. JSON'a çevirme (mesajlar, Pydantic modelleri, tarih),
2. ham telemetri `records` → özet,
3. hassas anahtar adları (`authorization`, `api_key`, `password`, `secret`, `token`, `cookie`…) → `***`,
4. `dql` / `dqlQueryString` → DQL şablonu (değerler `"…"`),
5. tüm stringlerde mevcut redaksiyon kuralları (`redact(…, "trace")`: Dynatrace/Anthropic/LangSmith token, JWT, private
   key, bearer, PII),
6. sınırlar: string 20 000 karakter, liste/dict 200 öğe, olay başına 200 KB; aşılırsa kırpılır ve `truncated` işaretlenir.

Maskeleme hatası olursa veri **yazılmaz** (`_capture_failed`), olay sayılır. UI'daki "kopyala" sunucudaki maskeli JSON'u
kopyalar.

## 5. Kararlılık

* Kayıt/maskeleme/yazma hatası agent'ı durdurmaz; `capture_errors` ve `lost_events` run satırında, log satırında
  yalnız run, sıra aralığı ve hata tipi (payload yok).
* `sequence` kaydedicide kilitle verilir (paralel node'larda da boşluksuz, kararlı).
* Kayıt kapalı: `SRE_AGENT_TRACE=0`.
* Kayıt durumu: `complete`, `truncated` (kırpılmış payload), `partial` (kayıp olay / yakalama hatası / yarıda kalma),
  `failed` (hiç olay yazılamadı), `none` (kayıt öncesinden kalma çalıştırma: "Bu çalıştırma için trace verisi
  kaydedilmemiş").
* `running` kalan run 3 saat sonra "yarıda kaldı" gösterilir (backend ve worker ayrı süreçler olduğu için açılışta toplu
  işaretleme yapılmaz).

## 6. API ve yetki

Tümü `observability:read` (SRE / admin). `chat_turn` run'ları konuşma gibi yalnız sahibine ve admin'e açıktır
(yetkisiz → 404); analiz run'ları investigation'lar gibi ekip geneli.

| Uç | İçerik |
|---|---|
| `GET /api/agent-traces` | Liste (kayıtlı run'lar + trace'i olmayan investigation'lar `recorded:false`), filtre: kind, status, trigger, q, recorded |
| `GET /api/agent-traces/{run_id}` | Run, olay metadata'sı (payload'sız), alt/üst run, LangSmith linki |
| `GET …/events/{event_id}` | Tek olayın maskeli payload'ı |
| `GET …/node-execs/{exec_id}` | Before / Node Update / After / Diff / geçişler |
| `GET …/calls/{call_id}` | LLM çağrısı + bağlı parser olayları; validation error'da `followup` (retry / fallback / yok) |
| `GET …/stream` | SSE; `id` = sequence, `Last-Event-ID` ile kaldığı yerden; run bitince `trace_done` |

Canlı takip, sohbetteki mevcut SSE istemcisini (`services/sse.ts` `streamRun`) kullanır; yeniden bağlanmada tekrar gelen
olaylar `seq` ve `event_id` ile elenir. Bitmiş trace sayfa yenilenince DB'den yeniden açılır.

## 7. Saklama

Retention kuralı `agent_trace_runs`: LLM Monitoring süresi (`llm_runs_days`); olaylar `ON DELETE CASCADE`. Investigation
silinince analiz trace'i de silinir.

## 8. Deploy ve geri alma

Migration 0014 yalnız iki yeni tablo ekler (mevcut veriye dokunmaz). Geri alma: `alembic downgrade 0013` + önceki imaj.
