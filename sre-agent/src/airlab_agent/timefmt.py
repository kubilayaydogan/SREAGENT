"""Kullanıcıya gösterilen zamanlar Türkiye saatiyle (TSİ, UTC+3) yazılır; UTC ayrıca belirtilmez.

Kayıtlar, query pencereleri ve API alanları UTC kalır (ISO-8601). Yalnız kullanıcıya giden METİN ve LLM'e verilen
veri bloğu TSİ'ye çevrilir. Çeviri deterministiktir: model saat hesabı yapmaz, verideki TSİ değerini aynen aktarır.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

TSI = timezone(timedelta(hours=3))
LABEL = "TSİ"
# Tam eşleşen ISO-8601 zaman damgası (saat dilimi belirtilmiş). DQL / log metni içindeki zamanlara dokunulmaz.
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$")


def _parse(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def tsi(v: Any, *, date: bool = True, seconds: bool = False, label: bool = True, missing: str = "?") -> str:
    """'2026-11-21T20:42:00Z' → '2026-11-21 23:42 TSİ' (tarih TSİ gününe göre)."""
    t = _parse(v) if v else None
    if t is None:
        return missing
    t = t.astimezone(TSI)
    fmt = ("%Y-%m-%d " if date else "") + ("%H:%M:%S" if seconds else "%H:%M")
    return t.strftime(fmt) + (f" {LABEL}" if label else "")


def span(start: Any, end: Any) -> str:
    """Aralık: '2026-11-21 15:15–15:20 TSİ'; gün değişiyorsa iki tarih de yazılır."""
    a, b = _parse(start), _parse(end)
    if a is None or b is None:
        return "?"
    a, b = a.astimezone(TSI), b.astimezone(TSI)
    if a.date() == b.date():
        return f"{a:%Y-%m-%d %H:%M}–{b:%H:%M} {LABEL}"
    return f"{a:%Y-%m-%d %H:%M} – {b:%Y-%m-%d %H:%M} {LABEL}"


def localize(obj: Any) -> Any:
    """Veri bloğundaki ISO zaman damgalarını TSİ metnine çevirir (yeni nesne döner; girdi değişmez)."""
    if isinstance(obj, dict):
        return {k: localize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [localize(v) for v in obj]
    if isinstance(obj, datetime):
        return tsi(obj, seconds=bool(obj.second))
    if isinstance(obj, str) and _ISO.match(obj):
        t = _parse(obj)
        return tsi(t, seconds=bool(t and t.second)) if t else obj
    return obj
