"""Kimlik doğrulama, oturum yaşam döngüsü ve kullanıcı yönetimi.

Güvenlik (P0): kalıcı login throttle, sunucu tarafı oturumlar (logout / iptal / idle), parola değişikliğinde ve
yetki değişikliğinde oturum iptali, tüm olaylar için audit (sre.audit_events).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.security import create_token, verify_password
from ...db.models import ApiKey, AuthSession, User
from ...repositories import users
from ...security import api_keys, audit, context, sessions, throttle
from ...security.permissions import P
from ..deps import current_user, db, is_api_key
from ..deps import require_perm as perm
from ..schemas import (In, LoginIn, PageOut, PasswordChangeIn, SessionOut, TokenOut, UserCreateIn, UserOut, UserUpdateIn,
                       page_of)

router = APIRouter(tags=["auth"])


def _ps(request: Request):
    return request.app.state.rt.ps


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, s: AsyncSession = Depends(db)):
    ps, sm = _ps(request), request.app.state.rt.sm
    ip = context.client_ip.get()
    key = throttle.key_for(body.username, ip)
    who = f"anonymous:{body.username[:40]}"
    if await throttle.login_throttled(s, key, max_fails=ps.login_max_fails, window_s=ps.login_window_s):
        await audit.record_separate(sm, actor=who, action="auth.login", section="auth", result="denied",
                                    reason_code="throttled")
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "çok fazla başarısız deneme; 5 dakika sonra tekrar deneyin",
                            headers={"Retry-After": str(ps.login_window_s)})
    u = await users.authenticate(s, body.username, body.password)
    if u is None:
        await throttle.login_failed(sm, key)
        await audit.record_separate(sm, actor=who, action="auth.login", section="auth", result="denied",
                                    reason_code="bad_credentials")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "kullanıcı adı veya parola hatalı")
    await throttle.login_succeeded(s, key)
    sid = sessions.new_id()
    exp = datetime.now(timezone.utc) + timedelta(minutes=ps.jwt_ttl_minutes)
    await sessions.create(s, u.id, exp, ip=ip, user_agent=request.headers.get("user-agent"), sid=sid)
    tok, exp = create_token(str(u.id), u.role.value, ps.jwt_secret, ps.jwt_ttl_minutes, jti=sid, exp=exp)
    await audit.record(s, actor=u.username, action="auth.login", section="auth", session_id=sid)
    return TokenOut(access_token=tok, expires_at=exp, user=UserOut.model_validate(u))


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, u: User = Depends(current_user), s: AsyncSession = Depends(db)):
    sid = request.state.session_id
    await sessions.revoke(s, sid, "logout")
    await audit.record(s, actor=u.username, action="auth.logout", section="auth", target=_short(sid))
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
async def me(u: User = Depends(current_user)):
    return u


@router.get("/auth/permissions")
async def my_permissions(u: User = Depends(current_user)):
    """Oturumdaki kullanıcının permission'ları (arayüz düğmeleri için; yetki kararı her zaman sunucudadır)."""
    from ...security.permissions import ROLE_PERMISSIONS
    return {"role": u.role.value, "permissions": sorted(p.value for p in ROLE_PERMISSIONS.get(u.role, ()))}


@router.post("/auth/sessions/revoke-others")
async def revoke_other_sessions(request: Request, u: User = Depends(current_user), s: AsyncSession = Depends(db)):
    """Bu oturum dışındaki tüm oturumlarımı kapat (ör. başka bir cihazda açık kalan oturum)."""
    n = await sessions.revoke_user(s, u.id, "user_revoke_others", keep=request.state.session_id)
    await audit.record(s, actor=u.username, action="auth.session.revoke_others", section="auth",
                       detail=f"{n} oturum kapatıldı")
    return {"revoked": n}


