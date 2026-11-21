"""Parola özeti (scrypt, standart kütüphane) ve JWT erişim belirteci."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

_N, _R, _P, _LEN = 2 ** 14, 8, 1, 32
ALGO = "HS256"


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("parola en az 10 karakter olmalı")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LEN)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


# Zamanlama saldırısını azaltmak için: kullanıcı yoksa da aynı maliyette doğrulama yapılır
DUMMY_HASH = "scrypt$16384$8$1$" + base64.b64encode(b"0" * 16).decode() + "$" + base64.b64encode(b"0" * 32).decode()


def create_token(user_id: str, role: str, secret: str, ttl_minutes: int) -> tuple[str, datetime]:
    exp = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    tok = jwt.encode({"sub": user_id, "role": role, "exp": exp, "iat": datetime.now(timezone.utc),
                      "jti": secrets.token_hex(8)}, secret, algorithm=ALGO)
    return tok, exp


def decode_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[ALGO], options={"require": ["sub", "exp", "role"]})
