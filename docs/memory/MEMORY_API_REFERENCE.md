# Agent Memory — API Reference

Tüm uçlar `/api/memory` altındadır, `Authorization: Bearer` ister. `agent` parametresi kayıtlı bir agent olmalıdır
(varsayılan `sre-agent`); bilinmeyen agent → 404. Hata gövdesi platform standardıdır:
`{"error", "message", "correlation_id", "details"}`. Policy reddi: `422`, `error = "memory_policy"`, `details` ihlal
kodları (`secret_detected`, `prompt_injection`, `untrusted_procedural`, `untrusted_self_verify`, `too_large`, `empty`,
`unknown_source`); içerik yanıtta tekrarlanmaz.

## Okuma (`observability:read` — SRE, admin)

| Metod | Yol | Açıklama |
|---|---|---|
| GET | `/agents` | Tenant'ın agent kayıtları |
| GET | `/overview?agent&range=1h\|24h\|7d\|30d` | Metric'ler, grafikler, backend health, metric tanımları |
| GET | `/health` | Checkpointer / Store / tablolar / publisher / vector index |
| GET | `/records?agent&memory_type&q&status&verification&backing&page&size` | Birleşik long-term liste (`by_backing` toplamları) |
| GET | `/records/{memory_id}` | Kayıt detayı (runbook ise bekleyen revizyonlar) — `MEMORY_READ` olayı |
| GET | `/records/{memory_id}/versions` | Versiyon geçmişi |
| GET | `/approvals` | Onay bekleyen procedural öneriler |
| GET | `/threads?agent&q&kind=chat\|analysis&page&size` | Checkpoint'i olan thread'ler |
| GET | `/threads/{thread_id}` | Mesajlar, context window, digest, tool çağrıları, linkler |
| GET | `/threads/{thread_id}/checkpoints?limit` | Checkpoint timeline (`next`, `tasks`, metadata) |
| GET | `/threads/{thread_id}/checkpoints/{checkpoint_id}?ns` | State values, parent/next/children, diff, pending writes |
| GET | `/threads/{thread_id}/compare?a&b` | İki checkpoint state farkı |
| GET | `/working` | Agent çalışmaları (çalışan üstte) |
| GET | `/working/{run_id}` | Working memory (Agent Trace State Out) |
| GET | `/retrievals?agent&run_id&origin&range&page&size` | Retrieval kayıtları |
| GET | `/retrievals/{id}` | Pipeline aşamaları, seçilen/dışlanan, injection, olaylar |
| POST | `/playground` | Salt okunur deneme araması (`exact`, `keyword`, `fingerprint`; `semantic`/`hybrid` → 422) |
| GET | `/strategies` | Uygulanan / uygulanmayan stratejiler |
| GET | `/events?agent&type&run_id&thread_id&memory_id&status&range&page&size` | Memory olayları |
| GET | `/events/stream?agent` | SSE canlı akış (`id` = olay zamanı µs, `Last-Event-ID` ile devam) |
| GET | `/events/{id}` | Olay + maskeli payload |
| GET | `/governance` | Kontroller, izinler, ihlaller, memory audit kayıtları |

## Yazma

| Metod | Yol | İzin | Açıklama |
|---|---|---|---|
| POST | `/records?agent` | `memory:write` | Semantic kayıt oluşturur; `procedural` → pending runbook önerisi; `episodic` → 422 |
| PATCH | `/records/{memory_id}?agent` | `memory:write` | `version` verilirse optimistic concurrency (uyuşmazsa 409); runbook → pending revizyon |
| POST | `/records/{memory_id}/archive\|restore\|verify\|reject_verification` | `memory:write` | Durum değişikliği (versiyonlanır) |
| POST | `/records/{memory_id}/approve\|reject` | `memory:approve` | Procedural karar; öneren karar veremez (403) |
| POST | `/records-delete/{memory_id}` | `memory:delete` | Gövde `{"confirm": "<memory_id>"}`; yalnız `mem:` kayıtları; versiyon geçmişi kalır |
| GET | `/export?agent&memory_type` | `memory:approve` | Maskeli JSON export (audit'lenir) |

Ayarlar: `GET/PATCH /api/settings/agent_memory` (mevcut Settings API, `settings:write`, revizyon + audit).

## Kimlik biçimleri

| Önek | Kaynak |
|---|---|
| `mem:<uuid>` | `sre.memory_records` |
| `ki:<uuid>` | `sre.known_issues` |
| `rb:<uuid>` | `sre.runbooks` |
| `ep:<investigation uuid>` | `sre.agent_episodes` |
| `st:<prefix>\|<key>` | `langgraph.store` (salt okunur) |

## Örnek

```http
POST /api/memory/records?agent=sre-agent
{"memory_type": "semantic", "title": "checkin → reservation", "content": "checkin-service reservation-service'i senkron çağırır",
 "category": "topology", "tags": ["checkin-service"]}

201 {"memory_id": "mem:…", "namespace": "sre-agent/semantic/topology", "source_trust": "trusted",
     "verification_status": "unverified", "version": 1, …}
```
