# Guardrails: Mimari

Guardrails, SRE Agent'ın ve Agent Builder ile oluşturulan custom agent'ların girdi, model context'i, tool / MCP çağrısı,
memory, checkpoint ve son çıktı aşamalarında çalışan **deterministik** güvenlik katmanıdır. Kararı kural motoru verir;
LLM hiçbir güvenlik kararı vermez (classifier modu yalnız `deterministic` olabilir).

## Bileşenler

| Katman | Konum | Görev |
|---|---|---|
| Model | `sre-agent/src/airlab_agent/guardrails/model.py` | `Policy`, `Assignment`, `EvalContext`, `Finding`, `Evaluation`, `Outcome`, `GuardrailBlocked`; aşama / kategori / aksiyon / mode sabitleri |
| Tespit | `guardrails/detect.py` | Secret / PII / injection kalıpları, maskeleme, normalizasyon, custom pattern doğrulaması (ReDoS koruması) |
| Evaluator'lar | `guardrails/evaluators.py` | Kayıtlı (`@register`) evaluator türleri; her biri `Params` (pydantic) şeması ve `evaluate()` |
| Engine | `guardrails/engine.py` | Aşama için uygulanabilir policy'leri seçer, sırayla çalıştırır, deny-overrides ile karar birleştirir, fail-open / fail-closed uygular |
| Katalog | `guardrails/catalog.py` | 46 yerleşik policy; DB'ye ulaşılamazsa engine bu kataloğla çalışır (fail-safe) |
| Runtime | `guardrails/runtime.py` | `RUN` contextvar, `GuardRun` (atomik sayaçlar), hook fonksiyonları (`check`, `mcp_request`, `memory_read` …), `guard_node` |
| Stream | `guardrails/stream.py` | Token akışında hassas veri maskeleme (`StreamGuard`) |
| Persistence | `sre-platform/backend/sre_platform/guardrails/` | `service.py` (policy / approval / violation / config / audit), `runtime.py` (engine yükleme, writer), `queries.py`, `tester.py`, `retention.py` |
| API | `sre_platform/api/routes/guardrails.py` | `/api/guardrails/*` |
| UI | `sre-platform/frontend/src/pages/GuardrailsPage.tsx` | 8 sekme: Overview, Policies, Evaluation Logs, Violations, Policy Tester, Human Approvals, Analytics, Settings |

## Karar akışı

1. Koşu başlarken (`g_rt.start`) aktif policy snapshot'ı yüklenir (config `revision`'a göre cache'li), `GuardRun`
   oluşturulur ve `before_agent` değerlendirilir. Kill switch veya engelleyici bir karar koşuyu hiç başlatmaz.
2. Her hook noktası `check(stage, payload)` çağırır. Engine o aşamaya ve bağlama (agent, environment, MCP server, tool,
   node) uyan policy'leri öncelik sırasıyla çalıştırır.
3. Kararlar **deny-overrides** ile birleşir: `terminated > blocked > approval_required > redacted / sanitized > warned > passed`.
   Dönüştüren aksiyonlar (redact / sanitize / quarantine) payload'ı bir sonraki policy'ye dönüştürülmüş haliyle aktarır.
4. `monitor` modundaki policy karar üretir ama uygulamaz (`enforced=false`). **Mandatory** policy her zaman `enforce`
   çalışır; DB'de `disabled` yazsa bile engine onu çalıştırır.
5. Evaluator hata verirse: `tool`, `mcp`, `data_protection`, `memory`, `execution`, `action` kategorilerinde **fail-closed**
   (sabit, değiştirilemez), `input`, `model_context`, `output`, `evidence` kategorilerinde Settings'teki `fail_mode`.
6. Engelleyici karar `GuardrailBlocked` istisnası olarak çağırana döner; çağıran katman (investigation, chat, builder)
   koşuyu kontrollü şekilde bitirir ve kullanıcıya policy mesajı gösterir.

## Kayıt modeli

* **Geçen (passed)** değerlendirmeler koşu başına `(policy, stage)` anahtarıyla toplanır: adet, toplam / max süre ve
  latency kovaları (0.1, 0.5, 1, 5, 10, 50, 100 ms ve üstü). Böylece yüksek hacimde tablo büyümez.
* **İhlaller** (passed dışı her karar) tek tek kaydedilir; snapshot (before / after) maskelenmiş ve boyut sınırlıdır.
* İhlaller `fingerprint` (tenant + policy + agent + tool + kod) ile `guardrail_violations` tablosunda tekilleştirilir;
  açık bir ihlal varken tekrarı `occurrences` sayısını artırır (partial unique index `uq_guardrail_violation_open`).
* Yazma işlemi `GuardrailWriter` kuyruğu ile ana akıştan ayrıdır; kuyruk doluysa kayıp `writer_lost` sağlık sayacına
  yazılır, agent akışı bloklanmaz. Kill switch durumu writer pump'ı içinde ~3 sn'de bir okunur.

## Veritabanı (migration 0019)

| Tablo | İçerik |
|---|---|
| `guardrail_policies` | Aktif tanım; `mandatory ⇒ mode='enforce'` check constraint'i |
| `guardrail_policy_versions` | Her değişiklikte tam snapshot + checksum (rollback yeni sürüm yaratır) |
| `guardrail_policy_assignments` | Kapsam: global → tenant → environment → agent → mcp_server → tool → graph_node |
| `guardrail_evaluations` | Değerlendirme kayıtları (toplu passed + tekil ihlal) |
| `guardrail_violations` | Triage edilebilir ihlal kayıtları ve geçmişi |
| `guardrail_approvals` | Human approval istekleri (action_hash, expiry, consume) |
| `guardrail_test_runs` | Policy Tester geçmişi ve etiketleri |
| `guardrail_config` | Tekil (id=1) yapılandırma + `revision` |
| `guardrail_audit_events` | Append-only hash zinciri (UPDATE / DELETE / TRUNCATE trigger ile engellenir) |

## Mevcut katmanlarla ilişki

Bazı kurallar zaten başka bir katmanda uygulanıyordu (middleware call limit, redaction, tool allowlist, DQL doğrulaması,
`tool.call` permission policy'si, memory validator). Bunlar katalogda `bound_to` ile işaretlidir: engine onları yeniden
değerlendirmez, o katmanın kararını `observe()` ile kaydeder. Böylece tek bir yerden görünürlük sağlanır, mevcut davranış
ve API sözleşmeleri değişmez.

Deterministik risk skoru, graph akışı ve analiz çıktısı değişmedi: `risk_score_integrity` yalnız LLM açıklamasının risk
skorunu / bandını yanlış aktardığı durumda metne bir "Düzeltme" notu ekler; skor hesabına dokunmaz.
