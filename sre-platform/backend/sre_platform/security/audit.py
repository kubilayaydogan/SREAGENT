"""Güvenlik ve yapılandırma audit kayıtları (sre.audit_events; DB tetikleyicisiyle append-only).

* `record(s, ...)`: çağıranın transaction'ında yazar — işlem geri alınırsa audit de geri alınır (değişiklik olmadıysa
  kayıt da olmaz).
* `record_separate(sm, ...)`: kendi transaction'ında yazar — reddedilen / başarısız işlemler (401, 403, 429, policy
  DENY) için; isteğin transaction'ı geri alınsa da kalır. Yazılamazsa hata loglanır, istek cevabı değişmez.

before/after/detail redakte edilir; secret değerleri audit'e girmez. correlation_id, source_ip, session_id istek
bağlamından otomatik doldurulur.
"""
from __future__ import annotations

import logging

from airlab_agent.redact import redact, redact_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import correlation_id
from ..db.models import AuditEvent
from . import context

log = logging.getLogger("sre.audit")


def _event(*, actor: str, action: str, section: str, target: str | None = None, revision: int | None = None,
           before: dict | None = None, after: dict | None = None, result: str = "ok", detail: str | None = None,
           actor_type: str | None = None, run_id: str | None = None, reason_code: str | None = None,
           source_ip: str | None = None, session_id: str | None = None) -> AuditEvent:
    cid = correlation_id.get()
    return AuditEvent(
        actor=(actor or "-")[:64], action=action[:48], section=section[:32], target=(target or None) and target[:128],
        revision=revision, before=redact(before, "audit") if before is not None else None,
        after=redact(after, "audit") if after is not None else None, result=result[:16],
        detail=(redact_text(detail, "audit")[:2000] if detail else None),
        actor_type=actor_type or ("system" if actor.startswith("system") else
                                  "anonymous" if actor.startswith("anonymous") else
                                  "service" if context.api_key.get() or actor.startswith("apikey:") else "user"),
        source_ip=source_ip or context.client_ip.get(), correlation_id=None if cid == "-" else cid,
        session_id=session_id or context.session_id.get() or (f"apikey:{context.api_key.get()}" if context.api_key.get()
                                                              else None),
        run_id=run_id, reason_code=reason_code)


async def record(s: AsyncSession, **kw) -> None:
    s.add(_event(**kw))
    await s.flush()


async def record_separate(sm, **kw) -> None:
    try:
        async with sm() as s:
            async with s.begin():
                s.add(_event(**kw))
    except Exception:  # noqa: BLE001 — audit yazılamaması isteğin cevabını değiştirmez; ama görünür olmalı
        log.exception("audit kaydı yazılamadı", extra={"audit_action": kw.get("action")})
