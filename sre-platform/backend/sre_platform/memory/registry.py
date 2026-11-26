"""Agent ve tenant kaydı (Agent Builder uyumluluk arayüzü).

Platformda Agent Builder YOK: tek agent gömülü SRE Agent'tır (`sre-agent`). Bu modül, kullanıcı tarafından
oluşturulacak agent'lar için gereken sözleşmeyi tanımlar (bağımsız namespace, ayar, policy, istatistik) ama
Agent Builder implementasyonu içermez. Tüm memory query'leri `tenant_id` + `agent_id` ile filtrelenir; namespace tek
başına güvenlik sınırı sayılmaz.

Tenant: platform tek tenant'lıdır (`default`). Kullanıcının tenant'ı `tenant_of()` ile tek noktadan çözülür; çok
tenant'a geçişte yalnız bu fonksiyon ve kullanıcı modeli değişir.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_TENANT = "default"
EMBEDDED_AGENT = "sre-agent"
MEMORY_TYPES = ("semantic", "episodic", "procedural")


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    name: str
    kind: str                                   # embedded | user_defined
    tenant_id: str = DEFAULT_TENANT
    namespace_prefix: str = ""
    settings_section: str | None = None         # memory ayarlarının bölümü (agent başına)
    shared_spaces: tuple[str, ...] = field(default_factory=tuple)   # açıkça izin verilen paylaşılan alanlar
    description: str = ""

    def owns(self, namespace: str) -> bool:
        return namespace == self.namespace_prefix or namespace.startswith(self.namespace_prefix + "/")


_AGENTS: dict[str, AgentDescriptor] = {
    EMBEDDED_AGENT: AgentDescriptor(
        agent_id=EMBEDDED_AGENT, name="SRE Agent (embedded)", kind="embedded", namespace_prefix=EMBEDDED_AGENT,
        settings_section="agent_memory",
        description="Platforma gömülü SRE Agent: analiz graph'ı (investigation) ve sohbet graph'ı"),
}


def tenant_of(_user=None) -> str:
    return DEFAULT_TENANT


def agents(tenant_id: str = DEFAULT_TENANT) -> list[AgentDescriptor]:
    return [a for a in _AGENTS.values() if a.tenant_id == tenant_id]


def get(agent_id: str, tenant_id: str = DEFAULT_TENANT) -> AgentDescriptor | None:
    a = _AGENTS.get(agent_id)
    return a if a is not None and a.tenant_id == tenant_id else None


def register(desc: AgentDescriptor) -> None:
    """Gelecekteki Agent Builder entegrasyonu için. Aynı kimlik ikinci kez kaydedilemez; namespace önekleri
    çakışamaz (bir agent diğerinin namespace'ini sahiplenemez)."""
    if desc.agent_id in _AGENTS:
        raise ValueError(f"agent zaten kayıtlı: {desc.agent_id}")
    for a in _AGENTS.values():
        if a.owns(desc.namespace_prefix) or desc.owns(a.namespace_prefix):
            raise ValueError("namespace öneki başka bir agent ile çakışıyor")
    _AGENTS[desc.agent_id] = desc


def unregister(agent_id: str) -> None:
    if agent_id == EMBEDDED_AGENT:
        raise ValueError("gömülü agent kaldırılamaz")
    _AGENTS.pop(agent_id, None)


def default_namespace(agent_id: str, memory_type: str, category: str | None = None) -> str:
    a = _AGENTS[agent_id]
    return "/".join(x for x in (a.namespace_prefix, memory_type, category) if x)
