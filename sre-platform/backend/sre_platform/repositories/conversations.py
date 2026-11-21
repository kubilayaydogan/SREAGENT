"""Konuşmalar ve mesajlar. Her sorgu kullanıcıya göre süzülür (başkasının konuşması 'bulunamadı' döner)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact, redact_text

from ..db.models import Conversation, Message, MessageRole
from .base import NotFound, Page, order_by, paginate, ts_match, ts_rank


def new_thread_id() -> str:
    return f"conv-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:12]}"


async def create(s: AsyncSession, user_id: uuid.UUID, title: str, application_id: str | None = None) -> Conversation:
    c = Conversation(user_id=user_id, title=redact_text(title.strip())[:200] or "Yeni konuşma",
                     thread_id=new_thread_id(), application_id=application_id)
    s.add(c)
    await s.flush()
    return c


async def get(s: AsyncSession, conv_id: uuid.UUID, user_id: uuid.UUID | None) -> Conversation:
    """user_id None = yönetici bağlamı (sahiplik kontrolü üst katmanda yapılmıştır)."""
    stmt = select(Conversation).where(Conversation.id == conv_id)
    if user_id is not None:
        stmt = stmt.where(Conversation.user_id == user_id)
    c = (await s.execute(stmt)).scalar_one_or_none()
    if c is None:
        raise NotFound("konuşma bulunamadı")
    return c


async def list_(s: AsyncSession, user_id: uuid.UUID, *, q: str | None = None, archived: bool = False,
                page: int = 1, size: int = 30, sort: str | None = None) -> Page[Conversation]:
    stmt = select(Conversation).where(Conversation.user_id == user_id, Conversation.archived == archived)
    if q:
        hit = select(Message.conversation_id).where(ts_match(Message.tsv, q))
        stmt = stmt.where((Conversation.id.in_(hit)) | Conversation.title.ilike(f"%{_like(q)}%", escape="\\"))
    stmt = order_by(stmt, sort, {"updated_at": Conversation.updated_at, "created_at": Conversation.created_at,
                                 "title": Conversation.title}, "-updated_at")
    return await paginate(s, stmt, page, size)


def _like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def rename(s: AsyncSession, conv_id: uuid.UUID, user_id: uuid.UUID, title: str | None = None,
                 archived: bool | None = None) -> Conversation:
    c = await get(s, conv_id, user_id)
    if title:
        c.title = redact_text(title.strip())[:200]
    if archived is not None:
        c.archived = archived
    await s.flush()
    return c


async def add_message(s: AsyncSession, conv_id: uuid.UUID, role: MessageRole, content: str,
                      meta: dict[str, Any] | None = None, investigation_id: uuid.UUID | None = None) -> Message:
    m = Message(conversation_id=conv_id, role=role, content=redact_text(content), meta=redact(meta or {}),
                investigation_id=investigation_id)
    s.add(m)
    await s.execute(update(Conversation).where(Conversation.id == conv_id).values(updated_at=func.now()))
    await s.flush()
    return m


async def messages(s: AsyncSession, conv_id: uuid.UUID, *, page: int = 1, size: int = 200) -> Page[Message]:
    stmt = select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at, Message.id)
    return await paginate(s, stmt, page, size)


async def recent_messages(s: AsyncSession, conv_id: uuid.UUID, n: int) -> list[Message]:
    stmt = (select(Message).where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(n))
    return list(reversed((await s.execute(stmt)).scalars().all()))


async def search_messages(s: AsyncSession, user_id: uuid.UUID, q: str, limit: int = 10) -> list[tuple[Message, str]]:
    """Kullanıcının kendi konuşmalarında tam metin araması (hafıza: 'son araştırmamızda ne bulmuştuk?')."""
    stmt = (select(Message, Conversation.title).join(Conversation)
            .where(Conversation.user_id == user_id, ts_match(Message.tsv, q))
            .order_by(ts_rank(Message.tsv, q).desc(), Message.created_at.desc()).limit(limit))
    return [(m, t) for m, t in (await s.execute(stmt)).all()]
