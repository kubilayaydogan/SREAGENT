# Agent Memory — Implementation

## Modüller (`sre-platform/backend/sre_platform/memory/`)

| Dosya | Sorumluluk |
|---|---|
| `registry.py` | Tenant / agent kaydı, namespace önekleri, `default_namespace()` |
| `events.py` | `MemoryEventPublisher` (thread-safe kuyruk + 1 sn pump), `MemCtx` korelasyonu, `suppressed()` |
| `policy.py` | Yazma politikası: secret, prompt injection, poisoning, PII redaction, boyut, `content_hash`; `retrievable()` |
| `context.py` | Token tahmini (karakter / 4), context budget, referans çıkarımı |
| `observed.py` | `ObservedMemoryService` (retrieval pipeline + olaylar + LangSmith span), `ObservedPostgresSaver` / `ObservedAsyncPostgresSaver`, `record_injection()` |
| `manager.py` | Birleşik long-term görünüm, CRUD, versiyonlama, onay (four-eyes), export, playground, auto extraction, retention |
| `observability.py` | Overview metric'leri ve grafikleri, backend health, thread / checkpoint explorer, working memory, olay ve retrieval listeleri |
| `api/routes/memory.py` | `/api/memory/*` REST + SSE |

## Agent entegrasyonu (davranış korunarak)

* `agent/runtime.py`: checkpointer'lar `Observed*Saver` ile, `MemoryService` `ObservedMemoryService` ile değiştirildi.
  Alt sınıflar yalnız olay üretir; hata yeniden fırlatılır (yutulmaz).
* `agent/chat.py`: `recall` çağrısı `bind(node="recall")` içinde; `_inject()` her memory bloğu için budget uygular
  (0 = sınırsız, blok aynen) ve `record_injection()` çağırır. Short-term kapalıysa history `[]`, focus `None`.
* `agent/investigation.py`: `run()` bağlamı (`CTX`) kendi koşusuna bağlar ve sonunda geri yükler (sohbet bağlamı
  sızmaz). `analysis_memory_enabled` açıksa rapor bittikten **sonra** `report.memory_context` eklenir; risk score,
  mechanism ve LLM girdisi değişmez (test: `test_default_flags_analysis_unchanged`).
* `repositories/episodes.py`: episode yazımı `MEMORY_WRITE` üretir; `THRESHOLD` contextvar'ı ile similarity eşiği
  istek başına ayarlanır (global değişken değiştirilmez); arşivlenen episode retrieval dışıdır.
* `repositories/knowledge.py`: agent yalnız `approval_status = approved` ve arşivlenmemiş runbook'ları görür.
* `worker.py` retention job'ı memory retention'ını da çalıştırır (expire, arşiv/olay temizliği).

## Feature flag'ler (Settings › `agent_memory`)

Varsayılanlar önceki davranışı birebir korur.

| Ayar | Varsayılan | Etki |
|---|---|---|
| `memory_enabled` | true | Kapalıysa olay üretimi durur; agent memory okuması `long_term_enabled`/`short_term_enabled` ile birlikte değerlendirilir |
| `memory_observability_enabled` | true | Olay / retrieval kaydı |
| `short_term_enabled` | true | Sohbet geçmişi ve focus LLM'e gider |
| `long_term_enabled`, `semantic_enabled`, `episodic_enabled`, `procedural_enabled` | true | Retrieval kapsamını daraltır (namespace_filter) |
| `memory_retrieval_enabled` | true | Kapalıysa search aşaması atlanır |
| `context_injection_enabled` | true | Kapalıysa memory bloğu LLM isteğine eklenmez (kayıt `not_injected`) |
| `analysis_memory_enabled` | **false** | Analiz sonrası `report.memory_context` (evidence değildir) |
| `auto_extraction_enabled` | **false** | Doğrulanmış episode'dan unverified topology olgusu |
| `top_k` / `similarity_threshold` | 5 / 0.35 | Retrieval parametreleri |
| `max_retrieved_records` / `max_context_tokens` | 0 / 0 | 0 = sınırsız (önceki davranış) |
| `memory_retention_days` / `memory_event_retention_days` / `default_ttl_days` | 0 / 90 / 0 | Temizlik |
| `require_validation`, `require_evidence_verification`, `pii_redaction_enabled`, `poisoning_protection_enabled` | true | Policy |
| `secret_redaction_enabled`, `require_procedural_approval` | true (kilitli) | Kapatılamaz |

## Veri kaynağı → katman eşlemesi

| Arayüz alanı | Kaynak |
|---|---|
| Overview sayıları | `sre.memory_records` + `known_issues` + `runbooks` + `agent_episodes` + `langgraph.store` (ayrık toplam) |
| Reads / Writes / Latency | `sre.memory_events` (`duration_ms`, gerçek ölçüm) |
| Retrieval success / empty / latency / tokens | `sre.memory_retrievals` |
| Storage | `pg_total_relation_size()` |
| Working Memory | `sre.agent_trace_events` (State Out), kopya yok |
| Short-Term | `langgraph.checkpoints` + `sre.conversations` + `sre.messages` + `sre.trace_links` |
| Checkpoints | LangGraph `aget_state_history` / `get_state_history`, `aget_tuple` |
| Governance | `sre.memory_events` (ihlaller), `sre.audit_events` (section=memory) |

## Testler

* Backend: `tests/test_memory_module.py` (policy, registry izolasyonu, CRUD + versiyon + optimistic concurrency,
  four-eyes, yetki, pending runbook'un agent'a görünmemesi, chat retrieval + olaylar + untrusted dışlama, thread
  sahipliği, checkpoint explorer'ın olay üretmemesi, varsayılan ayarlarla analiz regresyonu, playground salt okunurluğu,
  retention, event deposu hatası, eşzamanlı yazma).
* Frontend: `src/test/agentmemory.test.tsx`.

## Sınırlamalar

* Vector / embedding yok → Semantic ve Hybrid retrieval, query embedding, reranking yok.
* Consolidation yok (`MEMORY_CONSOLIDATED` üretilmez); replay / fork / resume yok (`MEMORY_CHECKPOINT_RESTORED` üretilmez).
* Token sayıları tahmindir (karakter / 4); gerçek tokenizer kullanılmaz.
* Working Memory, Agent Trace olaylarından okunur; Agent Trace kaydı olmayan eski çalışmalar görünmez.
* Prompt injection tespiti kural tabanlıdır; her saldırıyı yakalamaz.
* Tek tenant, tek environment (lab); Agent Builder yok.
* `observed.py` checkpoint sarmalayıcıları yalnız `put`/`get_tuple` (sync) ve `aput`/`aget_tuple` (async) üzerinden
  olay üretir; `put_writes` (pending writes) olay üretmez.
