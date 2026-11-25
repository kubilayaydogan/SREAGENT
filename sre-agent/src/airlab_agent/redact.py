"""Gizli bilgi ve hassas veri temizliği (DLP). Rapor, log, LLM, LangSmith trace'i, bildirim ve audit'e giden her şey
buradan geçer.

İki sınıf:
* **Secret** (token, anahtar, JWT, bearer, webhook, private key): her sink'te HER ZAMAN maskelenir.
* **PII** (e-posta, IBAN, kart numarası, TC kimlik no): sink politikasına göre maskelenir. Varsayılan: tüm sink'lerde
  maskeli (secure by default). `SRE_DLP_PII_KEEP_SINKS=storage,...` yalnız açıkça adı verilen sink'te korur.
  Sayısal kalıplar checksum ile doğrulanır (IBAN mod-97, kart Luhn + IIN, TCKN algoritması): telemetrideki zaman
  damgası / kimlik gibi sayılar yanlışlıkla maskelenmez.
"""
from __future__ import annotations

import os
import re
from typing import Any

_PATTERNS = [
    re.compile(r"dt0[a-z]\d{2}\.[A-Za-z0-9._-]+"),            # Dynatrace token / token kimliği
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),                  # Anthropic anahtarı
    re.compile(r"lsv2_[A-Za-z0-9_]{10,}"),                     # LangSmith anahtarı
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),   # JWT
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"),               # Slack webhook
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                                    # AWS erişim anahtarı
    re.compile(r"(?i)\b(bearer)(\s+)[A-Za-z0-9._~+/=-]{16,}"),                       # Bearer <token>
    re.compile(r"(?i)(authorization|api[-_]?key|password|passwd|secret|token)(\"?\s*[:=]\s*\"?)[^\s\",}]+"),
]
_extra: list[str] = []

# ------------------------------------------------------------------------------------------------ PII
SINKS = ("llm", "trace", "log", "notify", "export", "storage", "audit")


def _keep_sinks(raw: str) -> frozenset[str]:
    return frozenset(x.strip() for x in raw.split(",") if x.strip()) & frozenset(SINKS)


_PII_KEEP = _keep_sinks(os.getenv("SRE_DLP_PII_KEEP_SINKS", ""))

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_CARD = re.compile(r"(?<![\d.-])\d(?:[ -]?\d){12,18}(?![\d.-])")
_TCKN = re.compile(r"(?<![\w.-])[1-9]\d{10}(?![\w.-])")
_IIN = re.compile(r"^(4|5[1-5]|2[2-7]|3[47]|6011|65)")


def _luhn(d: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    r = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in r)) % 97 == 1


def _tckn_ok(d: str) -> bool:
    n = [int(c) for c in d]
    return (sum(n[0:9:2]) * 7 - sum(n[1:8:2])) % 10 == n[9] and sum(n[:10]) % 10 == n[10]


def _card(m: re.Match) -> str:
    d = re.sub(r"[ -]", "", m.group(0))
    return "[card]" if 13 <= len(d) <= 19 and _IIN.match(d) and _luhn(d) else m.group(0)


def redact_pii(s: str) -> str:
    s = _EMAIL.sub("[email]", s)
    s = _IBAN.sub(lambda m: "[iban]" if _iban_ok(m.group(0)) else m.group(0), s)
    s = _CARD.sub(_card, s)
    return _TCKN.sub(lambda m: "[tckn]" if _tckn_ok(m.group(0)) else m.group(0), s)


def dlp_policy() -> dict[str, dict[str, bool]]:
    """Sink başına politika (salt okunur görünüm)."""
    return {k: {"secrets": True, "pii": k not in _PII_KEEP} for k in SINKS}


def register_secrets(values: list[str]) -> None:
    """Ortamdan okunan gerçek gizli değerleri birebir maskelemek için kaydeder."""
    for v in values:
        if v and len(v) >= 8 and v not in _extra:
            _extra.append(v)


def redact_text(s: str, sink: str = "storage") -> str:
    for v in _extra:
        s = s.replace(v, "***")
    for p in _PATTERNS:
        s = p.sub(lambda m: (m.group(1) + m.group(2) + "***") if m.lastindex and m.lastindex >= 2 else "***", s)
    if sink not in _PII_KEEP:              # bilinmeyen sink adı da maskelenir (fail-closed)
        s = redact_pii(s)
    return s


def redact(obj: Any, sink: str = "storage") -> Any:
    if isinstance(obj, str):
        return redact_text(obj, sink)
    if isinstance(obj, dict):
        return {k: redact(v, sink) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, sink) for v in obj]
    return obj


def strip_fields(records: list[dict], fields: list[str]) -> list[dict]:
    """Değerlendirme/kaos etiketlerini gözlem verisinden çıkarır (ör. experiment_id, lab_hold)."""
    drop = set(fields)

    def hidden(k: str) -> bool:
        # tam ad, son segment veya iç içe sonek (waiter.chaos.scenario) eşleşmesi; chaos.* her zaman gizlenir
        return (k in drop or k.split(".")[-1] in drop or any(k.endswith("." + f) for f in drop)
                or k.startswith("chaos.") or ".chaos." in k)

    return [{k: _mask_op(k, v) for k, v in r.items() if not hidden(k)} for r in records]


# Kaos/lab workload'ları SQL yorumuna op=chaos-longtx, op=lab_hold_checkin gibi etiketler yazar; collector bunları
# waiter.op/blocker.op olarak taşır. Alan adı masum olsa da DEĞER cevabı verir → değer maskelenir (alan kalır).
_LAB_OP = re.compile(r"(?i)^(chaos|lab)[-_.:]")


def _mask_op(k: str, v: Any) -> Any:
    last = k.split(".")[-1]
    if (last == "op" or last.endswith("_op")) and isinstance(v, str) and _LAB_OP.match(v):
        return "unlabeled"
    return v
