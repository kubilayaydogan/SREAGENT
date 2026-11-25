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


async def authenticate(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                       s: AsyncSession = Depends(db)) -> User:
    """Kimlik doğrulayıcı (oturum VEYA API anahtarı). Doğrudan rotada kullanılmaz: `current_user` (yalnız oturum),
    `require_perm` (oturum + scope'lu API anahtarı) ya da `keyed` üzerinden gelir.

    Oturum: geçerli imza + zorunlu claim'ler + aktif sunucu oturumu + aktif kullanıcı. Rol DB'den okunur.
    API anahtarı (`srek_…`): bkz. security/api_keys.py. `request.state.api_scopes` yalnız anahtarla dolar."""
    from ..security import api_keys, context, sessions
    request.state.api_scopes = None
    if cred is None or cred.scheme.lower() != "bearer":
        raise _unauth()
    if api_keys.looks_like_key(cred.credentials):
        k, owner, why = await api_keys.authenticate(s, cred.credentials, pepper=request.app.state.rt.ps.jwt_secret,
                                                    ip=context.client_ip.get())
        if owner is None:
            await _audit_denied(request, actor=f"apikey:{k.prefix}" if k else "anonymous:apikey",
                                action="auth.apikey.rejected", reason=why)
            raise _unauth("geçersiz, süresi dolmuş veya iptal edilmiş API anahtarı")
        request.state.user_id = str(owner.id)
        request.state.username = owner.username
        request.state.session_id = None
        request.state.api_key_id = str(k.id)
        request.state.api_scopes = api_keys.effective_scopes(k, owner)
        context.api_key.set(k.prefix)
        return owner
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


def is_api_key(request: Request) -> bool:
    return getattr(request.state, "api_scopes", None) is not None


async def current_user(request: Request, u: User = Depends(authenticate)) -> User:
    """Yalnız etkileşimli oturum (deny-by-default): API anahtarı kimlik, oturum, parola, sohbet gibi uçlara giremez."""
    if is_api_key(request):
        await _audit_denied(request, actor=u.username, action="authz.denied", reason="apikey_not_allowed")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bu uç API anahtarıyla kullanılamaz; oturum gerekli")
    return u


async def _check_scope(request: Request, u: User, perm) -> None:
    if is_api_key(request) and perm.value not in request.state.api_scopes:
        await _audit_denied(request, actor=u.username, action="authz.denied", reason=f"scope_missing_{perm.value}"[:48])
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"API anahtarında '{perm.value}' scope'u yok")


def keyed(perm):
    """Oturumdaki her kullanıcıya açık (sahiplik kontrolü rotada) uç; API anahtarı ise `perm` scope'u ister."""
    async def dep(request: Request, u: User = Depends(authenticate)) -> User:
        await _check_scope(request, u, perm)
        return u
    return dep


def require(role: Role):
    async def dep(request: Request, u: User = Depends(current_user)) -> User:
        if RANK[u.role] < RANK[role]:
            await _audit_denied(request, actor=u.username, action="authz.denied", reason=f"requires_{role.value}")
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"bu işlem için '{role.value}' yetkisi gerekli")
        return u
    return dep


def require_perm(perm):
    """Permission kontrolü (security/permissions.py). Ret 403 + audit `authz.denied` (reason: missing_<perm>).
    API anahtarıyla gelen istekte ayrıca anahtarın etkin scope'u gerekir (reason: scope_missing_<perm>)."""
    from ..security.permissions import has

    async def dep(request: Request, u: User = Depends(authenticate)) -> User:
        if not has(u.role, perm):
            await _audit_denied(request, actor=u.username, action="authz.denied",
                                reason=f"missing_{perm.value}"[:48])
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"bu işlem için '{perm.value}' yetkisi gerekli")
        await _check_scope(request, u, perm)
        return u
    dep.permission = perm          # rota envanteri testi için
    return dep


viewer = current_user
sre = require(Role.SRE)
admin = require(Role.ADMIN)
