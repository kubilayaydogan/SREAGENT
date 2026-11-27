# Guardrails: API Referansı

Taban yol: `/api/guardrails`. Kimlik doğrulaması bearer token ile; hata gövdesi platform standardıdır:
`{"error": "<code>", "message": "...", "correlation_id": "...", "details": ...}`.

## Okuma (`observability:read`, sre+)

| Metot | Yol | Açıklama |
|---|---|---|
| GET | `/overview?range=1h\|24h\|7d\|30d` | Toplamlar, karar dağılımı, latency (avg / max / p95 ≈), histogram, policy durumu, bekleyen approval, açık ihlaller, zaman serisi, en çok tetiklenen policy / agent, son kritik ihlaller, writer sağlığı |
| GET | `/analytics?range=` | Kategori / aşama × karar, policy bazında oran ve latency, monitor vs enforce, etiketli FP / FN oranları (`null` = etiket yok) |
| GET | `/evaluators` | Evaluator kataloğu (`params_schema`, izinli `actions`), aşamalar, kategoriler, mode / severity / scope listeleri |
| GET | `/policies?category=&q=&status=` | `{"items": Policy[]}` |
| GET | `/policies/{id veya key}` | Policy + `versions[]` + `evaluator` |
| GET | `/effective?agent_id=&environment=&mcp_server=&tool=&node=` | Bağlam için geçerli policy'ler, `inherited_from`, `scope_values`, `rule` |
| GET | `/evaluations?range=&decision=&policy=&category=&stage=&agent=&run_id=&q=&enforced=&limit=&offset=` | `{"total", "items", "limit", "offset"}` |
| GET | `/evaluations/{id}` | Detay: `matched`, `thresholds`, `changed_fields`, maskeli `snapshot.before/after`, `latency_buckets`, aynı koşunun `run_evaluations` listesi |
| GET | `/violations?status=&severity=&category=&agent=&q=&range=&limit=&offset=` | Tekilleştirilmiş ihlaller (`occurrences`, `history`) |
| GET | `/approvals?status=&limit=&offset=` | Approval istekleri (süresi dolanlar okuma anında `expired` olur) |
| GET | `/config` | `{"revision", "data", "updated_by", "updated_at", "fixed"}` |

## Yönetim (`guardrail:manage`, admin)

| Metot | Yol | Gövde | Not |
|---|---|---|---|
| POST | `/policies` | `PolicyIn` (key, name, category, type, severity, mode, stages, action, priority, params, assignments, note) | 201; yeni policy varsayılan modu Settings › `optional_defaults.mode` |
| PATCH | `/policies/{pid}` | `PolicyIn` alanları + `version` (zorunlu) | Sürüm uyuşmazlığı → 409 `version_conflict`; mandatory zayıflatma → 422 |
| POST | `/policies/{pid}/clone` | `{"key", "name"?}` | `bound_to` policy kopyalanamaz (409) |
| POST | `/policies/{pid}/versions/{v}/rollback` | `{"note"?}` | Eski içerikle **yeni** sürüm yaratır |
| PUT | `/policies/{pid}/assignments` | `{"version", "assignments": [{"scope", "value"}]}` | Mandatory global kapsamı korunur |
| PUT | `/config` | `{"revision", "data"}` | Revision uyuşmazlığı → 409; sabit fail-closed kategorisi / LLM classifier → 422 |
| POST | `/kill-switch` | `{"revision", "global": bool, "agents": [...], "reason"}` | Gerekçe zorunlu (≥3 karakter) |

## Triage ve test (`guardrail:triage`, sre+)

| Metot | Yol | Gövde | Not |
|---|---|---|---|
| PATCH | `/violations/{id}` | `{"status"?, "assigned_to"?, "note"?}` | Geçişler: open → acknowledged / investigating / resolved / false_positive; resolved / false_positive → open (aynı fingerprint açık kayıt varsa 409) |
| GET | `/test/samples` | — | Hazır senaryolar |
| POST | `/test` | `{"stage", "text"?, "data"?, "context"?, "policy_keys"?, "draft"?, "expected_decision"?, "is_attack"?, "save"}` | Dry-run: tool / LLM çağrılmaz, değerlendirme kaydı ve approval yazılmaz; yalnız test geçmişi |
| GET | `/test/history?limit=` | — | Son test koşuları |

## Approval (`approval:decide`, admin)

| Metot | Yol | Gövde | Hatalar |
|---|---|---|---|
| POST | `/approvals/{id}/approve` | `{"reason"?}` | 403 `four_eyes`, 403 `role_required`, 409 `not_pending` |
| POST | `/approvals/{id}/reject` | `{"reason"}` | 422 `reason_required` |
| POST | `/approvals/{id}/cancel` | — | Yalnız isteyen kişi |

## Audit (`audit:read`, sre+)

| Metot | Yol | Açıklama |
|---|---|---|
| GET | `/audit?limit=` | Son audit kayıtları (`seq`, `actor`, `action`, `target`, `detail`, `before`, `after`, `hash`) |
| GET | `/audit/verify` | `{"ok", "checked", "broken_at"}` |

## Olay akışı (SSE)

Chat ve custom agent akışına bir olay türü eklendi: `answer_replaced` `{"text", "source"?}`. Son çıktı guardrail'i metni
değiştirdiğinde frontend canlı yanıtı bu metinle değiştirir. Engellenen koşuda mevcut `error` / `token` olayları
kullanılır; mevcut olay sözleşmeleri değişmedi.
