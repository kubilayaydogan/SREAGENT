"""Service account API anahtarları (P2.2; sre.api_keys).

Biçim: `srek_<prefix>_<secret>`. `prefix` (8 hex) herkese açık tanımlayıcıdır (liste ve audit'te görünür); `secret`
256 bit rastgeledir ve yalnız oluşturma cevabında bir kez döner. DB'de `HMAC-SHA256(pepper, secret)` tutulur; pepper
sunucu JWT secret'ıdır (DB sızıntısı tek başına anahtarı doğrulamaya yetmez; JWT secret değişirse anahtarlar geçersiz
olur).

Deny-by-default:
* Süre sonu zorunlu (en çok MAX_DAYS gün); iptal edilen / süresi dolan / sahibi devre dışı anahtar reddedilir.
* Scope'lar yalnız ALLOWED_SCOPES içinden seçilebilir ve oluşturan kişinin yetkilerinin alt kümesi olmalıdır.
* Etkin yetki her istekte `scopes ∩ sahibin o anki rol yetkileri` olarak hesaplanır (sahibin rolü düşerse anahtar da
  daralır).
* API anahtarı yalnız `require_perm` ile korunan rotalarda geçerlidir; kimlik / oturum / parola uçları gibi yalnız
  oturum isteyen rotalar anahtarı reddeder (api/deps.py).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from airlab_agent.errors import UserError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiKey, User
from .permissions import P, ROLE_PERMISSIONS

TOKEN_PREFIX = "srek_"
MAX_DAYS = 365
MAX_ACTIVE_PER_OWNER = 20
TOUCH_INTERVAL = timedelta(seconds=60)

# Otomasyona verilebilecek yetkiler. Kimlik/secret/connector/LLM/settings yazma ve onay kararı bilinçli olarak yok:
# bunlar insan oturumu ister.
ALLOWED_SCOPES: tuple[P, ...] = (P.RECORD_READ, P.AGENT_RUN, P.INCIDENT_WRITE, P.APPROVAL_REQUEST, P.SETTINGS_READ,
                                 P.AUDIT_READ, P.OBSERVABILITY_READ, P.KNOWLEDGE_MANAGE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(pepper: str, secret: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def looks_like_key(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


def effective_scopes(key: ApiKey, owner: User) -> frozenset[str]:
    role_perms = {p.value for p in ROLE_PERMISSIONS.get(owner.role, ())}
    allowed = {p.value for p in ALLOWED_SCOPES}
    return frozenset(set(key.scopes) & role_perms & allowed)


def status_of(k: ApiKey, now: datetime | None = None) -> str:
    now = now or _now()
    if k.revoked_at is not None:
        return "revoked"
    if k.expires_at <= now:
        return "expired"
    return "active"


async def create(s: AsyncSession, *, pepper: str, owner: User, name: str, scopes: list[str], days: int,
                 actor: str) -> tuple[ApiKey, str]:
    """(satır, düz anahtar). Düz anahtar yalnız burada döner; çağıran bir kez gösterir."""
    if not 1 <= days <= MAX_DAYS:
        raise UserError(f"geçerlilik süresi 1–{MAX_DAYS} gün olmalı")
    want = sorted(set(scopes))
    if not want:
        raise UserError("en az bir scope seçilmeli")
    allowed = {p.value for p in ALLOWED_SCOPES}
    bad = [x for x in want if x not in allowed]
    if bad:
        raise UserError(f"API anahtarına verilemeyen scope: {', '.join(bad)}")
    mine = {p.value for p in ROLE_PERMISSIONS.get(owner.role, ())}
    over = [x for x in want if x not in mine]
    if over:
        raise UserError(f"sahip kullanıcıda olmayan yetki: {', '.join(over)}")
    active = await s.scalar(select(func.count()).select_from(ApiKey).where(
        ApiKey.owner_id == owner.id, ApiKey.revoked_at.is_(None), ApiKey.expires_at > func.now()))
    if (active or 0) >= MAX_ACTIVE_PER_OWNER:
        raise UserError(f"en çok {MAX_ACTIVE_PER_OWNER} etkin anahtar olabilir; kullanılmayanları iptal edin")
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    row = ApiKey(id=uuid.uuid4(), name=name, prefix=prefix, key_hash=_digest(pepper, secret), owner_id=owner.id,
                 scopes=want, created_by=actor[:64], created_at=_now(), expires_at=_now() + timedelta(days=days))
    s.add(row)
    await s.flush()
    return row, f"{TOKEN_PREFIX}{prefix}_{secret}"


async def authenticate(s: AsyncSession, token: str, *, pepper: str,
                       ip: str | None) -> tuple[ApiKey | None, User | None, str]:
    """(anahtar, sahip, sebep). sebep 'ok' değilse istek reddedilir. Sabit zamanlı karşılaştırma."""
    body = token[len(TOKEN_PREFIX):]
    prefix, _, secret = body.partition("_")
    if len(prefix) != 8 or not secret:
        return None, None, "apikey_malformed"
    k = (await s.execute(select(ApiKey).where(ApiKey.prefix == prefix))).scalar_one_or_none()
    if k is None:
        _digest(pepper, secret)                 # bilinmeyen prefix'te de aynı iş (zamanlama farkını azaltır)
        return None, None, "apikey_unknown"
    if not hmac.compare_digest(k.key_hash, _digest(pepper, secret)):
        return k, None, "apikey_bad_secret"
    st = status_of(k)
    if st != "active":
        return k, None, f"apikey_{st}"
    owner = await s.get(User, k.owner_id)
    if owner is None or not owner.is_active:
        return k, None, "apikey_owner_inactive"
    now = _now()
    if k.last_used_at is None or now - k.last_used_at >= TOUCH_INTERVAL:
        k.last_used_at = now
        k.last_used_ip = (ip or None) and ip[:64]
    return k, owner, "ok"


async def revoke(s: AsyncSession, key_id: uuid.UUID, reason: str) -> ApiKey | None:
    k = await s.get(ApiKey, key_id)
    if k is None:
        return None
    if k.revoked_at is None:
        k.revoked_at = _now()
        k.revoked_reason = reason[:32]
    return k


def out(k: ApiKey, owner_name: str | None = None) -> dict:
    return {"id": str(k.id), "name": k.name, "prefix": k.prefix, "scopes": list(k.scopes),
            "owner": owner_name, "created_by": k.created_by, "created_at": k.created_at.isoformat(),
            "expires_at": k.expires_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None, "last_used_ip": k.last_used_ip,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None, "revoked_reason": k.revoked_reason,
            "status": status_of(k)}


async def revoke_owner(s: AsyncSession, owner_id: uuid.UUID, reason: str) -> int:
    """Sahibin tüm etkin anahtarlarını iptal et (sahip devre dışı bırakıldığında; yeniden etkinleştirme canlandırmaz)."""
    rows = (await s.execute(select(ApiKey).where(ApiKey.owner_id == owner_id, ApiKey.revoked_at.is_(None)))).scalars()
    n = 0
    for k in rows:
        k.revoked_at, k.revoked_reason = _now(), reason[:32]
        n += 1
    await s.flush()
    return n
