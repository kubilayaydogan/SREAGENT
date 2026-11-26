# Context Windows

Platform › **Context Windows** (`/context-windows`). Her gerçek LLM çağrısının context window kullanımını gösterir.
Gözlem amaçlıdır: prompt, tool seçimi ve agent akışı değişmez; kayıt hatası agent çalışmasını kesmez.
Okuma yetkisi `observability:read` (SRE, admin). Sohbet turu kayıtlarını yalnız sahibi ve admin görür.

## Ölçüm nerede yapılır

| Katman | Ne ölçülür |
|---|---|
| `airlab_agent.middleware.run_model_sync / run_model_async / stream_model` | Çağrı başlangıcı ve sonu, durum, final usage. Tüm model çağrıları buradan geçer: `Llm.interpret`, `Llm.explain`, `ClaudePort.route`, `ClaudePort.astream` (sohbet yanıtı ve RCA yönetici özeti). |
| `TracedChatAnthropic._get_request_payload` | Sağlayıcıya **gerçekten gönderilen** payload: girdi dağılımı, `max_tokens`, Agent Trace `call_id`. |
| Anthropic SDK httpx event hook'ları (`context_window.instrument`) | SDK içi retry'ların her HTTP denemesi (`x-stainless-retry-count`), HTTP durumu, `request-id`. |
| `context_window.note_context` (chat `_inject`, follow-up, route / interpret, explain) | Dahil edilen, özetlenen ve çıkarılan içeriğin referansları ve gerekçesi. |

Middleware'in engellediği çağrı (ör. LLM budget doldu) sağlayıcıya gitmez ve kayıt üretmez.

## Kayıt

* Tablo `sre.llm_context_calls` (migration 0017).
* Satır başına bir HTTP denemesi tutulur.
  * Aynı mantıksal çağrının denemeleri `logical_call_id` ile bağlıdır.
  * Önceki denemeler `status=retried` ile yazılır.
* `call_seq` run içindeki çağrı sırasıdır. Aynı node iki kez LLM'e giderse iki ayrı satır oluşur.
* Ham prompt, yanıt ve MCP payload'ı saklanmaz. Yalnız şunlar tutulur:
  * token sayıları,
  * kaynak türleri,
  * güvenli referanslar: kayıt kimlikleri, analiz alan adları, `<memory_data>` gibi blok adları.
* Retention `Settings › Retention › llm_runs_days` ile yönetilir (LLM Monitoring ile aynı süre).
* Yazma süreç başına bir kuyruk ve pump ile yapılır (API ve worker). Yazılamayan satır kayıp sayılır ve loglanır.

## Değerlerin kaynağı

| Değer | Kaynak |
|---|---|
| Girdi / çıktı token | Sağlayıcı `usage`. Streaming'de final usage: LangChain, kümülatif `message_delta`'dan üretir. |
| cache okuma / yazma | Sağlayıcı (`cache_read_input_tokens`, `cache_creation_input_tokens`). Girdi toplamının **alt kalemidir**; LangChain `input_tokens` değeri bunları içerir. |
| thinking (reasoning) | Sağlayıcı `output_tokens_details.thinking_tokens`. Çıktının alt kalemidir. Bildirilmezse bilinmiyor. |
| Context window kapasitesi | Anthropic Models API `max_input_tokens`. Arka planda alınır ve 6 saat önbelleklenir. Alınamazsa **bilinmiyor**; yüzde hesaplanmaz. |
| Modelin çıktı sınırı | Models API `max_tokens`. |
| Ayrılan çıktı bütçesi | Platformun gönderdiği `max_tokens` (Settings › LLM profili). |
| Bileşen dağılımı | **Tahmini**: gönderilen payload üzerinden karakter / 4 (Agent Memory ile aynı yöntem). |
| Diğer / sağlayıcı ek yükü | Sağlayıcı girdi toplamı − bileşen tahminleri. Tahmin toplamı aşarsa 0 gösterilir ve fark ayrıca yazılır. |

Doluluk formülleri (Anthropic'te giriş ve çıkış birlikte context window'a sayılır):

* Planlanan doluluk = (tahmini girdi + ayrılan çıktı) / kapasite.
* Gerçekleşen doluluk = (bildirilen girdi + bildirilen çıktı) / kapasite.

Değer yoksa NULL tutulur, 0 ile doldurulmaz.

### Bileşenler

Bileşenler şu kurallarla ayrılır:

* `system` → Sistem talimatları.
* Son kullanıcı mesajının serbest metni → Kullanıcı mesajı.
* Önceki mesajlar → Geçmiş.
* `<analysis_data>` → MCP / tool sonuçları. Bu, Dynatrace MCP sonuçlarından türetilen analiz özetidir; ham MCP çıktısı gönderilmez.
* `<memory_data>`, `<history_data>`, `<rca_data>` → RAG / memory.
* `<context_data>` anahtar bazında ayrılır:
  * `previous_answer` → Geçmiş.
  * `earlier_turns` → Özet.
  * `focus` → State.
  * `last_investigation` → RAG.
* Route system prompt'undaki konuşma odağı → Node'dan aktarılan state.
* `tools` → Tool tanımları (agent'ta tool bağlı değil).
* `output_config.format` → Structured output şeması.

## Durumlar

| Durum | Anlamı |
|---|---|
| `ok` | Çağrı tamamlandı. |
| `retried` | SDK bu HTTP denemesini retry etti (429 / 529 / 5xx / bağlantı). Bu deneme için usage bildirilmez. |
| `context_limit` | İki kaynaktan biri: 400 `prompt is too long` (`prompt_too_long`) ya da `stop_reason=model_context_window_exceeded`. |
| `error` | Diğer hatalar (timeout, rate limit, bağlantı…). |
| `cancelled` | Streaming kullanıcı tarafından kesildi. |

## API

| Uç | Açıklama |
|---|---|
| `GET /api/context-windows/overview?range=` | Toplam çağrı ve HTTP denemesi, en yüksek ölçülen doluluk, context limit hatası, retry, usage eksik. |
| `GET /api/context-windows/facets?range=` | Node ve model listeleri. |
| `GET /api/context-windows/calls` | Filtreler: `range`, `run_id`, `node`, `model`, `status`. Sıralama: `sort=time` / `fill` / `sequence`. Sayfalama: `limit`, `offset`. |
| `GET /api/context-windows/calls/{call_id}` | Detay: bütçe, kullanım, dağılım, kararlar, denemeler, bağlantılar. |

Bağlantılar:

* Agent Trace: `/agent-trace/{run_id}?call={agent trace call_id}`. Çağrı düzeyinde eşleşme yoksa yalnız run bağlantısı verilir.
* Agent Middleware Run Timeline.
* LLM Monitoring.

## Bilinen sınırlar

* Bileşen token'ları tahmindir. Sağlayıcı tokenizer'ı ya da token counting API'si çağrılmaz; ek bir dış istek yapılmaz ve içerik yeniden gönderilmez.
* Kapasite, modelin ilk çağrısında henüz alınmamışsa o çağrı için "Bilinmiyor" görünür. Önceden yükleme platform açılışında yapılır.
* Bu özellikten önceki run'ların kaydı yoktur.
* Retry edilen denemelerin girdi dağılımı yoktur; payload yalnız son denemeye bağlanır.
* Context kararları yalnız mevcut hazırlama akışının ürettiği yerlerde vardır: memory injection, follow-up, konuşma geçmişi penceresi, analiz özeti. Diğer çağrılarda "Ölçülmedi" yazar.