@router.post("/auth/password", status_code=204)
async def change_password(body: PasswordChangeIn, request: Request, u: User = Depends(current_user),
                          s: AsyncSession = Depends(db)):
    """Kendi parolasını değiştirme: mevcut parola zorunlu; diğer tüm oturumlar iptal edilir (bu oturum kalır)."""
    if not verify_password(body.current_password, u.password_hash):
        await audit.record_separate(request.app.state.rt.sm, actor=u.username, action="auth.password.change",
                                    section="auth", result="denied", reason_code="bad_current_password")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "mevcut parola hatalı")
    if body.current_password == body.new_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "yeni parola eskisiyle aynı olamaz")
    await users.set_password(s, u.id, body.new_password)
    n = await sessions.revoke_user(s, u.id, "password_change", keep=request.state.session_id)
    await audit.record(s, actor=u.username, action="auth.password.change", section="auth",
                       detail=f"{n} diğer oturum iptal edildi")
    return Response(status_code=204)


def _short(sid: str | None) -> str | None:
    return f"{sid[:8]}…" if sid else None


def _session_out(r: AuthSession, current: str | None) -> SessionOut:
    # Oturum kimliğinin tamamı bearer token'ın parçası değildir ama yine de yalnız sahibine/admin'e gösterilir.
    return SessionOut(id=r.id, created_at=r.created_at, last_seen_at=r.last_seen_at, expires_at=r.expires_at,
                      ip=r.ip, user_agent=r.user_agent, current=r.id == current)


@router.get("/auth/sessions", response_model=list[SessionOut])
async def my_sessions(request: Request, u: User = Depends(current_user), s: AsyncSession = Depends(db)):
    return [_session_out(r, request.state.session_id) for r in await sessions.list_active(s, u.id)]


@router.delete("/auth/sessions/{sid}", status_code=204)
async def revoke_session(sid: str, request: Request, u: User = Depends(current_user), s: AsyncSession = Depends(db)):
    from ...db.models import Role
    row = await s.get(AuthSession, sid)
    if row is None or (row.user_id != u.id and u.role != Role.ADMIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "oturum bulunamadı")      # başkasının oturumu: varlık sızdırılmaz
    await sessions.revoke(s, sid, "user_revoke" if row.user_id == u.id else "admin_revoke")
    await audit.record(s, actor=u.username, action="auth.session.revoke", section="auth", target=_short(sid))
    return Response(status_code=204)


# ---------------------------------------------------------------- kullanıcı yönetimi (admin)
@router.get("/users", response_model=PageOut[UserOut])
async def list_users(page: int = 1, size: int = 50, sort: str | None = None, _: User = Depends(perm(P.USER_MANAGE)),
                     s: AsyncSession = Depends(db)):
    return page_of(await users.list_(s, page, size, sort), UserOut)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreateIn, me_: User = Depends(perm(P.USER_MANAGE)), s: AsyncSession = Depends(db)):
    u = await users.create(s, body.username, body.display_name, body.role, body.password)
    await audit.record(s, actor=me_.username, action="user.create", section="users", target=u.username,
                       after={"role": u.role.value, "is_active": u.is_active})
    return u


@router.get("/users/{user_id}/sessions", response_model=list[SessionOut])
async def user_sessions(user_id: uuid.UUID, _: User = Depends(perm(P.USER_MANAGE)), s: AsyncSession = Depends(db)):
    await users.get(s, user_id)
    return [_session_out(r, None) for r in await sessions.list_active(s, user_id)]


