# Agent Middleware

Platform › **Agent Middleware**. Rol SRE ve admin içindir. Okuma için `observability:read`, yapılandırma için
`middleware:manage` gerekir; `middleware:manage` yalnız admin'dedir.

## Mimari gerçeği

Agent, LangChain `create_agent` **kullanmaz**. İki özel LangGraph StateGraph vardır:

* Analiz: `airlab_agent/graph.py`
* Sohbet: `sre_platform/agent/chat.py`

Bu yüzden LangChain middleware hook'ları kendiliğinden çalışmaz. Aynı adlı hook'lar, gerçek çağrı yollarına açık
adapter'larla bağlanmıştır ve arayüzde "LangChain hook değil" diye etiketlenir.

| Hook | Bağlandığı gerçek çağrı yolu | Adapter |
|---|---|---|
| `before_agent` | `InvestigationRunner.run` ve `ChatOrchestrator.turn` / `resume` başlangıcı | custom run wrapper |
| `after_agent` | Aynı yerler. Yalnız normal tamamlanmada çalışır (finally değildir). Hata, iptal ve timeout run kökü kaydının `outcome` alanında görünür. | custom run wrapper |
| `before_model`, `wrap_model_call`, `after_model` | `Llm.interpret`, `Llm.explain`, `ClaudePort.route`, `ClaudePort.astream` | client wrapper |
| `wrap_tool_call` | `McpGateway._one_inner`. Bu, doğrudan MCP `tools/call` çağrısıdır; paralel çağrıların her biri ayrı zarftır. | MCP client wrapper |

Desteklenmeyenler arayüzde açıkça yazılıdır:

* Graph node wrapper yoktur. Node ayrıntısı Agent Trace'tedir.
* `create_agent` hook'ları yoktur.
* Response cache yoktur.
* Bekleyen politika kararı yoktur. Onaylar Approvals sayfasındadır.

### Sıralama semantiği

LangChain ile aynıdır:

* `before_*` hook'ları artan `order` ile çalışır.
* `wrap_*` hook'ları iç içedir: en küçük `order` en dıştadır.
* `after_*` hook'ları ters sırada çalışır.

Wrapper'lar `pre` (içeri girerken) ve `post` (dönerken) adımlarıyla yürür. Her kayıt üç alan taşır:

* `sequence`: run içi monoton sayaç.
* `parent_execution_id`: gerçek iç içelik.
* `depth`: derinlik.

Run Timeline zaman damgasına göre değil, bu alanlara göre kurulur.

### Run izolasyonu

Run'a özgü veri `airlab_agent.middleware.RUN` contextvar'ında (`RunMw`) ve çağrı başına `Call` nesnesinde tutulur.
Middleware instance'larında değişken durum yoktur. `asyncio.to_thread` ve `asyncio.run` context'i kopyalar; bu yüzden
analiz thread'i ve paralel MCP çağrıları doğru run'a yazar.

### Streaming

`ClaudePort.astream` chunk'ları aynen ve anında iletir. Yalnız chunk sayısı, ilk chunk zamanı, tamamlandı/kesildi
bilgisi kaydedilir; yanıt buffer'a alınmaz.

## Başlangıç middleware'leri

Mevcut kurallar çoğaltılmaz, çağrılır.

| Middleware | Tür | Hook | Kuralın asıl yeri | Zorunlu |
|---|---|---|---|---|
| `call_logging` | logging | agent, model, tool | — | hayır |
| `timing` | logging | wrap model/tool | — | hayır |
| `redaction` | redaction | before_model | `airlab_agent/redact.py` (DLP, sink=llm) | **evet** |
| `tool_allowlist` | authorization | wrap_tool_call | Allowlist parametresi; tool hash bütünlüğü `McpGateway._verify_tool` | **evet** |
| `tool_param_validation` | validation | wrap_tool_call | `mcp_gateway.validate_dql` (parametreler yalnız daha kısıtlayıcı olabilir) | **evet** |
| `call_limit` | limit | before_model, wrap_tool_call | `RunBudget` + `max_queries_per_run` (Settings › Agent) | **evet** |
| `retry_observer` | retry | wrap_tool_call | Gateway retry + Anthropic SDK `max_retries` | hayır |

**Retry yapılmaz, yalnız gözlemlenir.** Gateway, timeout ve erişilemezlik durumlarında aynı `call_id` ile retry eder;
yetki hatasında retry etmez. Anthropic SDK da kendi retry'ını yapar. İkinci bir retry katmanı deneme sayısını
katlayacağı için eklenmedi.

Engellenen tool **sunucuya gönderilmez**. Çağırana dönen sonuç önceki davranışla aynıdır:

* Reddedilen DQL → `rejected`
* Query budget doldu → `SKIPPED_BUDGET`
* LLM budget doldu → `LlmBudgetExceeded`

MCP Çağrıları sayfasında bu çağrılar "gönderilmedi" olarak görünür.

`validate_dql` gateway içinde son savunma hattı olarak da çağrılır. Kural aynıdır, ikinci bir kopya değildir.

## Hata politikaları

