"""Ortak hata türleri."""
from __future__ import annotations


class UserError(ValueError):
    """Kullanıcıya AYNEN gösterilebilen doğrulama hatası (iç ayrıntı, yol, SQL, secret içermez).

    API yalnız bu türün mesajını istemciye döndürür; diğer ValueError'lar genel bir mesaja çevrilir ve ayrıntı
    (redakte edilerek) log'a yazılır (P1.9)."""
