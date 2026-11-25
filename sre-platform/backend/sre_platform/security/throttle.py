"""Kalıcı login throttle (sre.auth_login_failures) ve kullanıcı başına çalıştırma hız sınırı.

Login: anahtar `kullanıcı adı | istemci IP`; pencere içinde `max_fails` başarısız deneme → 429. Sayaçlar
veritabanındadır (backend restart'ı sıfırlamaz). Başarılı girişte anahtarın kayıtları silinir.

Çalıştırma hızı: kullanıcının son 60 sn'deki `run.start` audit olayları sayılır (kalıcı, ek tablo yok).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditEvent, AuthLoginFailure

MAX_FAILS, WINDOW_S = 5, 300


def key_for(username: str, ip: str | None) -> str:
    return f"{username.strip().lower()[:128]}|{ip or '-'}"


async def login_throttled(s: AsyncSession, key: str, *, max_fails: int = MAX_FAILS, window_s: int = WINDOW_S) -> bool:
    since = datetime.now(timezone.utc) - timedelta(seconds=window_s)
    n = await s.scalar(select(func.count()).select_from(AuthLoginFailure)
                       .where(AuthLoginFailure.key == key, AuthLoginFailure.ts > since))
    return (n or 0) >= max_fails


async def login_failed(sm, key: str) -> None:
    """Ayrı transaction: başarısız isteğin 401 cevabı transaction'ı geri alsa da sayaç kalır."""
    async with sm() as s:
        async with s.begin():
            s.add(AuthLoginFailure(key=key))
            # Eski kayıtlar fırsatçı olarak temizlenir (1 günden eski)
            await s.execute(delete(AuthLoginFailure).where(
                AuthLoginFailure.ts < datetime.now(timezone.utc) - timedelta(days=1)))


async def login_succeeded(s: AsyncSession, key: str) -> None:
    await s.execute(delete(AuthLoginFailure).where(AuthLoginFailure.key == key))


async def runs_last_minute(s: AsyncSession, username: str) -> int:
    since = datetime.now(timezone.utc) - timedelta(seconds=60)
    return int(await s.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.actor == username, AuditEvent.action == "run.start", AuditEvent.ts > since)) or 0)
