# Guardrails: Güvenlik Modeli

## İlkeler

1. **Deterministik karar.** Güvenlik kararı yalnız kural motoru tarafından verilir. LLM classifier yoktur
   (`classifier.mode` yalnız `deterministic`).
2. **Mandatory policy zayıflatılamaz.** DB constraint'i (`NOT mandatory OR mode='enforce'`), servis doğrulaması ve
   engine'in mandatory'leri `disabled` olsa bile çalıştırması üç ayrı katmandır.
3. **Fail-closed sabit kategoriler.** `tool`, `mcp`, `data_protection`, `memory`, `execution`, `action` evaluator hatasında
   engeller; bu ayar API'den değiştirilemez (422).
4. **Rol bağlamdan gelir.** RBAC kararında rol koşu bağlamından (`EvalContext.user_role`) okunur; tool argümanı veya
   kullanıcı metni rolü değiştiremez. Kullanıcısız platform koşuları (schedule, sistem) `system` rolüyle çalışır.
5. **Güvenilmeyen veri talimat değildir.** MCP yanıtı ve memory kayıtları güvenilmeyen dış veri kabul edilir; içlerindeki
   talimat benzeri ifadeler etkisizleştirilir, doğrulanmamış güvenilmeyen kaynaklı memory kaydı bağlama girmez.
6. **Kayıtta ham secret yok.** Snapshot, `matched` ve `reason` alanları maskelenmiş değer içerir; snapshot boyut sınırlıdır
   ve `capture_snapshots=false` ile tamamen kapatılabilir.
7. **Uydurma veri yok.** FP / FN oranları yalnız etiketli veriden (triage `false_positive` / `resolved` etiketleri ve
   etiketli Policy Tester koşuları) hesaplanır; etiket yoksa `null` döner, UI "—" gösterir.

## Tehdit → kontrol eşlemesi

| Tehdit | Kontrol |
|---|---|
| Direct prompt injection | `input_prompt_injection` (before_agent) |
| Indirect injection (MCP / memory) | `mcp_indirect_injection`, `memory_read_injection`, `llm_system_prompt_protection` |
| Secret / PII sızıntısı | `llm_request_redaction`, `llm_output_sensitive`, `mcp_sensitive_masking`, `output_sensitive`, `checkpoint_protection`, `StreamGuard` |
| Yazma / yıkıcı işlem | `mcp_read_only`, `tool_dangerous_operation`, `tool_denylist`, `action_approval` |
| Yetkisiz tool | `tool_allowlist`, `mcp_server_allowlist`, `tool_rbac`, `mcp_connector_authorization` |
| Tenant sızıntısı | `mcp_tenant_isolation`, `memory_read_tenant` |
| Kaynak tüketimi / döngü | `exec_limits`, `exec_loop_detection`, `exec_concurrency`, `mcp_rate_limit`, `llm_call_budget`, `tool_call_budget` |
| Memory poisoning | `memory_write_validation`, `memory_write_auth`, `memory_read_poisoning` |
| Yanıltıcı analiz | `evidence_integrity`, `evidence_sufficiency`, `evidence_window`, `risk_score_integrity`, `output_unsupported_claim` |
| Acil durum | `exec_kill_switch` (global / agent bazında) |

## Human approval

* İstek `action_hash` ile bağlanır: `sha256(tenant, agent, action, resource, params)`. Onay yalnız aynı parametrelerle
  ve **tek kez** kullanılabilir (`consumed_at`, replay → 409).
* Dört göz: isteyen kişi kendi isteğini onaylayamaz (403 `four_eyes`). Karar `approval:decide` (admin) ister; ayrıca isteğin `required_role` değerinin altındaki kullanıcı karar veremez.
* Reddetmede gerekçe zorunludur (422). İstek `approval_timeout_minutes` sonunda `expired` olur.
* Kullanım anında policy yeniden değerlendirilir (`recheck_failed`); onay verildikten sonra policy sıkılaşmışsa aksiyon
  yine engellenir.
* Mevcut agent salt okunurdur; varsayılan kurulumda yazma yeteneği olan tool yoktur. Approval akışı yazma yeteneği olan
  bir connector eklendiğinde devreye girer.

## Yetkiler

| Permission | Rol | Kapsam |
|---|---|---|
| `observability:read` | sre+ | Overview, logs, analytics, policy listesi, effective view, ihlal / approval listesi, config okuma |
| `guardrail:triage` | sre+ | İhlal triage (durum, atama, not), Policy Tester (örnekler, çalıştırma, geçmiş) |
| `approval:decide` | admin | Approval onay / red / iptal |
| `audit:read` | sre+ | Audit kayıtları ve zincir doğrulaması |
| `guardrail:manage` | admin | Policy oluşturma / değiştirme / clone / rollback / kapsam, config, kill switch |

## Audit

`guardrail_audit_events` append-only hash zinciridir: her kayıt `hash = sha256(prev_hash + içerik)`. UPDATE / DELETE /
TRUNCATE trigger ile engellenir; retention budaması yalnız `SET LOCAL sre.guardrail_audit_prune='on'` içeren
retention transaction'ında yapılabilir. `GET /api/guardrails/audit/verify` zinciri baştan hesaplar ve ilk bozuk `seq`
değerini döner. Policy, config, kill switch, triage ve approval kararlarının hepsi audit'e yazılır.

## Bilinen sınırlar

* Tespit kural tabanlıdır; yeni / gizlenmiş injection kalıplarını kaçırabilir (FN). Etiketli test seti ile ölçülmelidir.
* Rate limit penceresi süreç içidir; birden fazla backend replikasında replika başına uygulanır.
* Streaming maskeleme kelime sınırında çalışır; boşluk içeren kalıplar (Authorization header, kart grupları) tamamlanana
  kadar tutulur. Çok uzun boşluksuz parça `hold` × 4 karakteri aşınca maskelenerek iletilir.
