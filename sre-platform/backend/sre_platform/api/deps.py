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


async def _audit_denied(request: Request, *, actor: str, action: str, reason: str, detail: str | None = None) -> None:
    from ..security import audit
    await audit.record_separate(request.app.state.rt.sm, actor=actor, action=action, section="auth",
                                target=f"{request.method} {request.url.path}"[:128], result="denied",
                                reason_code=reason, detail=detail)


async def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                       s: AsyncSession = Depends(db)) -> User:
    """Deny-by-default: geçerli imza + zorunlu claim'ler + aktif sunucu oturumu + aktif kullanıcı. Rol DB'den okunur."""
    from ..security import context, sessions
    if cred is None or cred.scheme.lower() != "bearer":
        raise _unauth()
    try:
        claims = decode_token(cred.credentials, request.app.state.rt.ps.jwt_secret)
        uid = uuid.UUID(claims["sub"])
        sid = str(claims["jti"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauth("geçersiz veya süresi dolmuş oturum") from None
    u = await s.get(User, uid)
    if u is None or not u.is_active:
        raise _unauth("kullanıcı devre dışı")
    ok, why = await sessions.validate(s, sid, uid, request.app.state.rt.ps.session_idle_minutes)
    if not ok:
        await _audit_denied(request, actor=u.username, action="auth.session.rejected", reason=why)
        raise _unauth("oturum sona erdi; yeniden giriş yapın")
    request.state.user_id = str(u.id)
    request.state.username = u.username
    request.state.session_id = sid
    context.session_id.set(sid)
    return u


def require(role: Role):
    async def dep(request: Request, u: User = Depends(current_user)) -> User:
        if RANK[u.role] < RANK[role]:
            await _audit_denied(request, actor=u.username, action="authz.denied", reason=f"requires_{role.value}")
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"bu işlem için '{role.value}' yetkisi gerekli")
        return u
    return dep


viewer = current_user
sre = require(Role.SRE)
admin = require(Role.ADMIN)
