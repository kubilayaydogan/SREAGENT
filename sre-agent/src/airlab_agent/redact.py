"""Gizli bilgi ve hassas veri temizliği. Rapor, log ve LangSmith trace'lerine giden her şey buradan geçer."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"dt0[a-z]\d{2}\.[A-Za-z0-9._-]+"),            # Dynatrace token / token kimliği
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),                  # Anthropic anahtarı
    re.compile(r"lsv2_[A-Za-z0-9_]{10,}"),                     # LangSmith anahtarı
    re.compile(r"(?i)(authorization|api[-_]?key|password|passwd|secret|token)(\"?\s*[:=]\s*\"?)[^\s\",}]+"),
]
_extra: list[str] = []


def register_secrets(values: list[str]) -> None:
    """Ortamdan okunan gerçek gizli değerleri birebir maskelemek için kaydeder."""
    for v in values:
        if v and len(v) >= 8 and v not in _extra:
            _extra.append(v)


def redact_text(s: str) -> str:
    for v in _extra:
        s = s.replace(v, "***")
    for p in _PATTERNS:
        s = p.sub(lambda m: (m.group(1) + m.group(2) + "***") if m.lastindex and m.lastindex >= 2 else "***", s)
    return s


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


def strip_fields(records: list[dict], fields: list[str]) -> list[dict]:
    """Değerlendirme/kaos etiketlerini gözlem verisinden çıkarır (ör. experiment_id, lab_hold)."""
    drop = set(fields)
    return [{k: v for k, v in r.items() if k not in drop and k.split(".")[-1] not in drop} for r in records]
