"""Ajan çalıştırma olayları (SSE'ye aktarılır). `agent_thinking` yalnız kullanıcıya gösterilebilecek KISA ilerleme
özetidir; modelin iç düşünce zinciri hiçbir zaman olay olarak üretilmez."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

EventType = Literal["investigation_started", "tool_started", "tool_completed", "tool_failed", "agent_thinking", "token",
                    "evidence_found", "investigation_completed", "error", "message_saved", "memory_recalled",
                    "suggestions", "done", "agent_node", "agent_result", "answer_replaced"]  # agent_*: Agent Builder

# Agent Activity ekranındaki adımlar (sinyal → adım)
STEPS = [
    ("start", "Araştırma başladı"),
    ("app_metrics", "Uygulama metric'leri"),
    ("db_metrics", "PostgreSQL metric'leri"),
    ("logs", "Error / lock logları"),
    ("traces", "Distributed Tracing (trace)"),
    ("infra", "Host ve container kaynakları"),
    ("extra", "Ek evidence"),
    ("correlate", "Evidence correlation"),
    ("memory", "Geçmiş kayıtlar"),
    ("rca", "RCA / yanıt üretimi"),
    ("save", "Kaydetme"),
]
SIGNAL_STEP = {"http": "app_metrics", "hikari": "app_metrics", "jvm_gc": "app_metrics", "jvm_heap": "app_metrics",
               "db_conn": "db_metrics", "db_sessions": "db_metrics", "lock_waiters": "db_metrics",
               "collector": "db_metrics", "db_stats": "db_metrics", "statements": "db_metrics",
               "pg_server": "db_metrics", "pg_io": "db_metrics",
               "edges": "logs", "pg_lock_logs": "logs", "spans": "traces", "hosts": "infra", "containers": "infra",
               "edges_detail": "extra", "top_statements": "extra", "slow_traces": "traces", "problems": "extra"}
NODE_MESSAGE = {"plan": "Analiz penceresi ve karşılaştırma (baseline) penceresi belirlendi.",
                "collect": "Dynatrace verileri toplandı.",
                "analyze": "Evidence ilişkilendirildi ve risk score hesaplandı.",
                "extra_evidence": "Mechanism'i doğrulamak için ek evidence toplandı.",
                "explain": "Bulgular özetlendi.",
                "finalize": "Analiz raporu hazırlandı."}


class Emitter(Protocol):
    async def __call__(self, type_: EventType, data: dict[str, Any]) -> None: ...


def event(type_: EventType, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": type_, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "data": data}


class ListEmitter:
    """Testler için olay toplayıcı."""

    def __init__(self):
        self.events: list[dict] = []

    async def __call__(self, type_, data):
        self.events.append(event(type_, data))

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]
