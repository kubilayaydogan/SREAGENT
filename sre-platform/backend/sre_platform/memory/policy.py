"""Memory Validator + Memory Policy Manager.

Yazma (write) politikası, bir memory kaydı depoya girmeden ÖNCE uygulanır:

1. **Secret detection** — içerikte secret kalıbı (token, anahtar, JWT, private key, bearer…) varsa kayıt REDDEDİLİR
   (maskeleyip saklamak, secret'ın varlığını memory'ye taşımak olurdu). Kapatılamaz.
2. **PII redaction** — e-posta / IBAN / kart / TCKN maskelenir (ayar: pii_redaction_enabled).
3. **Prompt injection detection** — kural tabanlı kalıplar ("ignore previous instructions", "system prompt",
   rol etiketleri, araç çağrısı talimatı…). Eşleşme → reddedilir (require_validation).
4. **Memory poisoning protection** — güvenilmeyen kaynak (MCP sonucu, log, agent extraction) procedural memory'ye
   yazamaz, kendini 'verified' / 'approved' işaretleyemez; güvenilmeyen içerik retrieval'a doğrulanmadan girmez.
5. **Duplicate detection** — normalize içerik hash'i (aynı agent + namespace içinde aktif kayıt varsa yazılmaz).
6. **Boyut** — başlık 300, içerik 8000 karakter, structured_data 32 KB.

Bu kontroller kural tabanlıdır; her saldırıyı yakalamaz (bkz. MEMORY_SECURITY.md "bilinen sınırlamalar"). Memory
içeriği LLM'e her zaman VERİ bölümünde (<history_data>/<memory_data>) verilir; sistem prompt'u yetkisi kazanmaz.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from airlab_agent.redact import _PATTERNS, _extra, redact_pii

MAX_TITLE = 300
MAX_CONTENT = 8000
MAX_STRUCTURED_BYTES = 32_000

TRUSTED_SOURCES = {"manual", "import"}
UNTRUSTED_SOURCES = {"agent_extraction", "mcp", "log", "external"}
SOURCES = TRUSTED_SOURCES | UNTRUSTED_SOURCES

_INJECTION = [
    (re.compile(r"(?i)\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier)\b.{0,30}"
                r"\b(instruction|instructions|prompt|rules|directives?)\b"), "instruction_override"),
    (re.compile(r"(?i)\b(önceki|yukarıdaki|tüm)\b.{0,30}\b(talimat|kural|yönerge)\w*\b.{0,30}"
                r"\b(yok say|unut|görmezden gel|uyma)\w*"), "instruction_override_tr"),
    (re.compile(r"(?i)\byou are now\b|\bact as (an?|the)\b.{0,30}\b(admin|root|system|developer)\b|"
                r"\bnew (system )?instructions?\s*:"), "role_hijack"),
    (re.compile(r"(?i)(^|\n)\s*(system|assistant|developer)\s*:\s"), "role_label"),
    (re.compile(r"(?i)</?\s*(system|instructions?|history_data|memory_data|analysis_data|context_data)\s*>"),
     "prompt_delimiter"),
    (re.compile(r"(?i)\b(reveal|print|show|leak|exfiltrate)\b.{0,30}\b(system prompt|api[ _-]?key|secret|"
                r"credential|password|token)s?\b"), "exfiltration"),
    (re.compile(r"(?i)\b(call|invoke|execute|run)\b.{0,20}\b(tool|function|command|shell)\b.{0,40}"
                r"\b(without|no)\b.{0,20}\b(approval|confirmation|onay)\b"), "tool_abuse"),
    (re.compile(r"(?i)\b(disable|turn off|bypass|skip)\b.{0,30}\b(guardrail|safety|redaction|approval|policy|"
                r"risk scor\w*|validation)\b"), "policy_bypass"),
]


@dataclass
class PolicyResult:
    ok: bool
    title: str
    content: str
    structured_data: dict[str, Any]
    violations: list[dict[str, str]] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)
    content_hash: str = ""
    trust: str = "trusted"

    def public(self) -> dict:
        return {"ok": self.ok, "violations": self.violations, "redactions": self.redactions, "trust": self.trust,
                "content_hash": self.content_hash}


def trust_of(source: str) -> str:
    return "trusted" if source in TRUSTED_SOURCES else "untrusted"


def has_secret(text: str) -> bool:
    if any(v and v in text for v in _extra):
        return True
    return any(p.search(text) for p in _PATTERNS)


def injection_hits(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKC", text)
    return sorted({code for rx, code in _INJECTION if rx.search(norm)})


def content_hash(title: str, content: str, structured: dict | None = None) -> str:
    norm = unicodedata.normalize("NFKC", f"{title}\n{content}").casefold()
    norm = re.sub(r"\s+", " ", norm).strip()
    blob = norm + "\n" + json.dumps(structured or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _strings(o: Any):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v)


def _pii(o: Any) -> Any:
    if isinstance(o, str):
        return redact_pii(o)
    if isinstance(o, dict):
        return {k: _pii(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_pii(v) for v in o]
    return o


def validate_write(*, title: str, content: str, structured_data: dict | None, memory_type: str, source: str,
                   cfg: dict, requested_verification: str | None = None,
                   requested_approval: str | None = None) -> PolicyResult:
    """Yazma politikası. ok=False → kayıt yazılmaz (MEMORY_REJECTED / MEMORY_POLICY_VIOLATION)."""
    sd = structured_data or {}
    v: list[dict[str, str]] = []
    red: list[str] = []
    trust = trust_of(source)
    if source not in SOURCES:
        v.append({"code": "unknown_source", "detail": f"bilinmeyen kaynak: {source}"})
    if not title.strip() or not content.strip():
        v.append({"code": "empty", "detail": "başlık ve içerik zorunlu"})
    if len(title) > MAX_TITLE or len(content) > MAX_CONTENT:
        v.append({"code": "too_large", "detail": f"başlık ≤ {MAX_TITLE}, içerik ≤ {MAX_CONTENT} karakter"})
    if len(json.dumps(sd, ensure_ascii=False, default=str).encode()) > MAX_STRUCTURED_BYTES:
        v.append({"code": "too_large", "detail": f"structured_data ≤ {MAX_STRUCTURED_BYTES} bayt"})
    texts = [title, content, *_strings(sd)]
    if any(has_secret(t) for t in texts):                                   # kapatılamaz
        v.append({"code": "secret_detected", "detail": "içerikte secret kalıbı var; memory'ye secret yazılmaz"})
    if cfg.get("require_validation", True):
        hits = sorted({h for t in texts for h in injection_hits(t)})
        if hits:
            v.append({"code": "prompt_injection", "detail": "talimat benzeri içerik: " + ", ".join(hits)})
    if cfg.get("poisoning_protection_enabled", True) and trust == "untrusted":
        if memory_type == "procedural":
            v.append({"code": "untrusted_procedural",
                      "detail": "güvenilmeyen kaynak procedural memory (talimat) yazamaz"})
        if requested_verification == "verified" or requested_approval == "approved":
            v.append({"code": "untrusted_self_verify",
                      "detail": "güvenilmeyen kaynak kendini verified / approved işaretleyemez"})
    if cfg.get("pii_redaction_enabled", True):
        t2, c2, sd2 = redact_pii(title), redact_pii(content), _pii(sd)
        if (t2, c2) != (title, content) or sd2 != sd:
            red.append("pii")
        title, content, sd = t2, c2, sd2
    title, content = _guardrail(title, content, memory_type, source, v)
    return PolicyResult(ok=not v, title=title, content=content, structured_data=sd, violations=v, redactions=red,
                        content_hash=content_hash(title, content, sd), trust=trust)


def _guardrail(title: str, content: str, memory_type: str, source: str, v: list[dict]) -> tuple[str, str]:
    """Guardrails › memory: validator kararı kaydedilir (bound policy) ve memory_write policy'leri değerlendirilir.
    Rol koşu bağlamından gelir; koşu dışı yazma (API, RBAC ile yetkilendirilmiş) ve agent extraction 'system'dir."""
    try:
        from airlab_agent.guardrails import runtime as gr
        from airlab_agent.guardrails.model import GuardrailBlocked
    except Exception:  # noqa: BLE001
        return title, content
    gr.observe("memory:validate_write", decision="blocked" if v else "passed", stage="memory_write",
               reason="; ".join(x["detail"] for x in v)[:300] if v else None, code=v[0]["code"] if v else None)
    if v:
        return title, content
    run = gr.current()
    role = run.ctx.user_role if run is not None and source in TRUSTED_SOURCES else "system"
    try:
        out = gr.memory_write(title=title, content=content, memory_type=memory_type, source=source, user_role=role)
    except GuardrailBlocked as e:
        v.append({"code": e.code, "detail": f"guardrail: {str(e)[:300]}"})
        return title, content
    if isinstance(out.payload, dict) and out.changed:
        return str(out.payload.get("title", title)), str(out.payload.get("content", content))
    return title, content


def retrievable(rec: dict, cfg: dict) -> tuple[bool, str | None]:
    """Agent retrieval'ına girebilir mi? (Validation aşaması) — (evet/hayır, dışlanma nedeni)."""
    if rec.get("status") not in (None, "active"):
        return False, f"status={rec.get('status')}"
    if rec.get("verification_status") == "rejected":
        return False, "verification=rejected"
    if rec.get("memory_type") == "procedural" and rec.get("approval_status") not in ("approved",):
        return False, f"procedural approval={rec.get('approval_status')}"
    if (cfg.get("require_evidence_verification", True) and rec.get("source_trust") == "untrusted"
            and rec.get("verification_status") != "verified"):
        return False, "untrusted ve doğrulanmamış"
    return True, None
