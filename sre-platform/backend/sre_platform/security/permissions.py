"""Permission modeli (IAM-03, plan P1.2).

Rotalar rol yerine **permission** ister (`api/deps.require_perm`). Rol → permission eşlemesi mevcut rol davranışını
birebir korur (yetki genişlemesi/daralması yok); yeni bir rol ya da ince ayar bu tabloda yapılır.
Okuma uçları (`record:read`) kimliği doğrulanmış her kullanıcıya açıktır (tek organizasyon modeli, karar 1).
"""
from __future__ import annotations

from enum import Enum

from ..db.models import Role


class P(str, Enum):
    RECORD_READ = "record:read"                 # investigation / incident / RCA / bilgi tabanı okuma
    AGENT_RUN = "agent:run"                     # sohbet, manuel investigation, resume
    INCIDENT_WRITE = "incident:write"           # incident, not, known issue, RCA üretimi
    APPROVAL_REQUEST = "approval:request"
    APPROVAL_DECIDE = "approval:decide"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"           # bölüm değişikliği, rollback, testler, iş zamanlama
    AUDIT_READ = "audit:read"
    OBSERVABILITY_READ = "observability:read"
    CONNECTOR_MANAGE = "connector:manage"       # MCP connector kayıt / test / keşif
    LLM_MANAGE = "llm:manage"                   # LLM profilleri
    SECRET_MANAGE = "secret:manage"
    MEMORY_DELETE = "memory:delete"             # checkpoint thread silme, memory kaydı silme
    MEMORY_WRITE = "memory:write"               # memory kaydı oluşturma / düzenleme / arşiv / verify, procedural öneri
    MEMORY_APPROVE = "memory:approve"           # procedural memory onayı (four-eyes), memory export
    MIDDLEWARE_MANAGE = "middleware:manage"     # agent middleware yapılandırması (etkinlik, order, kapsam, parametre)
    KNOWLEDGE_MANAGE = "knowledge:manage"       # runbook
    USER_MANAGE = "user:manage"


_VIEWER = {P.RECORD_READ}
_SRE = _VIEWER | {P.AGENT_RUN, P.INCIDENT_WRITE, P.APPROVAL_REQUEST, P.SETTINGS_READ, P.AUDIT_READ,
                  P.OBSERVABILITY_READ, P.MEMORY_WRITE}
_ADMIN = set(P)

ROLE_PERMISSIONS: dict[Role, frozenset[P]] = {Role.VIEWER: frozenset(_VIEWER), Role.SRE: frozenset(_SRE),
                                              Role.ADMIN: frozenset(_ADMIN)}


def has(role: Role, perm: P) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, frozenset())


def matrix() -> list[dict]:
    """Arayüz / API için salt okunur görünüm."""
    return [{"permission": p.value, "roles": [r.value for r in Role if has(r, p)]} for p in P]
