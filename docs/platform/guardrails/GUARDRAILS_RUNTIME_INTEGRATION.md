# Guardrails: Runtime Entegrasyonu

Agent özel bir LangGraph `StateGraph` kullanır (`create_agent` yok). Guardrail hook'ları gerçek çağrı yollarına bağlıdır;
her hook `airlab_agent.guardrails.runtime` modülündeki bir fonksiyondur ve aktif koşuyu `RUN` contextvar'ından okur.
Koşu dışındaki (unbound) çağrılar `user_role="system"` bağlamıyla ve fallback engine ile değerlendirilir.

## Hook noktaları

| Aşama | Nerede | Fonksiyon | Etki |
|---|---|---|---|
| `before_agent` | `sre_platform/guardrails/runtime.py::start` (investigation, chat turn, custom agent) | `check("before_agent")` | Girdi doğrulama / injection / kill switch / concurrency; engelse koşu başlamaz |
| `node_start` / `node_end` | `graph.py` → `g.add_node(name, guard_node(name, fn))`; builder `engine._wrap` | `node_start`, `node_end` | Execution limit, loop detection, kill switch (çalışan koşu bir sonraki node'da durur) |
| `llm_request` | `middleware.py` (LLM çağrı zarfı, before_model) | `llm_request(call)` | Context size, prompt delimiter sanitize; LLM çağrı sayacı |
| `llm_response` | `middleware.py` (after_model; sync, async, streaming birleşimi) | `llm_response_text` | Çıktıda secret / PII, system prompt sızıntısı, boş yanıt |
| `mcp_request` / `tool_request` | `mcp_gateway.py` (`execute-dql` çağrısı öncesi) | `mcp_request(...)` | Server allowlist, read-only, RBAC, tenant, DQL time range, rate limit, approval; tool sayacı |
| `mcp_response` / `tool_response` | `mcp_gateway.py` (yanıt sonrası, kayıtlar agent'a girmeden) | `mcp_response(...)` | Boyut / kayıt sınırı, indirect injection sanitize, secret maskeleme |
| `memory_read` | `memory/observed.py::_guard_recall` | `memory_read(items)` | Tenant isolation, poisoning (provenance), indirect injection |
| `memory_write` | `memory/policy.py::_guardrail` | `memory_write(...)` | Yazma yetkisi ve talimat benzeri içerik |
| `checkpoint` | `memory/observed.py` (`put` / `aput`) | `checkpoint(data)` | Checkpoint'e secret / PII yazılmaz (fail-closed) |
| `final_output` | `graph.py::explain` (`_guard_explanation`), `chat.py::_guard_answer`, builder `n_result` | `final_output(text, facts)` | Evidence / risk tutarlılığı, hassas veri, schema; değişirse `answer_replaced` olayı |
| stream | `chat.py` respond ve builder streaming | `StreamGuard.feed / flush` | Token akışında kelime sınırında maskeleme |
| `after_agent` | `GuardSession.finish` | `check("after_agent", enforce=False)` + `finish` | Koşu özetinin kaydı |
| `tool.call` permission | `security/policy.py::decide` | `observe` | Mevcut permission kararı guardrail kaydına yansır |

## Akış örneği (investigation)

1. `investigation.run()` → `g_rt.start(kind="investigation", ...)`. `GuardrailBlocked` → investigation `FAILED`, olay
   akışına hata yazılır, graph çalışmaz.
2. Graph node'ları `guard_node` ile sarılıdır; her node'dan önce sayaçlar ve kill switch kontrol edilir.
3. Gateway her DQL çağrısından önce `mcp_request`, sonra `mcp_response` çağırır. Retry'lar `_send` içinde olduğundan
   loop detection çağrı başına bir kez sayar.
4. `explain` node'u LLM açıklamasını `final_output` ile değerlendirir. Engellenirse deterministik açıklamaya düşer
   (kaynak `deterministik (guardrail)`); skor ve evidence değişmez.
5. `finally` bloğunda `GuardSession.finish` çalışır, `RUN` contextvar'ı eski değerine döner.

## Chat

* `turn()` → `_gr_start`. Engellenirse LLM çağrılmaz; yanıt `answer_source="guardrail"`, intent `guardrail_blocked`
  olarak kaydedilir ve kullanıcıya "Bu istek bir güvenlik policy'si nedeniyle işlenmedi: …" mesajı döner.
* `respond` token akışını `StreamGuard` üzerinden iletir; kalıcı yanıt ve mesaj kaydı yalnız güvenli parçalardan oluşur.
* Tüm metin gerektiren policy'ler akış bitince `final_output` ile çalışır; metin değişirse `answer_replaced` olayı
  frontend'de canlı yanıtı düzeltilmiş metinle değiştirir (`runReducer`).

## Custom agent (Agent Builder)

`builder/engine.py::execute` koşuyu `kind="custom_agent"` ile başlatır (agent_id, input_schema, variables). Node
sarmalayıcı `GuardrailBlocked` istisnasını `AgentRunError(code, ...)` olarak çevirir; builder'ın mevcut hata sözleşmesi
korunur.

## Engine yükleme ve cache

`load_engine(sm)` aktif policy'leri ve assignment'ları okur, `guardrail_config.revision` değişmedikçe aynı
engine'i döndürür. DB okunamazsa son başarılı engine, o da yoksa yerleşik katalog kullanılır (`set_fallback`).
Policy veya config değişikliği `revision`'ı artırır; yeni koşular yeni snapshot'ı alır, çalışan koşu başladığı
snapshot ile devam eder.

## Performans

Evaluator'lar saf Python ve regex'tir; ağ çağrısı yapmaz. Tek değerlendirme tipik olarak 0.1 ms'nin altındadır. Ölçülen
değerler Overview'da latency dağılımı ve Analytics'te policy bazında p50 / p95 (histogram kovası üst sınırı, yaklaşık)
olarak görünür. `evaluation_timeout_ms` aşılırsa sonuç geçerli kalır, sağlık sayacına `timeout_exceeded` yazılır.
