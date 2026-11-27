# Guardrails: Policy Referansı

## Kavramlar

| Alan | Değerler | Açıklama |
|---|---|---|
| `mode` | `monitor`, `enforce`, `disabled` | monitor: karar kaydedilir, uygulanmaz. Mandatory policy her zaman enforce çalışır. |
| `action` | `allow`, `warn`, `redact`, `sanitize`, `block`, `require_approval`, `quarantine`, `terminate`, `retry` | Evaluator'ın izin verdiği alt küme kullanılabilir (`/api/guardrails/evaluators` → `actions`). `retry`: çağıran desteklemiyorsa engel olarak uygulanır. |
| `severity` | `low`, `medium`, `high`, `critical` | İhlal önceliği ve bildirim minimum severity filtresi. |
| `stages` | 14 aşama | `before_agent`, `node_start`, `node_end`, `llm_request`, `llm_response`, `tool_request`, `mcp_request`, `tool_response`, `mcp_response`, `memory_read`, `memory_write`, `checkpoint`, `final_output`, `after_agent` |
| `priority` | 1–1000 | Küçük değer önce çalışır; dönüştüren aksiyonlar sırayla zincirlenir. |
| `assignments` | scope + value | `global`, `tenant`, `environment`, `agent`, `mcp_server`, `tool`, `graph_node`. Mandatory policy global kapsamdan çıkarılamaz. |
| `bound_to` | ör. `middleware:redaction` | Kural başka katmanda uygulanır; engine o katmanın kararını kaydeder. |

### Mandatory kuralları (backend doğrular, UI yalnız gösterir)

* `mode` her zaman `enforce`; `disabled` / `monitor` yapılamaz, `status` `active` kalır.
* Aksiyon `allow` veya `warn`'a zayıflatılamaz.
* Global kapsam kaldırılamaz.
* Parametreler değiştirilebilir (şema sınırları içinde); her değişiklik yeni sürüm ve audit kaydı üretir.

### Karar birleştirme (deny-overrides)

`terminated` > `blocked` > `approval_required` > `redacted` / `sanitized` > `warned` > `passed`.
Bir aşamada birden fazla policy tetiklenirse en güçlü karar kazanır; `error` kararı fail-closed kategorilerde engel
olarak, fail-open kategorilerde kayıt olarak işlenir.

## Evaluator türleri

