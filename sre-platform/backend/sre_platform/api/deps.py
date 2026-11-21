"""FastAPI bağımlılıkları: DB oturumu (istek başına transaction), kimlik doğrulama, rol kontrolü."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decode_token
from ..db.models import Role, User

bearer = HTTPBearer(auto_error=False)
RANK = {Role.VIEWER: 0, Role.SRE: 1, Role.ADMIN: 2}


def rt(request: Request):
    return request.app.state.rt


async def db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.rt.sm() as s:
        async with s.begin():
            yield s


def _unauth(msg: str = "kimlik doğrulama gerekli") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, msg, headers={"WWW-Authenticate": "Bearer"})


async def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                       s: AsyncSession = Depends(db)) -> User:
    if cred is None or cred.scheme.lower() != "bearer":
        raise _unauth()
    try:
        claims = decode_token(cred.credentials, request.app.state.rt.ps.jwt_secret)
        uid = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauth("geçersiz veya süresi dolmuş oturum") from None
    u = await s.get(User, uid)
    if u is None or not u.is_active:
        raise _unauth("kullanıcı devre dışı")
    request.state.user_id = str(u.id)
    return u


def require(role: Role):
    async def dep(u: User = Depends(current_user)) -> User:
        if RANK[u.role] < RANK[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"bu işlem için '{role.value}' yetkisi gerekli")
        return u
    return dep


viewer = current_user
sre = require(Role.SRE)
admin = require(Role.ADMIN)