@router.post("/users/{user_id}/sessions/revoke")
async def revoke_user_sessions(user_id: uuid.UUID, request: Request, me_: User = Depends(perm(P.USER_MANAGE)),
                               s: AsyncSession = Depends(db)):
    """Kullanıcının tüm oturumlarını kapat (kendi hesabınızda bu oturum korunur)."""
    target = await users.get(s, user_id)
    keep = request.state.session_id if user_id == me_.id else None
    n = await sessions.revoke_user(s, user_id, "admin_revoke", keep=keep)
    await audit.record(s, actor=me_.username, action="auth.session.revoke_all", section="users", target=target.username,
                       detail=f"{n} oturum kapatıldı")
    return {"revoked": n}


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdateIn, me_: User = Depends(perm(P.USER_MANAGE)),
                      s: AsyncSession = Depends(db)):
    if user_id == me_.id and (body.is_active is False or (body.role and body.role != me_.role)):
        raise HTTPException(status.HTTP_409_CONFLICT, "kendi rolünüzü düşüremez veya hesabınızı kapatamazsınız")
    cur = await users.get(s, user_id)
    before = {"role": cur.role.value, "is_active": cur.is_active, "display_name": cur.display_name}
    u = await users.update(s, user_id, role=body.role, is_active=body.is_active, display_name=body.display_name)
    after = {"role": u.role.value, "is_active": u.is_active, "display_name": u.display_name}
    changed = {k for k in before if before[k] != after[k]}
    revoked = 0
    if "role" in changed or (body.is_active is False and "is_active" in changed):
        # Yetki değişikliği / devre dışı: mevcut oturumlar eski yetki bağlamında açılmıştır → iptal
        revoked = await sessions.revoke_user(s, user_id, "role_change" if "role" in changed else "disabled")
    keys_revoked = await api_keys.revoke_owner(s, user_id, "owner_disabled") if (
        body.is_active is False and "is_active" in changed) else 0
    if body.password:
        await users.set_password(s, user_id, body.password)
        revoked += await sessions.revoke_user(s, user_id, "password_reset")
    action = ("user.role_change" if "role" in changed else "user.disable" if "is_active" in changed and not u.is_active
              else "user.update")
    await audit.record(s, actor=me_.username, action=action, section="users", target=u.username,
                       before={k: before[k] for k in changed} or None, after={k: after[k] for k in changed} or None,
                       detail=("parola sıfırlandı; " if body.password else "") + f"{revoked} oturum iptal edildi" + (f"; {keys_revoked} API anahtarı iptal edildi" if keys_revoked else ""))
    return u


# ------------------------------------------------------------------------------------------- service account API anahtarları
class ApiKeyCreateIn(In):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._ -]+$")
    scopes: list[str] = Field(min_length=1, max_length=16)
    days: int = Field(90, ge=1, le=api_keys.MAX_DAYS)


@router.get("/api-keys")
async def list_api_keys(include_inactive: bool = False, _: User = Depends(perm(P.USER_MANAGE)),
                        s: AsyncSession = Depends(db)):
    q = select(ApiKey, User.username).join(User, User.id == ApiKey.owner_id).order_by(ApiKey.created_at.desc())
    rows = [api_keys.out(k, owner) for k, owner in (await s.execute(q)).all()]
    if not include_inactive:
        rows = [r for r in rows if r["status"] == "active"]
    return {"items": rows, "allowed_scopes": [p.value for p in api_keys.ALLOWED_SCOPES], "max_days": api_keys.MAX_DAYS}


@router.post("/api-keys", status_code=201)
async def create_api_key(body: ApiKeyCreateIn, request: Request, me_: User = Depends(perm(P.USER_MANAGE)),
                         s: AsyncSession = Depends(db)):
    """Anahtar oluşturan admin'e bağlanır. Düz anahtar YALNIZ bu cevapta döner; sonra geri alınamaz."""
    if is_api_key(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API anahtarı yeni anahtar oluşturamaz")
    k, token = await api_keys.create(s, pepper=_ps(request).jwt_secret, owner=me_, name=body.name.strip(),
                                     scopes=body.scopes, days=body.days, actor=me_.username)
    await audit.record(s, actor=me_.username, action="apikey.create", section="users", target=f"{k.name} ({k.prefix})",
                       after={"scopes": k.scopes, "expires_at": k.expires_at.isoformat()})
    return {**api_keys.out(k, me_.username), "token": token}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: uuid.UUID, me_: User = Depends(perm(P.USER_MANAGE)), s: AsyncSession = Depends(db)):
    k = await api_keys.revoke(s, key_id, "admin_revoke")
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "anahtar bulunamadı")
    await audit.record(s, actor=me_.username, action="apikey.revoke", section="users", target=f"{k.name} ({k.prefix})")
    return api_keys.out(k)
