"""Working memory: konuşma thread'inin checkpoint'lenen kısa vadeli durumu.

Üç parça (hepsi ChatState içinde, AsyncPostgresSaver ile kalıcı):

* `messages` — son MAX_STATE_MESSAGES ham mesaj (router ve takip soruları için).
* `focus`    — konuşmanın o anki odağı: uygulamalar, analiz penceresi, mechanism, son investigation. Eksik bilgi
               içeren takip sorularını ("peki 1 saat öncesi?", "aynı pencerede checkin'e bak") tamamlar.
* `digests`  — tur başına kısa özet (soru, niyet, investigation, mechanism, cevabın başı). Ham mesaj penceresinden
               düşen eski turlar böylece LLM çağrısı olmadan (deterministik) hatırlanır.

Kurallar: soruda AÇIKÇA verilen uygulama/zaman her zaman odağın önüne geçer; arayüzde seçilen aralık da öyle.
Odak yalnız soru eksik/elips içerdiğinde devralınır ve devralma kullanıcıya agent_thinking olayıyla söylenir.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_DIGESTS = 30
DIGEST_ANSWER_CHARS = 240

# Eksik/elips içeren takip soruları (kullanıcı girdisi sınıflandırması; çıktı metni değildir)
_ELLIPTIC = re.compile(r"^(peki|ya |aynı|şimdi|bir de|bunu|onu|orada|o uygulama|tekrar|yine|devam)|"
                       r"\b(aynı (uygulama|servis)|aynısını|bunun|onun|orada)\b", re.I)
_SAME_WINDOW = re.compile(r"\baynı (zaman|pencere|aralık|dönem|saat)", re.I)
_SHIFT = re.compile(r"\b(\d+|bir|iki|üç|yarım)\s*(saat|dakika|dk|gün)\s+(öncesi|önceki|öncesine|öncesini)", re.I)
_WORDS = {"bir": 1, "iki": 2, "üç": 3, "yarım": 0.5}


def append_digests(a: list | None, b: list | None) -> list:
    return ((a or []) + (b or []))[-MAX_DIGESTS:]


def next_turn(state: dict) -> int:
    d = state.get("digests") or []
    return (d[-1].get("turn") or len(d)) + 1 if d else 1


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _shift_minutes(q: str) -> float | None:
    m = _SHIFT.search(q)
    if not m:
        return None
    n = _WORDS.get(m.group(1).lower()) or float(m.group(1))
    unit = m.group(2).lower()
    return n * (1440 if unit == "gün" else 60 if unit == "saat" else 1)


def resolve(route: dict, focus: dict | None, question: str, *, explicit_window: bool) -> tuple[dict, list[str]]:
    """Router çıktısını odakla tamamlar. Yalnız boş alanlar doldurulur. (route, kullanıcıya gösterilecek notlar)."""
    d = dict(route)
    notes: list[str] = []
    f = focus or {}
    if not f or d.get("intent") not in ("analyze", "compare", "history"):
        return d, notes
    q = question.strip()
    elliptic = bool(_ELLIPTIC.search(q))
    if not d.get("focus_apps") and f.get("apps") and elliptic:
        d["focus_apps"] = list(f["apps"])
        notes.append("uygulama: " + ", ".join(f["apps"]))
    if d.get("intent") == "history" and not d.get("mechanism") and f.get("mechanism") and elliptic:
        d["mechanism"] = f["mechanism"]
        notes.append(f"mechanism: {f['mechanism']}")
    has_time = bool(d.get("minutes") or d.get("start_utc"))
    win = f.get("window") or {}
    if d.get("intent") in ("analyze", "compare") and not explicit_window and win.get("start") and win.get("end"):
        shift = _shift_minutes(q)
        if shift and not d.get("start_utc"):
            s, e = _dt(win["start"]) - timedelta(minutes=shift), _dt(win["end"]) - timedelta(minutes=shift)
            d.update(minutes=None, start_utc=_iso(s), end_utc=_iso(e))
            notes.append(f"pencere: önceki pencere {shift:g} dk geriye kaydırıldı ({_iso(s)} – {_iso(e)})")
        elif not has_time and _SAME_WINDOW.search(q):
            d.update(minutes=None, start_utc=win["start"], end_utc=win["end"])
            notes.append(f"pencere: önceki pencere ({win['start']} – {win['end']})")
    return d, notes


def focus_from_route(prev: dict | None, route: dict, turn: int) -> dict:
    f = dict(prev or {})
    if route.get("focus_apps"):
        f["apps"] = list(route["focus_apps"])
    if route.get("mechanism"):
        f["mechanism"] = route["mechanism"]
    f.update(intent=route.get("intent"), turn=turn, updated_at=_iso(datetime.now(timezone.utc)))
    return f


def focus_from_investigation(prev: dict | None, inv: dict) -> dict:
    f = dict(prev or {})
    w = inv.get("window") or []
    if len(w) == 2:
        f["window"] = {"start": _iso(_dt(w[0])), "end": _iso(_dt(w[1]))}
    if inv.get("mechanism"):
        f["mechanism"] = inv["mechanism"]
    apps = list(dict.fromkeys((inv.get("source_apps") or []) + (inv.get("affected_apps") or [])))
    if apps and not f.get("apps"):
        f["apps"] = apps
    f["investigation"] = f"inv:{inv['investigation_id']}" if inv.get("investigation_id") else f.get("investigation")
    return f


def digest(turn: int, question: str, route: dict, inv: dict | None, answer: str) -> dict[str, Any]:
    head = " ".join((answer or "").split())
    return {"turn": turn, "at": _iso(datetime.now(timezone.utc)), "question": question[:300],
            "intent": route.get("intent"), "apps": route.get("focus_apps") or [],
            "investigation": f"inv:{inv['investigation_id']}" if inv and inv.get("investigation_id") else None,
            "mechanism": (inv or {}).get("mechanism") or route.get("mechanism"),
            "answer_head": head[:DIGEST_ANSWER_CHARS] + ("…" if len(head) > DIGEST_ANSWER_CHARS else "")}


def router_hint(focus: dict | None) -> str | None:
    """LLM router'a verilen odak özeti (yalnız sınıflandırma bağlamı)."""
    if not focus:
        return None
    parts = []
    if focus.get("apps"):
        parts.append("uygulamalar=" + ", ".join(focus["apps"]))
    w = focus.get("window") or {}
    if w.get("start"):
        parts.append(f"son analiz penceresi (UTC)={w['start']} – {w['end']}")
    if focus.get("mechanism"):
        parts.append(f"mechanism={focus['mechanism']}")
    if focus.get("investigation"):
        parts.append(f"son investigation={focus['investigation']}")
    return "; ".join(parts) or None


def public_view(state: dict, max_messages: int) -> dict[str, Any]:
    msgs = state.get("messages") or []
    return {"focus": state.get("focus") or None, "digests": list(reversed(state.get("digests") or [])),
            "messages_in_state": len(msgs), "max_messages": max_messages, "max_digests": MAX_DIGESTS}
