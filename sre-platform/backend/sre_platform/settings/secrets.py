"""Secret referansları.

  env:AD      deployment'ın yönettiği ortam değişkeni. Yalnız izin listesindeki adlar çözülür (aksi halde bir yönetici
              SRE_JWT_SECRET gibi platform sırlarını harici bir connector'a "secret" olarak bağlayıp sızdırabilirdi).
              İzin listesi: bilinen entegrasyon anahtarları + SRE_SECRET_* + SRE_SECRET_ENV_ALLOWLIST.
  secret:ad   veritabanında şifreli (Fernet, AES-128-CBC + HMAC). Anahtar SRE_SETTINGS_KEY ortamındadır, veritabanında
              DEĞİL. Anahtar yoksa saklanan secret özelliği kapalıdır (yalnız env referansları kullanılır).

Değerler hiçbir API cevabına, loga, audit'e veya trace'e yazılmaz; çözülen değerler redaksiyon listesine eklenir.
"""
from __future__ import annotations

from airlab_agent.errors import UserError

import base64
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import register_secrets

from ..db.models import StoredSecret

KNOWN_ENV = ("DT_PLATFORM_TOKEN", "ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "SRE_SLACK_WEBHOOK_URL")
SECRET_ENV_RE = re.compile(r"^SRE_SECRET_[A-Z0-9_]{1,56}$")


class SecretError(UserError):
    pass


def env_allowlist() -> set[str]:
    extra = {x.strip() for x in os.getenv("SRE_SECRET_ENV_ALLOWLIST", "").split(",") if x.strip()}
    return set(KNOWN_ENV) | extra


def env_allowed(name: str) -> bool:
    return name in env_allowlist() or bool(SECRET_ENV_RE.match(name))


def _fernet():
    key = os.getenv("SRE_SETTINGS_KEY", "")
    if len(key) < 32:
        return None
    from cryptography.fernet import Fernet
    # Herhangi bir uzun sırdan deterministik Fernet anahtarı (32 bayt → urlsafe base64)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def store_available() -> bool:
    return _fernet() is not None


def hint(value: str) -> str:
    return f"{len(value)} karakter"


EXPIRING_DAYS = 14                 # süre sonuna bu kadar gün kala "expiring" uyarısı
DEFAULT_ROTATION_DAYS = 90
_UNSET: Any = object()


def lifecycle(row: StoredSecret, now: datetime | None = None) -> dict:
    """Yaşam döngüsü durumu (değer YOK). Öncelik: expired > expiring > rotation_due > ok."""
    now = now or datetime.now(timezone.utc)
    last = row.rotated_at or row.created_at
    due = last + timedelta(days=row.rotation_days) if row.rotation_days and last else None
    status = "ok"
    if due and due <= now:
        status = "rotation_due"
    if row.expires_at and row.expires_at - now <= timedelta(days=EXPIRING_DAYS):
        status = "expiring"
    if row.expires_at and row.expires_at <= now:
        status = "expired"
    return {"status": status, "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "rotation_days": row.rotation_days, "rotation_due_at": due.isoformat() if due else None,
            "last_changed_at": last.isoformat() if last else None, "description": row.description}


def _check_meta(expires_at, rotation_days, description) -> None:
    if expires_at is not _UNSET and expires_at is not None:
        if expires_at.tzinfo is None:
            raise SecretError("expires_at saat dilimi içermeli (ör. 2027-01-01T00:00:00Z)")
        if expires_at <= datetime.now(timezone.utc):
            raise SecretError("expires_at gelecekte olmalı")
    if rotation_days is not _UNSET and rotation_days is not None and not 1 <= rotation_days <= 3650:
        raise SecretError("rotation_days 1–3650 olmalı (boş = rotation politikası yok)")
    if description is not _UNSET and description is not None and len(description) > 256:
        raise SecretError("açıklama en çok 256 karakter")


def set_meta(row: StoredSecret, *, expires_at=_UNSET, rotation_days=_UNSET, description=_UNSET) -> dict:
    """Metadata günceller; değişenleri {alan: (eski, yeni)} döner. Gönderilmeyen alan değişmez, None temizler."""
    _check_meta(expires_at, rotation_days, description)
    changed = {}
    for k, v in (("expires_at", expires_at), ("rotation_days", rotation_days), ("description", description)):
        if v is not _UNSET and getattr(row, k) != v:
            changed[k] = (getattr(row, k), v)
            setattr(row, k, v)
    return changed


async def put(s: AsyncSession, name: str, value: str, actor: str, *, expires_at=_UNSET, rotation_days=_UNSET,
              description=_UNSET) -> tuple[StoredSecret, bool]:
    """Oluşturur ya da döndürür (rotate). (kayıt, yeni_mi). Süresi dolmuş secret, yeni bir süre sonu verilmeden
    (ya da açıkça kaldırılmadan) döndürülemez: eski süre sonu yeni değere sessizce taşınmaz."""
    f = _fernet()
    if f is None:
        raise SecretError("SRE_SETTINGS_KEY tanımlı değil (en az 32 karakter); saklanan secret kullanılamaz")
    if not re.match(r"^[a-z0-9][a-z0-9_.-]{0,63}$", name):
        raise SecretError("secret adı küçük harf, rakam, '_', '.', '-' içermeli (en fazla 64)")
    if not value or len(value) > 8192:
        raise SecretError("secret değeri 1–8192 karakter olmalı")
    _check_meta(expires_at, rotation_days, description)
    row = await s.get(StoredSecret, name)
    ct = f.encrypt(value.encode()).decode()
    if row is None:
        row = StoredSecret(name=name, ciphertext=ct, hint=hint(value), created_by=actor,
                           created_at=datetime.now(timezone.utc), rotation_days=DEFAULT_ROTATION_DAYS)
        set_meta(row, expires_at=expires_at, rotation_days=rotation_days, description=description)
        s.add(row)
        await s.flush()
        return row, True
    now = datetime.now(timezone.utc)
    if expires_at is _UNSET and row.expires_at is not None and row.expires_at <= now:
        raise SecretError("secret'ın süresi dolmuş: döndürürken yeni bir son kullanma tarihi verin ya da kaldırın")
    row.ciphertext, row.hint, row.rotated_at = ct, hint(value), now
    set_meta(row, expires_at=expires_at, rotation_days=rotation_days, description=description)
    await s.flush()
    return row, False


async def resolve(s: AsyncSession | None, ref: str | None) -> str | None:
    """Referansı değere çevirir. Çözülemezse SecretError. `None` → None."""
    if not ref:
        return None
    kind, _, name = ref.partition(":")
    if kind == "env":
        if not env_allowed(name):
            raise SecretError(f"env:{name} izin listesinde değil (SRE_SECRET_* veya SRE_SECRET_ENV_ALLOWLIST)")
        v = os.getenv(name, "")
        if not v:
            raise SecretError(f"ortam değişkeni {name} tanımlı değil")
        register_secrets([v])
        return v
    if kind == "secret":
        f = _fernet()
        if f is None:
            raise SecretError("SRE_SETTINGS_KEY tanımlı değil; saklanan secret çözülemez")
        if s is None:
            raise SecretError("veritabanı oturumu yok")
        row = await s.get(StoredSecret, name)
        if row is None:
            raise SecretError(f"secret:{name} bulunamadı")
        if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
            raise SecretError(f"secret:{name} süresi dolmuş ({row.expires_at:%Y-%m-%d}); döndürülmesi gerekiyor")
        try:
            v = f.decrypt(row.ciphertext.encode()).decode()
        except Exception:  # noqa: BLE001 — anahtar değişmiş olabilir; değer asla mesaja girmez
            raise SecretError(f"secret:{name} çözülemedi (SRE_SETTINGS_KEY değişmiş olabilir)") from None
        register_secrets([v])
        return v
    raise SecretError("secret referansı env:AD veya secret:ad olmalı")


def check_ref(ref: str | None) -> None:
    """Kaydetmeden önce: env referansı yalnız izin listesindeki değişkenler. (Henüz tanımlı olmaması kabul edilir.)"""
    if ref and ref.startswith("env:") and not env_allowed(ref[4:]):
        raise SecretError(f"{ref}: ortam değişkeni secret izin listesinde değil (SRE_SECRET_ENV_ALLOWLIST)")


def describe(ref: str | None) -> dict:
    """Arayüz için: referansın türü ve çözülebilir olup olmadığı (değer YOK)."""
    if not ref:
        return {"ref": None, "kind": None, "available": False}
    kind, _, name = ref.partition(":")
    if kind == "env":
        ok = env_allowed(name) and bool(os.getenv(name))
        why = None if ok else ("izin listesinde değil" if not env_allowed(name) else "ortamda tanımlı değil")
        return {"ref": ref, "kind": "env", "available": ok, "problem": why}
    return {"ref": ref, "kind": "secret", "available": None if store_available() else False,
            "problem": None if store_available() else "SRE_SETTINGS_KEY yok"}