| Durum | Davranış |
|---|---|
| Telemetry (kayıt) hatası | Çağrı etkilenmez (fail-open). Kayıp sayılır ve loglanır. |
| Güvenlik middleware'i (`security=True`) hata verir | Çağrı **engellenir** (fail-closed). Kayıtta `error_code=middleware_error`. |
| Diğer middleware hata verir | Kayıtta `result=error`, çağrı devam eder. |
| Snapshot'ta zorunlu middleware kapalı görünür | Yine çalışır (backend'de kapatılamaz, runtime'da da atlanmaz). |

## Yapılandırma

* **Tanım** kodda durur (`REGISTRY`); arayüzden kod çalıştırılamaz.
* **Yapılandırma** iki tabloda tutulur:
  * `sre.middleware_configs`: güncel değer.
  * `sre.middleware_config_versions`: değişmez geçmiş.
* Kural ve doğrulamalar:
  * Parametreler middleware'in Pydantic şemasıyla backend'de doğrulanır. Arayüz formu aynı şemadan üretilir.
  * Optimistic concurrency: istek `version` taşır; farklıysa 409 döner.
  * Geçersiz parametre, aynı `order` çakışması, zorunlu middleware'i kapatma veya kapsamını daraltma → 422.
  * Her değişiklik `audit_events`'e yazılır (`section=middleware`). Reddedilen istek de ayrı transaction'da audit'lenir.
* **Ne zaman etkili olur:** run başında `run_config` snapshot alır (`rc.middleware`, ayrıca
  `investigations.config_snapshot.middleware`).
  * Çalışan run kendi snapshot'ıyla devam eder.
  * Değişiklik yeni run'lardan itibaren geçerlidir.
  * Her execution kaydı kullandığı gerçek `middleware_version` değerini taşır.
* Run dışındaki çağrılar (ör. watch poller) süreçte en son yüklenen yapılandırmayı kullanır ve kayıt üretmez.

## Execution kaydı ve gizlilik

* Tablo `sre.middleware_executions`. Kayıt türleri: `entry_type` = `agent` (run kökü), `call` (model/tool zarfı) veya
  `middleware` (hook yürütmesi).
* Varsayılan olarak **yalnız metadata** kaydedilir.
* Payload yakalama `Settings › agent_middleware.capture_payloads` ile açılır:
  * Önce ve sonra görünümü kayıttan önce maskelenir (`redact` + `safe_copy`).
  * Boyutu `max_payload_bytes` ile sınırlanır; aşan kayıt `truncated` işaretlenir.
  * İstemciye gönderilmeden önce yeniden maskelenir.
* MCP kayıtlarında ham DQL tutulmaz; MCP Çağrıları ile aynı güvenli `request_preview` kullanılır.
* Sohbet turu kayıtlarını yalnız sahibi ve admin görür; Agent Trace ile aynı kuraldır.
* Retention `execution_retention_days` ile belirlenir (varsayılan 30). Liste uçlarında sayfalama vardır.
* Ortak kimliklerle ilgili ekranlara bağlantı verilir, kayıtlar çoğaltılmaz:
  * `run_id` → Agent Trace
  * `tool_call_id` = MCP çağrı kaydı kimliği → MCP Çağrıları
  * Model zarfları → LLM Monitoring

## Dry-run

`POST /api/middleware/dry-run` kayıtlı bir middleware'in kararını sentetik girdiyle hesaplar:

* Gerçek LLM veya MCP çağrısı yapılmaz, kayıt yazılmaz, gerçek run budget'ına dokunulmaz.
* Sonuç bölümleri: karar, değişen alanlar, parametre hataları.
* `timing` ve `retry_observer` sardıkları gerçek çağrının sonucuna dayandığı için "dry-run desteklenmiyor" döner.
* `call_logging` "simulated" olarak işaretlenir.

## API

| Uç | Yetki |
|---|---|
| `GET /api/middleware/overview?range=` | observability:read |
| `GET /api/middleware/hooks` | observability:read |
| `GET /api/middleware/catalog`, `GET /api/middleware/catalog/{name}` | observability:read |
| `PATCH /api/middleware/catalog/{name}` (`version`, `enabled`, `order`, `params`, `scope`, `note`) | middleware:manage |
| `POST /api/middleware/reorder` | middleware:manage |
| `POST /api/middleware/dry-run` | observability:read |
| `GET /api/middleware/executions`, `GET /api/middleware/executions/{id}` | observability:read |
| `GET /api/middleware/runs`, `GET /api/middleware/runs/{run_id}/timeline` | observability:read |
| `GET /api/middleware/settings` (+ `/api/settings/agent_middleware`) | observability:read / settings:write |

## Bilinen sınırlar

* `span_id` kaydedilmez ("desteklenmiyor"); `trace_id` analizde investigation id, sohbette tur id'sidir.
* Run timeout'unda analiz thread'i arka planda sürerse, kapanıştan sonra üretilen kayıtlar yazılmaz.
* Watch poller'ın MCP çağrıları middleware kararlarından geçer ama run kaydı üretmez.
* Middleware'e özel `user_id` filtresi yalnız `chat_turn` için uygulanır; analiz run'ları ekip geneli görünür.
