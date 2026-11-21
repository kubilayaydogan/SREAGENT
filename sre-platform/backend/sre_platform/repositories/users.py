from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import DUMMY_HASH, hash_password, verify_password
from ..db.models import Role, User
from .base import Conflict, NotFound, Page, order_by, paginate


async def get(s: AsyncSession, user_id: uuid.UUID) -> User:
    u = await s.get(User, user_id)
    if u is None:
        raise NotFound("kullanıcı bulunamadı")
    return u


async def by_username(s: AsyncSession, username: str) -> User | None:
    return (await s.execute(select(User).where(func.lower(User.username) == username.lower()))).scalar_one_or_none()


async def create(s: AsyncSession, username: str, display_name: str, role: Role, password: str) -> User:
    u = User(username=username, display_name=display_name, role=role, password_hash=hash_password(password))
    s.add(u)
    try:
        await s.flush()
    except IntegrityError:
        raise Conflict(f"kullanıcı adı kullanımda: {username}") from None
    return u


async def authenticate(s: AsyncSession, username: str, password: str) -> User | None:
    u = await by_username(s, username)
    if u is None:
        verify_password(password, DUMMY_HASH)          # sabit süre
        return None
    if not u.is_active or not verify_password(password, u.password_hash):
        return None
    u.last_login_at = datetime.now(timezone.utc)
    return u


async def set_password(s: AsyncSession, user_id: uuid.UUID, password: str) -> None:
    (await get(s, user_id)).password_hash = hash_password(password)


async def update(s: AsyncSession, user_id: uuid.UUID, *, role: Role | None = None, is_active: bool | None = None,
                 display_name: str | None = None) -> User:
    u = await get(s, user_id)
    if role is not None:
        u.role = role
    if is_active is not None:
        u.is_active = is_active
    if display_name:
        u.display_name = display_name
    await s.flush()
    return u


async def list_(s: AsyncSession, page: int = 1, size: int = 50, sort: str | None = None) -> Page[User]:
    stmt = order_by(select(User), sort, {"username": User.username, "created_at": User.created_at,
                                         "role": User.role}, "username")
    return await paginate(s, stmt, page, size)


async def ensure_admin(s: AsyncSession, username: str, password: str) -> bool:
    """Hiç admin yoksa ilk admini oluşturur. Var olan kullanıcıların parolasına dokunmaz."""
    exists = (await s.execute(select(func.count()).select_from(User).where(User.role == Role.ADMIN))).scalar_one()
    if exists:
        return False
    if not password:
        raise RuntimeError("Hiç admin kullanıcı yok ve SRE_ADMIN_PASSWORD tanımlı değil")
    await create(s, username, "Yönetici", Role.ADMIN, password)
    return True
