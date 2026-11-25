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


async def put(s: AsyncSession, name: str, value: str, actor: str) -> tuple[StoredSecret, bool]:
    """Oluşturur ya da döndürür (rotate). (kayıt, yeni_mi)."""
    f = _fernet()
    if f is None:
        raise SecretError("SRE_SETTINGS_KEY tanımlı değil (en az 32 karakter); saklanan secret kullanılamaz")
    if not re.match(r"^[a-z0-9][a-z0-9_.-]{0,63}$", name):
        raise SecretError("secret adı küçük harf, rakam, '_', '.', '-' içermeli (en fazla 64)")
    if not value or len(value) > 8192:
        raise SecretError("secret değeri 1–8192 karakter olmalı")
    from datetime import datetime, timezone
    row = await s.get(StoredSecret, name)
    ct = f.encrypt(value.encode()).decode()
    if row is None:
        row = StoredSecret(name=name, ciphertext=ct, hint=hint(value), created_by=actor)
        s.add(row)
        await s.flush()
        return row, True
    row.ciphertext, row.hint, row.rotated_at = ct, hint(value), datetime.now(timezone.utc)
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