| Tür | Kategori | Ne yapar |
|---|---|---|
| `input_length` | input | Girdi karakter sınırı (`max_chars`). |
| `invalid_input` | input | Kontrol / görünmez karakterleri temizler (sanitize). |
| `input_schema` | input | Custom agent input şeması (JSON Schema alt kümesi). |
| `prompt_injection` | input | Talimat geçersiz kılma, rol ele geçirme, system prompt sızdırma, policy atlatma kalıpları; skor `min_score` threshold'u ile. |
| `unsupported_request` | input | Salt okunur agent'a eylem isteği (restart, delete …) — uyarı. |
| `context_size` | model_context | Tahmini girdi token'ı (karakter/4) sınırı. |
| `system_prompt_protection` | model_context | İstek: veri içine gömülü prompt sınırlayıcıları / rol etiketleri etkisizleştirilir (platformun kendi `<memory_data>` vb. sarmalayıcısı korunur). Yanıt: system prompt parçasının tekrarı (sızıntı). |
| `model_output_validation` | model_context | Boş / aşırı uzun model yanıtı. |
| `sensitive_data` | data_protection | Private key, API key, access token, JWT, parola, connection string, kart (Luhn), IBAN, TCKN, e-posta, telefon, IP; `types` ve `custom_patterns` ile. |
| `tool_denylist` | tool | Yasaklı tool adları / kalıpları. |
| `mcp_server_allowlist` | mcp | Yalnız izinli MCP server'lar. |
| `read_only_enforcement` | mcp | Yazma yeteneği olan tool / DQL ifadesi engellenir. |
| `tool_rbac` | tool | Tool → minimum rol; rol koşu bağlamından alınır, payload'dan asla. |
| `tenant_isolation` | mcp / memory | Kaynak / kayıt tenant'ı koşu tenant'ıyla aynı olmalı. |
| `dangerous_operation` | tool | Argümanlarda DELETE, DROP, `kubectl delete`, `rm -rf` … |
| `mcp_request_validation` | mcp | Argüman şeması ve boyutu. |
| `query_time_range` | mcp | DQL zaman aralığı sınırı (`max_days`). |
| `rate_limit` | mcp | Server başına dakikalık çağrı sınırı (süreç içi pencere). |
| `mcp_response_limits` | mcp | Kayıt sayısı / bayt sınırı; fazlası kesilir (sanitize). |
| `mcp_response_validation` | mcp | Yanıt biçimi. |
| `indirect_injection` | mcp / memory | Güvenilmeyen veri içindeki talimat benzeri ifadeler etkisizleştirilir veya kayıt karantinaya alınır. |
| `output_schema` | output | Structured output şema doğrulaması. |
| `unsupported_claim` | output | Evidence'a dayanmayan kesin ifadeler için not. |
| `evidence_integrity` | evidence | Atıf yapılan evidence ID'si var mı, provenance'ı geçerli mi. |
| `evidence_sufficiency` | evidence | Kullanılabilir sinyal yokken kesin root cause iddiası. |
| `evidence_window` | evidence | Evidence analiz penceresi dışında / bayat. |
| `risk_score_integrity` | evidence | Metindeki risk skoru / band deterministik skorla aynı olmalı; değilse "Düzeltme" notu. |
| `memory_write_auth` | memory | Memory yazma yetkisi (rol / kaynak). |
| `memory_poisoning` | memory | Yazma: talimat benzeri içerik reddedilir. Okuma: doğrulanmamış güvenilmeyen kayıt bağlama girmez. |
| `execution_limits` | execution | Tool / MCP / LLM çağrısı, node adımı ve süre sınırları (atomik sayaçlar). |
| `loop_detection` | execution | Aynı çağrının / node'un tekrar sayısı. |
| `concurrency_limit` | execution | Agent başına eşzamanlı koşu. |
| `kill_switch` | execution | Global veya agent bazında acil durdurma. |
| `action_risk` | action | Yazma yeteneği olan aksiyon → human approval (`action_hash` bağlı). |

