"""Koşu başına LLM kullanım kaydı (P1.6). Sayaç `airlab_agent.llm.RUN_BUDGET`'tadır; koşu sonunda yazılır.

LangSmith kapalı / örneklenmemiş koşularda da tam kayıt tutulur. Sınır nedeniyle yapılmayan çağrılar `denied` satırı ve
`llm.budget_exceeded` audit olayı olarak yazılır. Yazma hatası koşuyu bozmaz (log'a düşer).
"""
from __future__ import annotations

import logging
import uuid

from airlab_agent.llm import RUN_BUDGET

from ..db.models import LlmUsage
from ..db.session import transaction
from . import audit

log = logging.getLogger("sre.llm_usage")


async def persist(sm, *, run_id: str, trigger: str | None, investigation_id: uuid.UUID | None = None,
                  conversation_id: uuid.UUID | None = None, actor: str = "system:agent") -> dict | None:
    b = RUN_BUDGET.get()
    if b is None:
        return None
    with b._lock:                                      # aynı bütçe iki kez yazılmaz (sohbet + iç investigation)
        start_c, start_d = getattr(b, "_flushed", (0, 0))
        calls, denied = list(b.calls[start_c:]), list(b.denied[start_d:])
        b._flushed = (len(b.calls), len(b.denied))
    if not calls and not denied:
        return b.summary()
    common = {"run_id": str(run_id)[:64], "trigger": trigger, "investigation_id": investigation_id,
              "conversation_id": conversation_id}
    try:
        async with transaction(sm) as s:
            for c in calls:
                s.add(LlmUsage(**common, task=c["task"][:48], model=(c["model"] or None),
                               input_tokens=c["input_tokens"], output_tokens=c["output_tokens"]))
            for t in denied:
                s.add(LlmUsage(**common, task=t[:48], denied=True))
            if denied:
                sm_ = b.summary()
                await audit.record(s, actor=actor, action="llm.budget_exceeded", section="llm",
                                   target=f"run:{common['run_id']}", result="denied", reason_code="llm_run_budget",
                                   detail=f"{len(denied)} LLM çağrısı yapılmadı ({', '.join(sorted(set(denied)))})",
                                   after={k: sm_[k] for k in ("calls", "tokens", "max_calls", "max_tokens")})
    except Exception:  # noqa: BLE001
        log.exception("LLM kullanım kaydı yazılamadı")
    return b.summary()
