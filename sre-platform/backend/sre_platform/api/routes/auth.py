from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.security import create_token
from ...db.models import User
from ...repositories import users
from ..deps import admin, current_user, db
from ..schemas import LoginIn, PageOut, TokenOut, UserCreateIn, UserOut, UserUpdateIn, page_of

router = APIRouter(tags=["auth"])
_FAILS: dict[str, deque] = defaultdict(deque)
MAX_FAILS, WINDOW_S = 5, 300


def _throttled(key: str) -> bool:
    q, now = _FAILS[key], time.monotonic()
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    return len(q) >= MAX_FAILS


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, s: AsyncSession = Depends(db)):
    key = f"{body.username.lower()}|{request.client.host if request.client else '-'}"
    if _throttled(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "çok fazla başarısız deneme; 5 dakika sonra tekrar deneyin")
    u = await users.authenticate(s, body.username, body.password)
    if u is None:
        _FAILS[key].append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "kullanıcı adı veya parola hatalı")
    _FAILS.pop(key, None)
    ps = request.app.state.rt.ps
    tok, exp = create_token(str(u.id), u.role.value, ps.jwt_secret, ps.jwt_ttl_minutes)
    return TokenOut(access_token=tok, expires_at=exp, user=UserOut.model_validate(u))


@router.get("/auth/me", response_model=UserOut)
async def me(u: User = Depends(current_user)):
    return u


@router.get("/users", response_model=PageOut[UserOut])
async def list_users(page: int = 1, size: int = 50, sort: str | None = None, _: User = Depends(admin),
                     s: AsyncSession = Depends(db)):
    return page_of(await users.list_(s, page, size, sort), UserOut)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreateIn, _: User = Depends(admin), s: AsyncSession = Depends(db)):
    return await users.create(s, body.username, body.display_name, body.role, body.password)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdateIn, me_: User = Depends(admin),
                      s: AsyncSession = Depends(db)):
    if user_id == me_.id and (body.is_active is False or (body.role and body.role != me_.role)):
        raise HTTPException(status.HTTP_409_CONFLICT, "kendi rolünüzü düşüremez veya hesabınızı kapatamazsınız")
    u = await users.update(s, user_id, role=body.role, is_active=body.is_active, display_name=body.display_name)
    if body.password:
        await users.set_password(s, user_id, body.password)
    return u
