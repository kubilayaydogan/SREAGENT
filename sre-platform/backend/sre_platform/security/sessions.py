"""Sunucu tarafı oturumlar (sre.auth_sessions).

Bir access token ancak `jti`'sine karşılık gelen oturum satırı (1) iptal edilmemiş, (2) mutlak süresi dolmamış,
(3) idle süresi aşılmamış ve (4) aynı kullanıcıya aitse geçerlidir. Fail-closed: oturum bulunamazsa token reddedilir.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuthSession

TOUCH_INTERVAL = timedelta(seconds=60)          # last_seen_at en fazla dakikada bir yazılır


def new_id() -> str:
    return secrets.token_urlsafe(24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create(s: AsyncSession, user_id: uuid.UUID, expires_at: datetime, *, ip: str | None,
                 user_agent: str | None, sid: str | None = None) -> AuthSession:
    row = AuthSession(id=sid or new_id(), user_id=user_id, expires_at=expires_at, ip=(ip or None) and ip[:64],
                      user_agent=(user_agent or None) and user_agent[:256], created_at=_now(), last_seen_at=_now())
    s.add(row)
    await s.flush()
    return row


async def validate(s: AsyncSession, sid: str | None, user_id: uuid.UUID, idle_minutes: int) -> tuple[bool, str]:
    """(geçerli mi, sebep). Geçerliyse last_seen_at güncellenir (dakikada bir)."""
    if not sid:
        return False, "no_session"
    row = await s.get(AuthSession, sid)
    if row is None or row.user_id != user_id:
        return False, "unknown_session"
    now = _now()
    if row.revoked_at is not None:
        return False, f"revoked:{row.revoked_reason or '-'}"
    if row.expires_at <= now:
        return False, "expired"
    if idle_minutes > 0 and now - row.last_seen_at > timedelta(minutes=idle_minutes):
        return False, "idle_timeout"
    if now - row.last_seen_at > TOUCH_INTERVAL:
        row.last_seen_at = now
        await s.flush()
    return True, "ok"


async def is_active(sm, sid: str | None, idle_minutes: int = 0) -> bool:
    """Uzun ömürlü bağlantılar (SSE) için ayrı transaction'da yeniden kontrol (last_seen güncellenmez)."""
    if not sid:
        return False
    async with sm() as s:
        row = await s.get(AuthSession, sid)
        if row is None or row.revoked_at is not None or row.expires_at <= _now():
            return False
        return True


async def revoke(s: AsyncSession, sid: str, reason: str) -> bool:
    res = await s.execute(update(AuthSession).where(AuthSession.id == sid, AuthSession.revoked_at.is_(None))
                          .values(revoked_at=_now(), revoked_reason=reason[:32]))
    return (res.rowcount or 0) > 0


async def revoke_user(s: AsyncSession, user_id: uuid.UUID, reason: str, *, keep: str | None = None) -> int:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep:
        q = q.where(AuthSession.id != keep)
    res = await s.execute(q.values(revoked_at=_now(), revoked_reason=reason[:32]))
    return res.rowcount or 0


async def list_active(s: AsyncSession, user_id: uuid.UUID) -> list[AuthSession]:
    now = _now()
    q = (select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None),
                                   AuthSession.expires_at > now).order_by(AuthSession.created_at.desc()))
    return list((await s.execute(q)).scalars())


async def purge_expired(s: AsyncSession, older_than_days: int = 30) -> int:
    from sqlalchemy import delete
    cut = _now() - timedelta(days=older_than_days)
    res = await s.execute(delete(AuthSession).where(AuthSession.expires_at < cut))
    return res.rowcount or 0
