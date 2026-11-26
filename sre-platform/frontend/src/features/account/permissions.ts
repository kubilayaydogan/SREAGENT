/** Permission kodlarının arayüz etiketleri (backend: sre_platform/security/permissions.py). */
export const PERM_LABEL: Record<string, string> = {
  "record:read": "Kayıtları görüntüleme (investigation, incident, RCA, bilgi tabanı)",
  "agent:run": "Agent çalıştırma (sohbet, manuel investigation, resume)",
  "incident:write": "Incident, not, known issue, RCA üretimi",
  "approval:request": "Onay isteme / iptal",
  "approval:decide": "Onay kararı verme",
  "settings:read": "Settings görüntüleme",
  "settings:write": "Settings değişikliği, rollback, test, run-now",
  "audit:read": "Audit log görüntüleme",
  "observability:read": "LLM observability",
  "connector:manage": "MCP connector yönetimi",
  "llm:manage": "LLM profili ve model yönetimi",
  "secret:manage": "Secret yönetimi",
  "memory:delete": "Checkpoint / memory kaydı silme",
  "memory:write": "Memory kaydı oluşturma, düzenleme, arşivleme, verify",
  "memory:approve": "Procedural memory onayı, memory export",
  "middleware:manage": "Agent middleware yapılandırması (etkinlik, sıra, kapsam, parametre)",
  "knowledge:manage": "Runbook yönetimi",
  "guardrail:manage": "Guardrail policy, kapsam, rollback ve yapılandırma (kill switch dahil)",
  "guardrail:triage": "Guardrail ihlal yönetimi ve Policy Tester",
  "user:manage": "Kullanıcı ve oturum yönetimi",
};

export const ROLE_LABEL: Record<string, string> = { admin: "Yönetici", sre: "SRE", viewer: "İzleyici" };