Parametre şemaları çalışma anında `/api/guardrails/evaluators` yanıtındaki `params_schema` alanından okunur (tek kaynak:
evaluator'ın `Params` pydantic modeli).

## Yerleşik policy kataloğu

| Key | Ad | Kategori | Tür | Severity | Aksiyon | Mandatory | Aşamalar | Uygulayan | Not |
|---|---|---|---|---|---|---|---|---|---|
| `input_max_length` | Maximum input length | input | `input_length` | medium | block | hayır | before_agent | engine | Kullanıcı / schedule girdisi uzunluk sınırı. |
| `input_invalid` | Invalid input & sanitization | input | `invalid_input` | low | sanitize | hayır | before_agent | engine | Kontrol ve görünmez karakterler temizlenir. |
| `input_schema` | Input schema validation | input | `input_schema` | medium | block | hayır | before_agent | engine | Custom agent input şeması (tanımlıysa). |
| `input_prompt_injection` | Direct prompt injection | input | `prompt_injection` | high | block | hayır | before_agent | engine | Talimat geçersiz kılma, rol ele geçirme, sızdırma, policy atlatma. |
| `input_unsupported_request` | Unsupported request (action intent) | input | `unsupported_request` | low | warn | hayır | before_agent | engine | Agent salt okunur: eylem istekleri işaretlenir. |
| `llm_context_size` | Context size validation | model_context | `context_size` | medium | block | hayır | llm_request | engine | LLM'e giden tahmini girdi token'ı sınırı. |
| `llm_call_budget` | LLM call & token budget | model_context | `builtin` | high | block | evet | llm_request | middleware:call_limit | RunBudget (Settings › Agent). |
| `llm_request_redaction` | Secret redaction / PII masking (LLM input) | data_protection | `builtin` | critical | redact | evet | llm_request | middleware:redaction | LLM'e giden mesajlar DLP ile maskelenir. |
| `llm_system_prompt_protection` | System prompt protection | model_context | `system_prompt_protection` | high | sanitize | hayır | llm_request, llm_response | engine | Veri içindeki prompt sınırlayıcıları; yanıtta system prompt sızıntısı. |
| `llm_output_validation` | Model output validation | model_context | `model_output_validation` | low | warn | hayır | llm_response | engine | Boş / aşırı uzun model yanıtı. |
| `llm_output_sensitive` | Sensitive data in model output | data_protection | `sensitive_data` | high | redact | evet | llm_response | engine | Model yanıtındaki secret ve PII maskelenir. |
| `tool_allowlist` | Tool / MCP tool allowlist | tool | `builtin` | critical | block | evet | mcp_request | middleware:tool_allowlist | Yalnız izinli connector + tool. |
| `mcp_connector_authorization` | MCP connector authorization | mcp | `builtin` | critical | block | evet | mcp_request | policy:tool.call | Connector onayı, tool tanım hash'i (rug-pull). |
| `tool_args_dql` | Tool argument validation (DQL) | tool | `builtin` | high | block | evet | mcp_request | middleware:tool_param_validation | DQL salt okuma komutları, uzunluk. |
| `tool_call_budget` | Tool call budget | tool | `builtin` | high | block | evet | mcp_request | middleware:call_limit | Koşu başına MCP query sınırı. |
| `tool_denylist` | Tool denylist | tool | `tool_denylist` | high | block | evet | tool_request, mcp_request | engine | Listedeki tool hiçbir koşulda çağrılmaz. |
| `mcp_server_allowlist` | MCP server allowlist | mcp | `mcp_server_allowlist` | critical | block | evet | mcp_request | engine | Yalnız izinli MCP sunucuları. |
| `mcp_read_only` | Read-only policy enforcement | mcp | `read_only_enforcement` | critical | block | evet | tool_request, mcp_request | engine | Yalnız doğrulanmış salt-okuma adapter'ı; sunucu metadata'sı yetki vermez. |
| `tool_rbac` | Tool authorization (RBAC) | tool | `tool_rbac` | critical | block | evet | tool_request, mcp_request | engine | Tool risk sınıfı → rol. |
| `mcp_tenant_isolation` | Tenant isolation (MCP) | mcp | `tenant_isolation` | critical | block | evet | tool_request, mcp_request | engine | Koşu tenant'ı dışında kaynak yok. |
| `tool_dangerous_operation` | Dangerous operation detection | tool | `dangerous_operation` | high | block | evet | tool_request, mcp_request | engine | Argümanda yazma / yıkıcı işlem. |
| `mcp_request_validation` | MCP request validation | mcp | `mcp_request_validation` | medium | block | evet | tool_request, mcp_request | engine | Argüman şeması ve boyutu. |
| `mcp_query_time_range` | Query time range limit | mcp | `query_time_range` | medium | block | hayır | mcp_request | engine | DQL zaman aralığı sınırı. |
| `mcp_rate_limit` | MCP rate limiting | mcp | `rate_limit` | medium | block | hayır | mcp_request | engine | Sunucu başına dakikalık istek. |
| `action_approval` | Action risk / human approval | action | `action_risk` | critical | require_approval | evet | tool_request, mcp_request | engine | Yazma yetenekli eylem insan onayı ister. |
| `mcp_response_limits` | MCP response size & record limits | mcp | `mcp_response_limits` | high | sanitize | evet | mcp_response, tool_response | engine | Kayıt ve boyut sınırı. |
| `mcp_response_validation` | MCP response validation | mcp | `mcp_response_validation` | medium | block | evet | mcp_response, tool_response | engine | Yanıt biçimi. |
| `mcp_indirect_injection` | Indirect prompt injection (MCP data) | mcp | `indirect_injection` | high | sanitize | hayır | mcp_response, tool_response | engine | Güvenilmeyen veri içindeki talimatlar etkisizleştirilir. |
| `mcp_sensitive_masking` | Sensitive data masking (MCP data) | data_protection | `sensitive_data` | high | redact | hayır | mcp_response, tool_response | engine | MCP kayıtlarındaki secret değerleri persist / LLM öncesi maskelenir. |
| `output_schema` | Structured output validation | output | `output_schema` | medium | block | hayır | llm_response, final_output | engine | JSON / şema (tanımlıysa). |
| `output_sensitive` | Sensitive output / data leakage | data_protection | `sensitive_data` | critical | redact | evet | final_output | engine | Kullanıcıya giden çıktıda secret / PII. |
| `output_unsupported_claim` | Unsupported claim detection | output | `unsupported_claim` | low | sanitize | hayır | final_output | engine | Kesin ifade + evidence referansı yok. |
| `evidence_integrity` | Evidence existence / ID / provenance | evidence | `evidence_integrity` | high | sanitize | hayır | final_output | engine | Geçersiz evidence ID, provenance. |
| `evidence_sufficiency` | Missing evidence / unsupported root cause | evidence | `evidence_sufficiency` | high | sanitize | hayır | final_output | engine | Yetersiz evidence ile kesin kök sebep yok. |
| `evidence_window` | Evidence freshness & time window | evidence | `evidence_window` | medium | warn | hayır | final_output | engine | Pencere geçerliliği ve tazelik. |
| `risk_score_integrity` | Deterministic risk score integrity | evidence | `risk_score_integrity` | critical | sanitize | evet | final_output | engine | LLM risk skorunu değiştiremez. |
| `memory_write_auth` | Memory write authorization | memory | `memory_write_auth` | high | block | evet | memory_write | engine | Rol ve procedural memory koruması. |
| `memory_write_validation` | Memory validator (secret, PII, injection, duplicate) | memory | `builtin` | high | block | evet | memory_write | memory:validate_write | Memory Policy Manager kararları. |
| `memory_read_tenant` | Memory tenant isolation | memory | `tenant_isolation` | critical | quarantine | evet | memory_read | engine | Başka tenant kaydı bağlama girmez. |
| `memory_read_poisoning` | Memory poisoning (read) | memory | `memory_poisoning` | high | quarantine | evet | memory_read | engine | Doğrulanmamış güvenilmeyen kayıt bağlama girmez. |
| `memory_read_injection` | Indirect injection (memory) | memory | `indirect_injection` | high | sanitize | hayır | memory_read | engine | Memory içeriğindeki talimatlar etkisizleştirilir. |
| `checkpoint_protection` | Checkpoint data protection | data_protection | `sensitive_data` | critical | redact | evet | checkpoint | engine | Checkpoint'e secret yazılmaz. |
| `exec_kill_switch` | Kill switch | execution | `kill_switch` | critical | terminate | evet | before_agent, node_start, llm_request, mcp_request, tool_request | engine | Global / agent bazlı acil durdurma. |
| `exec_limits` | Execution limits | execution | `execution_limits` | high | terminate | evet | node_start, mcp_request, tool_request, llm_request | engine | Node, tool, LLM çağrısı ve süre sınırları (atomik sayaç). |
| `exec_loop_detection` | Agent loop detection | execution | `loop_detection` | high | terminate | evet | node_start, mcp_request, tool_request | engine | Tekrarlayan node / çağrı. |
| `exec_concurrency` | Concurrent execution limit | execution | `concurrency_limit` | medium | block | hayır | before_agent | engine | Agent başına eşzamanlı koşu. |
