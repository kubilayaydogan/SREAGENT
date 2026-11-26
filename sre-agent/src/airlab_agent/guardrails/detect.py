"""Deterministik dedektörler: hassas veri (genişletilebilir tür listesi + yapılandırılabilir kalıplar), prompt
injection, tehlikeli işlem ve kesinlik ifadeleri.

Kurallar kural tabanlıdır: her saldırıyı yakalamaz (GUARDRAILS_SECURITY_MODEL.md "bilinen sınırlamalar"). Yapılandırılan
kalıplar yalnız regex'tir (kod çalıştırılmaz); kabul öncesi uzunluk ve iç içe niceleyici kontrolünden geçer, taranan
metin uzunluğu sınırlıdır (ReDoS ve kaynak tüketimi).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..redact import _extra as _registered_secrets
from ..redact import _iban_ok, _IIN, _luhn, _tckn_ok

MAX_SCAN_CHARS = 200_000


@dataclass(frozen=True)
class Detector:
    type: str
    label: str
    kind: str                     # secret | pii
    pattern: re.Pattern
    check: Callable[[str], bool] | None = None
    group: int = 0                # maskelenecek grup (0 = tümü)


def _card_ok(raw: str) -> bool:
    d = re.sub(r"[ -]", "", raw)
    return 13 <= len(d) <= 19 and bool(_IIN.match(d)) and _luhn(d)


DETECTORS: list[Detector] = [
    Detector("private_key", "Private key", "secret",
             re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    Detector("api_key", "API key", "secret",
             re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]{10,}|lsv2_[A-Za-z0-9_]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
                        r"dt0[a-z]\d{2}\.[A-Za-z0-9._-]{8,}|xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    Detector("access_token", "Access token (JWT)", "secret",
             re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    Detector("bearer_token", "Bearer token", "secret", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{16,})"), group=1),
    Detector("auth_header", "Authorization header", "secret",
             re.compile(r"(?i)\b(?:authorization|proxy-authorization|x-api-key)\s*[:=]\s*\"?([^\s\",}]{6,})"), group=1),
    Detector("connection_string", "Connection string", "secret",
             re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,64}:([^\s@/]{3,128})@[^\s/]+")
             , group=1),
    Detector("db_credential", "Database credential", "secret",
             re.compile(r"(?i)\b(?:pwd|password|passwd)\s*=\s*([^;\s\"']{3,128})(?=;|\s|$)"), group=1),
    Detector("password", "Password", "secret",
             re.compile(r"(?i)\b(?:password|passwd|parola|şifre|secret|client_secret)\"?\s*[:=]\s*\"?([^\s\",}]{3,128})"),
             group=1),
    Detector("session_id", "Session identifier", "secret",
             re.compile(r"(?i)\b(?:session[_-]?id|sessionid|jsessionid|phpsessid|sid)\"?\s*[:=]\s*\"?([A-Za-z0-9._-]{12,})"),
             group=1),
    Detector("webhook", "Webhook URL", "secret", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+")),
    Detector("email", "E-posta adresi", "pii",
             re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\b")),
    Detector("national_id", "Ulusal kimlik no (TCKN)", "pii", re.compile(r"(?<![\w.-])[1-9]\d{10}(?![\w.-])"),
             check=_tckn_ok),
    Detector("credit_card", "Kredi kartı", "pii", re.compile(r"(?<![\d.-])\d(?:[ -]?\d){12,18}(?![\d.-])"),
             check=_card_ok),
    Detector("iban", "IBAN", "pii", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"),
             check=_iban_ok),
]
DETECTOR_TYPES = {d.type: d for d in DETECTORS}
SECRET_TYPES = [d.type for d in DETECTORS if d.kind == "secret"]
PII_TYPES = [d.type for d in DETECTORS if d.kind == "pii"]

_NESTED_QUANT = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*[+*{]")


def validate_custom_pattern(p: str) -> re.Pattern:
    """Yapılandırılabilir kalıp kabulü. Geçersiz → ValueError. Kod çalıştırma yoktur; yalnız regex."""
    if not isinstance(p, str) or not 3 <= len(p) <= 200:
        raise ValueError("kalıp 3-200 karakter olmalı")
    if _NESTED_QUANT.search(p) or re.search(r"\\\d", p) or "(?P=" in p:
        raise ValueError("iç içe niceleyici ya da backreference içeren kalıp kabul edilmez (ReDoS)")
    try:
        rx = re.compile(p)
    except re.error as e:
        raise ValueError(f"geçersiz regex: {e}") from None
    if rx.search("") is not None:
        raise ValueError("boş metinle eşleşen kalıp kabul edilmez")
    return rx


def _custom(patterns: list[dict] | None) -> list[Detector]:
    out = []
    for i, c in enumerate(patterns or []):
        try:
            rx = validate_custom_pattern(c.get("pattern", ""))
        except ValueError:
            continue                       # yapılandırma kaydında zaten reddedilir; snapshot bozuksa atlanır
        name = re.sub(r"[^a-z0-9_]", "_", str(c.get("name") or f"custom_{i}").lower())[:40]
        out.append(Detector(f"custom:{name}", str(c.get("label") or name)[:80], c.get("kind", "secret"), rx))
    return out


def scan(text: str, types: list[str] | None = None, custom: list[dict] | None = None,
         max_chars: int = MAX_SCAN_CHARS) -> tuple[list[dict], str, bool]:
    """Hassas veri tarama. Dönüş: (eşleşmeler [{type,label,kind,start,end}], maskelenmiş metin, kısmi_tarama).
    Eşleşme kayıtlarında DEĞER tutulmaz."""
    if not isinstance(text, str) or not text:
        return [], text, False
    partial = len(text) > max_chars
    head, tail = (text[:max_chars], text[max_chars:]) if partial else (text, "")
    dets = [d for d in DETECTORS if types is None or d.type in types] + _custom(custom)
    hits: list[dict] = []
    spans: list[tuple[int, int, str]] = []
    for v in _registered_secrets:                     # ortamdan kayıtlı gerçek secret değerleri (birebir)
        if types is None or "api_key" in types:
            start = head.find(v)
            while start != -1:
                spans.append((start, start + len(v), "api_key"))
                hits.append({"type": "registered_secret", "label": "Kayıtlı secret", "kind": "secret",
                             "start": start, "end": start + len(v)})
                start = head.find(v, start + len(v))
    for d in dets:
        for m in d.pattern.finditer(head):
            raw = m.group(0)
            if d.check is not None and not d.check(raw):
                continue
            s, e = m.span(d.group) if d.group and m.group(d.group) else m.span()
            if any(s < b and e > a for a, b, _ in spans):
                continue                             # daha önce maskelenen aralıkla çakışma
            spans.append((s, e, d.type))
            hits.append({"type": d.type, "label": d.label, "kind": d.kind, "start": s, "end": e})
    if not spans:
        return [], text, partial
    out, last = [], 0
    for s, e, t in sorted(spans):
        if s < last:
            continue
        out.append(head[last:s])
        out.append(f"[redacted:{t}]")
        last = e
    out.append(head[last:])
    return hits, "".join(out) + tail, partial


def walk_strings(obj: Any, fn: Callable[[str, str], str | None], path: str = "$", depth: int = 0,
                 budget: list[int] | None = None) -> Any:
    """JSON benzeri yapıda her string yaprağa fn(path, value) uygular; None → değişmez. Sınırlı derinlik/boyut."""
    budget = budget if budget is not None else [20000]
    if depth > 12 or budget[0] <= 0:
        return obj
    if isinstance(obj, str):
        budget[0] -= 1
        r = fn(path, obj)
        return obj if r is None else r
    if isinstance(obj, dict):
        return {k: walk_strings(v, fn, f"{path}.{k}", depth + 1, budget) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        out = [walk_strings(v, fn, f"{path}[{i}]", depth + 1, budget) for i, v in enumerate(obj)]
        return out if isinstance(obj, list) else tuple(out)
    return obj


def iter_strings(obj: Any, path: str = "$", depth: int = 0) -> Iterator[tuple[str, str]]:
    if depth > 12:
        return
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, f"{path}.{k}", depth + 1)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f"{path}[{i}]", depth + 1)


# ------------------------------------------------------------------------------------------------ prompt injection
INJECTION: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"(?i)\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier|system)\b.{0,30}"
                r"\b(instruction|instructions|prompt|rules|directives?|messages?)\b"), "instruction_override", 0.9),
    (re.compile(r"(?i)\b(önceki|yukarıdaki|tüm|bütün)\b.{0,30}\b(talimat|kural|yönerge|komut)\w*\b.{0,30}"
                r"\b(yok say|unut|görmezden gel|uyma|geçersiz)\w*"), "instruction_override_tr", 0.9),
    (re.compile(r"(?i)\byou are now\b|\bact as (an?|the)\b.{0,30}\b(admin|root|system|developer|jailbroken)\b|"
                r"\bnew (system )?instructions?\s*:|\bdeveloper mode\b|\bDAN\b mode"), "role_hijack", 0.8),
    (re.compile(r"(?i)(^|\n)\s*(system|assistant|developer)\s*:\s"), "role_label", 0.5),
    (re.compile(r"(?i)</?\s*(system|instructions?|history_data|memory_data|analysis_data|context_data|"
                r"tool_result|untrusted_data)\s*>"), "prompt_delimiter", 0.7),
    (re.compile(r"(?i)\b(reveal|print|show|leak|exfiltrate|repeat|output)\b.{0,30}\b(system prompt|hidden prompt|"
                r"api[ _-]?key|secret|credential|password|token|initial instructions)s?\b"), "exfiltration", 0.8),
    (re.compile(r"(?i)\b(sistem prompt|gizli talimat)\w*\b.{0,30}\b(göster|yaz|paylaş|söyle)\w*"), "exfiltration_tr", 0.8),
    (re.compile(r"(?i)\b(call|invoke|execute|run)\b.{0,20}\b(tool|function|command|shell)\b.{0,40}"
                r"\b(without|no)\b.{0,20}\b(approval|confirmation|onay)\b"), "tool_abuse", 0.8),
    (re.compile(r"(?i)\b(disable|turn off|bypass|skip|devre dışı bırak|atla)\b.{0,30}\b(guardrail|safety|redaction|"
                r"approval|policy|risk scor\w*|validation|güvenlik|onay)\b"), "policy_bypass", 0.9),
    (re.compile(r"(?i)\b(set|change|override|değiştir)\b.{0,25}\b(risk score|risk skor\w*|severity)\b.{0,20}"
                r"\b(to|=|olarak)\b"), "score_tamper", 0.6),
]
_ZW = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def normalize(s: str) -> str:
    return _ZW.sub("", unicodedata.normalize("NFKC", s))


def injection(text: str, max_chars: int = 20000) -> list[dict]:
    """Eşleşen injection kalıpları [{rule, score, start, end}] (değer tutulmaz)."""
    if not isinstance(text, str) or not text:
        return []
    t = normalize(text[:max_chars])
    out = []
    for rx, rule, score in INJECTION:
        m = rx.search(t)
        if m:
            out.append({"rule": rule, "score": score, "start": m.start(), "end": m.end()})
    return out


def neutralize(text: str, max_chars: int = 200_000) -> tuple[str, int]:
    """Güvenilmeyen içerikteki talimat benzeri ifadeleri etkisizleştirir (sanitize)."""
    n = 0
    t = normalize(text) if len(text) <= max_chars else text
    for rx, rule, _ in INJECTION:
        t, k = rx.subn(f"[removed:untrusted-instruction:{rule}]", t)
        n += k
    return t, n


# ------------------------------------------------------------------------------------------------ niyet / işlem
DANGEROUS_REQUEST = re.compile(
    r"(?i)\b(restart|reboot|delete|drop|truncate|kill|terminate|shutdown|scale (down|to 0)|rollback|deploy|"
    r"yeniden başlat|sil|kapat|durdur|ölçekle|geri dön|deploy et)\b.{0,40}\b(service|servis|pod|db|database|"
    r"veritabanı|table|tablo|host|cluster|node|uygulama|application|prod\w*)\b")
DANGEROUS_ARG = re.compile(r"(?i)\b(delete|drop|truncate|update|insert|alter|grant|revoke|shutdown|exec|xp_cmdshell|"
                           r"rm\s+-rf|kubectl\s+(delete|scale|rollout)|systemctl\s+(stop|restart))\b")
CERTAINTY = re.compile(r"(?i)\b(kesin(likle)?|şüphesiz|kanıtlanmış|definitely|certainly|the root cause is|"
                       r"root cause (is|was)|kök sebep(i)? (kesin|budur)|kesin kök)\b")
EVIDENCE_ID = re.compile(r"\bev-[0-9a-f]{10}\b")
