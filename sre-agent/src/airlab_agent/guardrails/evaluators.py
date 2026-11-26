"""Guardrail evaluator'ları: ortak protokol + kayıt (registry).

Her evaluator saf ve deterministiktir: `evaluate(payload, ctx, params, state) -> Finding`. Policy aksiyonu (block,
redact, ...) evaluator'ın değil engine'in işidir; evaluator yalnız "eşleşti mi, neden, dönüştürülmüş hali ne" der.
Yeni evaluator eklemek için `Evaluator` alt sınıfı yazıp `register` ile kaydetmek yeterlidir; engine değişmez.

Kimlik doğrulama, yetki, tenant izolasyonu ve tool izinleri LLM'e sorulmaz; model tabanlı sınıflandırıcı bu sürümde
yoktur (Settings'te `classifier.mode = deterministic`).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import detect
from .model import EvalContext, Finding


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evaluator:
    type: ClassVar[str]
    title: ClassVar[str]
    category: ClassVar[str]
    stages: ClassVar[tuple[str, ...]]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]] = NoParams
    actions: ClassVar[tuple[str, ...]] = ("block", "warn", "allow")      # anlamlı aksiyonlar
    model_based: ClassVar[bool] = False

    def evaluate(self, payload: Any, ctx: EvalContext, p: BaseModel, state: Any) -> Finding:  # pragma: no cover
        raise NotImplementedError


REGISTRY: dict[str, Evaluator] = {}


def register(cls):
    REGISTRY[cls.type] = cls()
    return cls


def describe(t: str) -> dict:
    e = REGISTRY[t]
    return {"type": e.type, "title": e.title, "category": e.category, "stages": list(e.stages),
            "description": e.description, "actions": list(e.actions), "model_based": e.model_based,
            "params_schema": e.Params.model_json_schema()}


def _text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        t = payload.get("text")
        if isinstance(t, str):
            return t
    return ""


def _with_text(payload: Any, text: str) -> Any:
    if isinstance(payload, dict):
        return {**payload, "text": text}
    return text


# ================================================================================================= input
@register
class InputLength(Evaluator):
    type, title, category = "input_length", "Maximum input length", "input"
    stages = ("before_agent",)
    description = "Kullanıcı / schedule girdisinin karakter uzunluğu sınırı."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_chars: int = Field(8000, ge=10, le=200_000)

    def evaluate(self, payload, ctx, p, state):
        n = len(_text(payload))
        if n > p.max_chars:
            return Finding(True, f"girdi {n} karakter (sınır {p.max_chars})", "input_too_long",
                           thresholds={"max_chars": {"limit": p.max_chars, "actual": n}})
        return Finding(False)


@register
class InvalidInput(Evaluator):
    type, title, category = "invalid_input", "Invalid input detection", "input"
    stages = ("before_agent",)
    description = "Boş girdi, kontrol karakterleri ve görünmez (zero-width / bidi) karakterler."
    actions = ("block", "sanitize", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        allow_empty: bool = True

    _CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def evaluate(self, payload, ctx, p, state):
        t = _text(payload)
        if not t.strip():
            return Finding(not p.allow_empty, "boş girdi", "empty_input")
        ctrl = len(self._CTRL.findall(t))
        zw = len(detect._ZW.findall(t))
        if ctrl or zw:
            clean = detect._ZW.sub("", self._CTRL.sub("", t))
            return Finding(True, f"{ctrl} kontrol, {zw} görünmez karakter", "invalid_chars",
                           matches=[{"control_chars": ctrl, "invisible_chars": zw}], transformed=_with_text(payload, clean),
                           changed=["$.text"], can_transform=True)
        return Finding(False)


@register
class InputSchema(Evaluator):
    type, title, category = "input_schema", "Input schema validation", "input"
    stages = ("before_agent",)
    description = "Agent'ın tanımlı input şemasına (JSON Schema) göre girdi değişkenleri doğrulanır."

    def evaluate(self, payload, ctx, p, state):
        schema = (payload or {}).get("input_schema") if isinstance(payload, dict) else None
        if not schema:
            return Finding(False, "şema tanımlı değil")
        import jsonschema
        data = {"input": _text(payload), **((payload or {}).get("variables") or {})}
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            return Finding(True, f"şema ihlali: {e.message[:200]}", "input_schema",
                           matches=[{"path": "/".join(str(x) for x in e.absolute_path) or "$"}])
        return Finding(False)


@register
class PromptInjection(Evaluator):
    type, title, category = "prompt_injection", "Direct prompt injection detection", "input"
    stages = ("before_agent",)
    description = ("Talimatları geçersiz kılma, rol ele geçirme, prompt sınırlayıcı, sızdırma ve policy atlatma "
                   "kalıpları (kural tabanlı; skor threshold'u).")
    actions = ("block", "sanitize", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        min_score: float = Field(0.7, ge=0.1, le=1.0)

    def evaluate(self, payload, ctx, p, state):
        t = _text(payload)
        hits = [h for h in detect.injection(t) if h["score"] >= p.min_score]
        if not hits:
            return Finding(False)
        clean, _ = detect.neutralize(t)
        return Finding(True, "prompt injection kalıbı: " + ", ".join(h["rule"] for h in hits), "prompt_injection",
                       matches=[{"rule": h["rule"], "score": h["score"]} for h in hits],
                       thresholds={"min_score": {"limit": p.min_score, "actual": max(h["score"] for h in hits)}},
                       transformed=_with_text(payload, clean), changed=["$.text"], can_transform=True)


@register
class UnsupportedRequest(Evaluator):
    type, title, category = "unsupported_request", "Intent classification / unsupported request", "input"
    stages = ("before_agent",)
    description = ("Deterministik niyet sınıflandırma: agent salt okunurdur; restart / delete / deploy gibi eylem "
                   "istekleri desteklenmez. Model tabanlı sınıflandırıcı bu sürümde yok.")
    actions = ("warn", "block")

    def evaluate(self, payload, ctx, p, state):
        m = detect.DANGEROUS_REQUEST.search(detect.normalize(_text(payload)[:20000]))
        if m:
            return Finding(True, "eylem isteği (agent salt okunur; eylem yürütmez)", "action_request",
                           matches=[{"intent": "operational_action", "verb": m.group(1).lower()}])
        return Finding(False)


# ================================================================================================= model / context
@register
class ContextSize(Evaluator):
    type, title, category = "context_size", "Context size validation", "model_context"
    stages = ("llm_request",)
    description = "LLM'e gidecek tahmini girdi token'ı (karakter/4) sınırı; model kapasitesinden bağımsız policy sınırı."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_input_tokens: int = Field(400_000, ge=1000, le=2_000_000)

    def evaluate(self, payload, ctx, p, state):
        est = int((payload or {}).get("est_tokens") or 0)
        if est > p.max_input_tokens:
            return Finding(True, f"tahmini girdi {est} token (sınır {p.max_input_tokens})", "context_too_large",
                           thresholds={"max_input_tokens": {"limit": p.max_input_tokens, "actual": est}})
        return Finding(False)


@register
class SystemPromptProtection(Evaluator):
    type, title, category = "system_prompt_protection", "System prompt protection", "model_context"
    stages = ("llm_request", "llm_response")
    description = ("İstek: veri içine gömülmüş prompt sınırlayıcıları ve rol etiketleri etkisizleştirilir (platformun "
                   "kendi veri sarmalayıcısı korunur). "
                   "Yanıt: system prompt'un uzun bir parçasının yanıtta tekrarlanması (sızıntı) tespit edilir.")
    actions = ("sanitize", "block", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        leak_min_chars: int = Field(120, ge=40, le=2000)

    _DELIM = re.compile(r"(?i)</?\s*(system|instructions?|history_data|memory_data|analysis_data|context_data|"
                        r"tool_result|untrusted_data)\s*>")
    _ROLE = ("system", "instruction", "instructions")

    @classmethod
    def _strip(cls, text: str) -> str:
        """Platformun kendi veri sarmalayıcısı (her bölüm adı için ilk açılış + son kapanış) korunur; veri içine
        gömülmüş fazladan / iç içe sınırlayıcılar ve rol etiketleri (system / instructions) etkisizleştirilir."""
        ms = list(cls._DELIM.finditer(text))
        keep: set[int] = set()
        for name in {m.group(1).lower() for m in ms} - set(cls._ROLE):
            opens = [m for m in ms if m.group(1).lower() == name and not m.group(0).lstrip("<").lstrip().startswith("/")]
            closes = [m for m in ms if m.group(1).lower() == name and m.group(0).lstrip("<").lstrip().startswith("/")]
            if opens and closes and opens[0].start() < closes[-1].start():
                keep.update((opens[0].start(), closes[-1].start()))
        out, pos = [], 0
        for m in ms:
            if m.start() in keep:
                continue
            out.append(text[pos:m.start()])
            out.append("[removed:prompt-delimiter]")
            pos = m.end()
        out.append(text[pos:])
        return "".join(out)

    def evaluate(self, payload, ctx, p, state):
        if ctx.stage == "llm_request":
            msgs = (payload or {}).get("messages") or []
            out, changed = [], []
            for i, m in enumerate(msgs):
                role, text = (m[0], m[1]) if isinstance(m, (list, tuple)) and len(m) == 2 else (None, m)
                if role != "system" and isinstance(text, str) and self._DELIM.search(text):
                    new = self._strip(text)
                    if new != text:
                        text = new
                        changed.append(f"$.messages[{i}]")
                out.append((role, text) if role is not None else text)
            if changed:
                return Finding(True, "veri içinde prompt sınırlayıcısı", "prompt_delimiter",
                               matches=[{"messages": len(changed)}], transformed={**payload, "messages": out},
                               changed=changed, can_transform=True)
            return Finding(False)
        sys_text = (payload or {}).get("system") or ""
        t = _text(payload)
        if len(sys_text) >= p.leak_min_chars and t:
            step = p.leak_min_chars
            for i in range(0, len(sys_text) - step + 1, max(step // 2, 20)):
                if sys_text[i:i + step] in t:
                    return Finding(True, "yanıt system prompt'un bir bölümünü içeriyor", "system_prompt_leak",
                                   matches=[{"overlap_chars": step}],
                                   fallback="Bu yanıt güvenlik nedeniyle gösterilmedi (system prompt sızıntısı).")
        return Finding(False)


@register
class ModelOutputValidation(Evaluator):
    type, title, category = "model_output_validation", "Model input/output validation", "model_context"
    stages = ("llm_response",)
    description = "Boş yanıt ve yanıt uzunluğu sınırı (model çıktısı sonraki adımlara girmeden)."
    actions = ("warn", "block")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_output_chars: int = Field(200_000, ge=100, le=2_000_000)

    def evaluate(self, payload, ctx, p, state):
        t = _text(payload)
        if (payload or {}).get("structured"):
            return Finding(False, "yapılandırılmış çıktı (şema ayrıca doğrulanır)")
        if not t.strip():
            return Finding(True, "boş model yanıtı", "empty_output")
        if len(t) > p.max_output_chars:
            return Finding(True, f"yanıt {len(t)} karakter", "output_too_long",
                           thresholds={"max_output_chars": {"limit": p.max_output_chars, "actual": len(t)}})
        return Finding(False)


# ================================================================================================= data protection
class _Sensitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    types: list[str] = Field(default_factory=lambda: list(detect.SECRET_TYPES + detect.PII_TYPES))
    custom_patterns: list[dict] = Field(default_factory=list, max_length=30)
    max_scan_chars: int = Field(detect.MAX_SCAN_CHARS, ge=1000, le=2_000_000)

    @field_validator("types")
    @classmethod
    def _t(cls, v):
        bad = [x for x in v if x not in detect.DETECTOR_TYPES]
        if bad:
            raise ValueError(f"bilinmeyen tür: {bad}")
        return v

    @field_validator("custom_patterns")
    @classmethod
    def _c(cls, v):
        for c in v:
            if not isinstance(c, dict) or set(c) - {"name", "label", "pattern", "kind"}:
                raise ValueError("custom pattern alanları: name, label, pattern, kind")
            detect.validate_custom_pattern(c.get("pattern", ""))
            if c.get("kind", "secret") not in ("secret", "pii"):
                raise ValueError("kind: secret | pii")
        return v


@register
class SensitiveData(Evaluator):
    type, title, category = "sensitive_data", "Sensitive data detection & redaction", "data_protection"
    stages = ("llm_request", "llm_response", "mcp_response", "tool_response", "memory_write", "checkpoint",
              "final_output")
    description = ("Secret (API key, token, bearer, parola, connection string, authorization header, session id, "
                   "private key) ve PII (e-posta, TCKN, kart, IBAN) + yapılandırılabilir kalıplar. Değerler kayda "
                   "girmez; yalnız tür ve konum.")
    Params = _Sensitive
    actions = ("redact", "block", "warn", "quarantine")

    def evaluate(self, payload, ctx, p, state):
        hits_all: list[dict] = []
        changed: list[str] = []
        partial = [False]

        def fn(path, s):
            hits, red, part = detect.scan(s, p.types, p.custom_patterns, p.max_scan_chars)
            partial[0] = partial[0] or part
            if hits:
                for h in hits:
                    hits_all.append({"path": path, "type": h["type"], "kind": h["kind"]})
                changed.append(path)
                return red
            return None
        # Taranan alan: "data" varsa o, yoksa "text", yoksa tüm payload (ör. MCP kayıtları, memory kaydı)
        field = next((k for k in ("data", "text") if isinstance(payload, dict) and k in payload), None)
        data = payload[field] if field else payload
        out = detect.walk_strings(data, fn, "$." + field if field else "$")
        if not hits_all:
            return Finding(False, "kısmi tarama (sınır)" if partial[0] else "")
        types = sorted({h["type"] for h in hits_all})
        transformed = {**payload, field: out} if field else out
        return Finding(True, f"hassas veri: {', '.join(types)}", "sensitive_data", matches=hits_all[:50],
                       transformed=transformed, changed=changed[:50], can_transform=True)


# ================================================================================================= tool / mcp
@register
class ToolDenylist(Evaluator):
    type, title, category = "tool_denylist", "Tool denylist", "tool"
    stages = ("tool_request", "mcp_request")
    description = "Adı listede olan tool hiçbir agent tarafından çağrılamaz (allowlist'ten bağımsız)."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tools: list[str] = Field(default_factory=list, max_length=200)

    def evaluate(self, payload, ctx, p, state):
        if ctx.tool and ctx.tool in p.tools:
            return Finding(True, f"'{ctx.tool}' denylist'te", "tool_denied", matches=[{"tool": ctx.tool}])
        return Finding(False)


@register
class McpServerAllowlist(Evaluator):
    type, title, category = "mcp_server_allowlist", "MCP server allowlist", "mcp"
    stages = ("mcp_request",)
    description = "Yalnız listedeki MCP sunucularına istek gönderilir."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        servers: list[str] = Field(default_factory=lambda: ["dynatrace"], max_length=50)

    def evaluate(self, payload, ctx, p, state):
        if ctx.mcp_server not in p.servers:
            return Finding(True, f"MCP sunucusu '{ctx.mcp_server}' izinli değil", "server_not_allowed",
                           matches=[{"server": ctx.mcp_server}])
        return Finding(False)


@register
class ReadOnlyEnforcement(Evaluator):
    type, title, category = "read_only_enforcement", "Read-only policy enforcement", "mcp"
    stages = ("tool_request", "mcp_request")
    description = ("Yalnız platformun doğrulanmış salt-okuma adapter'ı olan tool'lar çağrılabilir. MCP sunucusunun "
                   "bildirdiği metadata / description (read_only annotation) yetki VERMEZ; yalnız riski artırabilir.")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        read_only_tools: list[str] = Field(default_factory=lambda: ["execute-dql"], max_length=50)

    def evaluate(self, payload, ctx, p, state):
        if ctx.tool not in p.read_only_tools:
            return Finding(True, f"'{ctx.tool}' doğrulanmış salt-okuma tool'u değil", "not_read_only",
                           matches=[{"tool": ctx.tool}])
        meta = (payload or {}).get("server_annotations") or {}
        if meta.get("destructiveHint") or meta.get("readOnlyHint") is False:
            return Finding(True, "sunucu tool'u yazma yetenekli bildiriyor", "server_declares_write",
                           matches=[{"tool": ctx.tool}])
        return Finding(False)


@register
class ToolRbac(Evaluator):
    type, title, category = "tool_rbac", "Tool authorization (RBAC)", "tool"
    stages = ("tool_request", "mcp_request")
    description = ("Tool risk sınıfı → izinli roller. Rol koşu bağlamından (doğrulanmış kullanıcı / system) gelir; "
                   "LLM ya da istemci rolü belirleyemez. Sınıflandırılmamış tool 'high' sayılır.")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tool_risk: dict[str, str] = Field(default_factory=lambda: {"execute-dql": "low"})
        roles_by_risk: dict[str, list[str]] = Field(default_factory=lambda: {
            "low": ["viewer", "sre", "admin", "system"], "medium": ["sre", "admin", "system"],
            "high": ["admin"], "critical": []})

    def evaluate(self, payload, ctx, p, state):
        risk = p.tool_risk.get(ctx.tool or "", "high")
        role = ctx.user_role or "anonymous"
        if role not in p.roles_by_risk.get(risk, []):
            return Finding(True, f"rol '{role}' {risk} riskli tool '{ctx.tool}' için yetkili değil", "tool_unauthorized",
                           matches=[{"tool": ctx.tool, "risk": risk, "role": role}])
        return Finding(False)


@register
class TenantIsolation(Evaluator):
    type, title, category = "tenant_isolation", "Tenant isolation", "mcp"
    stages = ("mcp_request", "tool_request", "memory_read", "memory_write")
    description = "İstek / kayıt tenant'ı koşu tenant'ıyla aynı olmalı (tek organizasyon modelinde 'default')."
    actions = ("block", "quarantine")

    def evaluate(self, payload, ctx, p, state):
        if ctx.stage == "memory_read":
            items = (payload or {}).get("items") or []
            bad = [i for i, x in enumerate(items) if (x.get("tenant_id") or "default") != ctx.tenant_id]
            if bad:
                keep = [x for i, x in enumerate(items) if i not in bad]
                return Finding(True, f"{len(bad)} kayıt başka tenant'a ait", "cross_tenant",
                               matches=[{"items": len(bad)}], transformed={**payload, "items": keep},
                               changed=[f"$.items[{i}]" for i in bad], can_transform=True)
            return Finding(False)
        t = (payload or {}).get("tenant_id") or ctx.tenant_id
        if t != ctx.tenant_id:
            return Finding(True, "kaynak tenant'ı koşu tenant'ından farklı", "cross_tenant")
        return Finding(False)


@register
class DangerousOperation(Evaluator):
    type, title, category = "dangerous_operation", "Dangerous operation detection", "tool"
    stages = ("tool_request", "mcp_request")
    description = "Tool argümanlarında yazma / yıkıcı işlem ifadeleri (DELETE, DROP, kubectl delete, rm -rf ...)."

    def evaluate(self, payload, ctx, p, state):
        for path, s in detect.iter_strings((payload or {}).get("args") or {}):
            m = detect.DANGEROUS_ARG.search(s[:20000])
            if m:
                return Finding(True, f"argümanda tehlikeli işlem: {m.group(1).lower()}", "dangerous_operation",
                               matches=[{"path": path, "operation": m.group(1).lower()}])
        return Finding(False)


@register
class McpRequestValidation(Evaluator):
    type, title, category = "mcp_request_validation", "MCP request / argument validation", "mcp"
    stages = ("mcp_request", "tool_request")
    description = ("Argüman yapısı tool'un platformda kayıtlı şemasına uyar (execute-dql: dqlQueryString zorunlu, "
                   "bilinmeyen alan yok, boyut sınırı).")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_arg_chars: int = Field(20000, ge=100, le=200_000)
        schemas: dict[str, dict] = Field(default_factory=lambda: {"execute-dql": {
            "required": ["dqlQueryString"], "allowed": ["dqlQueryString", "includeTypes"]}})

    def evaluate(self, payload, ctx, p, state):
        args = (payload or {}).get("args")
        if not isinstance(args, dict):
            return Finding(True, "argümanlar nesne değil", "invalid_args")
        size = len(json.dumps(args, default=str))
        if size > p.max_arg_chars:
            return Finding(True, f"argüman boyutu {size}", "args_too_large",
                           thresholds={"max_arg_chars": {"limit": p.max_arg_chars, "actual": size}})
        sc = p.schemas.get(ctx.tool or "")
        if sc:
            miss = [k for k in sc.get("required", []) if not isinstance(args.get(k), str) or not args.get(k)]
            extra = [k for k in args if k not in sc.get("allowed", [])]
            if miss or extra:
                return Finding(True, f"şema ihlali (eksik: {miss}, bilinmeyen: {extra})", "args_schema",
                               matches=[{"missing": miss, "unknown": extra}])
        return Finding(False)


_REL = re.compile(r"from\s*:\s*now\(\)\s*-\s*(\d+)\s*([smhdw])\b", re.I)
_ABS = re.compile(r'from\s*:\s*"([^"]+)"\s*,\s*to\s*:\s*"([^"]+)"', re.I)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _iso(s: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@register
class QueryTimeRange(Evaluator):
    type, title, category = "query_time_range", "Query time range limit", "mcp"
    stages = ("mcp_request",)
    description = "DQL'deki zaman aralığı (from:now()-N / from..to) sınırı; ayrıştırılamayan aralık değerlendirilmez."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_days: float = Field(31, ge=0.01, le=400)

    def evaluate(self, payload, ctx, p, state):
        dql = (payload or {}).get("dql") or ""
        spans = [int(n) * _UNIT[u.lower()] for n, u in _REL.findall(dql)]
        for a, b in _ABS.findall(dql):
            x, y = _iso(a), _iso(b)
            if x and y:
                spans.append(int((y - x).total_seconds()))
        if not spans:
            return Finding(False, "zaman aralığı ayrıştırılamadı")
        days = max(spans) / 86400
        if days > p.max_days:
            return Finding(True, f"zaman aralığı {days:.1f} gün (sınır {p.max_days})", "time_range",
                           thresholds={"max_days": {"limit": p.max_days, "actual": round(days, 2)}})
        return Finding(False)


@register
class McpResponseLimits(Evaluator):
    type, title, category = "mcp_response_limits", "MCP response size & record limits", "mcp"
    stages = ("mcp_response", "tool_response")
    description = ("Kayıt sayısı ve serileştirilmiş boyut sınırı. sanitize/quarantine: sınırı aşan kayıtlar atılır "
                   "(gateway'in sabit üst sınırına ek, daha kısıtlayıcı policy sınırı).")
    actions = ("sanitize", "block", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_records: int = Field(5000, ge=1, le=5000)
        max_chars: int = Field(4_000_000, ge=1000, le=4_000_000)

    def evaluate(self, payload, ctx, p, state):
        recs = (payload or {}).get("records") or []
        n = len(recs)
        size = len(json.dumps(recs, default=str)) if n else 0
        th = {}
        if n > p.max_records:
            th["max_records"] = {"limit": p.max_records, "actual": n}
        if size > p.max_chars:
            th["max_chars"] = {"limit": p.max_chars, "actual": size}
        if not th:
            return Finding(False)
        keep, total = [], 0
        for r in recs[:p.max_records]:
            total += len(json.dumps(r, default=str))
            if total > p.max_chars:
                break
            keep.append(r)
        return Finding(True, f"yanıt sınırı aşıldı ({n} kayıt, {size} karakter)", "response_limit", thresholds=th,
                       transformed={**payload, "records": keep}, changed=["$.records"], can_transform=True)


@register
class McpResponseValidation(Evaluator):
    type, title, category = "mcp_response_validation", "MCP response validation", "mcp"
    stages = ("mcp_response", "tool_response")
    description = "Yanıt biçimi: bilinen durum, kayıtlar nesne listesi, kayıt başına alan sayısı sınırı."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_fields_per_record: int = Field(200, ge=1, le=5000)

    def evaluate(self, payload, ctx, p, state):
        st = (payload or {}).get("status")
        recs = (payload or {}).get("records")
        if st not in ("OK", "EMPTY", "ERROR", "PERMISSION", "UNAVAILABLE", "TIMEOUT", "SKIPPED_BUDGET", "CANCELLED"):
            return Finding(True, f"bilinmeyen yanıt durumu: {st}", "invalid_status")
        if recs is not None and (not isinstance(recs, list) or any(not isinstance(r, dict) for r in recs)):
            return Finding(True, "kayıtlar nesne listesi değil", "invalid_records")
        wide = sum(1 for r in recs or [] if len(r) > p.max_fields_per_record)
        if wide:
            return Finding(True, f"{wide} kayıt alan sınırını aşıyor", "too_many_fields",
                           thresholds={"max_fields_per_record": {"limit": p.max_fields_per_record, "actual": wide}})
        return Finding(False)


@register
class IndirectInjection(Evaluator):
    type, title, category = "indirect_injection", "Indirect prompt injection (untrusted data)", "mcp"
    stages = ("mcp_response", "tool_response", "memory_read")
    description = ("MCP yanıtları ve memory kayıtları güvenilmeyen dış veridir: içlerindeki talimat benzeri ifadeler "
                   "etkisizleştirilir (sanitize) ya da kayıt karantinaya alınır (quarantine).")
    actions = ("sanitize", "quarantine", "warn", "block")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        min_score: float = Field(0.7, ge=0.1, le=1.0)
        quarantine_items: bool = False          # memory_read: eşleşen kaydı tamamen çıkar

    def evaluate(self, payload, ctx, p, state):
        key = "items" if ctx.stage == "memory_read" else "records"
        items = (payload or {}).get(key) or []
        hits: list[dict] = []
        out = []
        for i, it in enumerate(items):
            found = []

            def fn(path, s, i=i, found=found):
                hs = [h for h in detect.injection(s) if h["score"] >= p.min_score]
                if hs:
                    found.extend({"path": f"$.{key}[{i}]{path[1:]}", "rule": h["rule"]} for h in hs)
                    return detect.neutralize(s)[0]
                return None
            new = detect.walk_strings(it, fn)
            if found:
                hits.extend(found)
                if p.quarantine_items:
                    continue
            out.append(new)
        if not hits:
            return Finding(False)
        return Finding(True, f"güvenilmeyen içerikte talimat benzeri ifade ({len(hits)})", "indirect_injection",
                       matches=hits[:50], transformed={**payload, key: out},
                       changed=sorted({h["path"] for h in hits})[:50], can_transform=True)


@register
class RateLimit(Evaluator):
    type, title, category = "rate_limit", "MCP rate limiting", "mcp"
    stages = ("mcp_request",)
    description = ("Sunucu başına kayan pencere (süreç içi) ve koşu başına dakikalık istek sınırı. Süreçler arası "
                   "paylaşılmaz (backend ve worker ayrı sayar).")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        per_minute_per_server: int = Field(600, ge=1, le=10000)

    def evaluate(self, payload, ctx, p, state):
        n = state.rate_hit(ctx.mcp_server or "-") if state is not None else 0
        if n > p.per_minute_per_server:
            return Finding(True, f"son 60 sn'de {n} istek", "rate_limited",
                           thresholds={"per_minute_per_server": {"limit": p.per_minute_per_server, "actual": n}})
        return Finding(False)


# ================================================================================================= output
@register
class OutputSchema(Evaluator):
    type, title, category = "output_schema", "Structured output validation", "output"
    stages = ("llm_response", "final_output")
    description = "JSON çıktısı: geçerli JSON, JSON Schema (zorunlu alanlar, türler). Şema yoksa uygulanmaz."
    actions = ("block", "retry", "fallback", "warn")

    def evaluate(self, payload, ctx, p, state):
        schema = (payload or {}).get("schema")
        if not schema:
            return Finding(False, "şema yok")
        data = (payload or {}).get("data")
        if data is None:
            try:
                data = json.loads(_text(payload))
            except (ValueError, TypeError):
                return Finding(True, "geçersiz JSON", "invalid_json")
        import jsonschema
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            code = "required_field" if e.validator == "required" else "schema_violation"
            return Finding(True, f"şema ihlali: {e.message[:200]}", code,
                           matches=[{"path": "/".join(str(x) for x in e.absolute_path) or "$", "rule": e.validator}])
        return Finding(False)


@register
class UnsupportedClaim(Evaluator):
    type, title, category = "unsupported_claim", "Unsupported claim / hallucination risk", "output"
    stages = ("final_output",)
    description = ("Kesinlik ifadesi (ör. 'kesin', 'the root cause is') + yanıtta hiç evidence referansı yok + "
                   "koşuda evidence mevcut → desteklenmeyen iddia riski.")
    actions = ("warn", "sanitize")

    def evaluate(self, payload, ctx, p, state):
        t = _text(payload)
        ev = (payload or {}).get("evidence_ids")
        if ev is None:
            return Finding(False, "evidence bağlamı yok")
        if detect.CERTAINTY.search(t) and not detect.EVIDENCE_ID.search(t) and ev:
            note = "\n\n> Not: Bu yanıttaki kesin ifadeler bir evidence referansıyla desteklenmiyor."
            return Finding(True, "kesin ifade, evidence referansı yok", "unsupported_claim",
                           transformed=_with_text(payload, t + note), changed=["$.text"], can_transform=True)
        return Finding(False)


# ================================================================================================= evidence
@register
class EvidenceIntegrity(Evaluator):
    type, title, category = "evidence_integrity", "Evidence existence / ID / provenance", "evidence"
    stages = ("final_output",)
    description = ("Yanıtta geçen evidence ID'leri koşunun evidence listesinde olmalı (geçersiz ID işaretlenir); "
                   "atıf yapılan OK durumlu evidence'ın Dynatrace query kimliği (provenance) bulunmalı.")
    actions = ("sanitize", "warn", "block")

    def evaluate(self, payload, ctx, p, state):
        if (payload or {}).get("evidence") is None:
            return Finding(False, "evidence bağlamı yok")
        evs = payload["evidence"]
        known = {e.get("id") for e in evs}
        t = _text(payload)
        cited = set(detect.EVIDENCE_ID.findall(t))
        unknown = sorted(cited - known)
        # provenance: yanıtın dayandığı (atıf yapılan) OK evidence'ın Dynatrace query kimliği olmalı
        no_prov = [e.get("id") for e in evs if e.get("id") in cited and e.get("status") == "OK"
                   and not e.get("dynatrace_query_id")]
        if not unknown and not no_prov:
            return Finding(False)
        new = t
        for u in unknown:
            new = new.replace(u, f"{u} [geçersiz evidence]")
        return Finding(True, f"geçersiz evidence ID: {len(unknown)}, provenance eksik: {len(no_prov)}",
                       "evidence_invalid", matches=[{"unknown_ids": unknown[:20], "no_provenance": no_prov[:20]}],
                       transformed=_with_text(payload, new), changed=["$.text"] if unknown else [],
                       can_transform=bool(unknown))


@register
class EvidenceSufficiency(Evaluator):
    type, title, category = "evidence_sufficiency", "Missing evidence / unsupported root cause", "evidence"
    stages = ("final_output",)
    description = ("Kullanılabilir sinyal yoksa (OK evidence yok ya da coverage threshold'un altında) yanıt kesin "
                   "kök sebep iddia edemez: 'insufficient evidence' notu eklenir.")
    actions = ("sanitize", "warn", "block")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        min_coverage: float = Field(0.3, ge=0, le=1)

    def evaluate(self, payload, ctx, p, state):
        if (payload or {}).get("evidence") is None:
            return Finding(False, "evidence bağlamı yok")
        ok = [e for e in payload["evidence"] if e.get("status") == "OK"]
        cov = (payload.get("risk") or {}).get("coverage")
        weak = not ok or (isinstance(cov, (int, float)) and cov < p.min_coverage)
        t = _text(payload)
        if weak and detect.CERTAINTY.search(t):
            note = ("> **Yetersiz evidence (insufficient evidence):** kullanılabilir sinyal sınırlı; bu analiz kesin bir "
                    "kök sebep belirtmez. Ek veri toplanmalı.\n\n")
            return Finding(True, "yetersiz evidence ile kesin iddia", "insufficient_evidence",
                           thresholds={"min_coverage": {"limit": p.min_coverage, "actual": cov}},
                           matches=[{"ok_evidence": len(ok)}], transformed=_with_text(payload, note + t),
                           changed=["$.text"], can_transform=True)
        return Finding(False)


@register
class EvidenceWindow(Evaluator):
    type, title, category = "evidence_window", "Evidence freshness & time window", "evidence"
    stages = ("final_output",)
    description = "Analiz penceresi geçerli olmalı (başlangıç < bitiş, gelecekte değil, en fazla N gün) ve taze olmalı."
    actions = ("warn", "block")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_window_days: float = Field(31, ge=0.01, le=400)
        max_age_hours: float = Field(24 * 30, ge=0.1, le=24 * 400)

    def evaluate(self, payload, ctx, p, state):
        w = (payload or {}).get("window")
        if not w:
            return Finding(False, "pencere bilgisi yok")
        a, b = _iso(str(w.get("start") or "")), _iso(str(w.get("end") or ""))
        if not a or not b:
            return Finding(True, "pencere ayrıştırılamadı", "window_invalid")
        nowt = datetime.now(timezone.utc)
        probs = {}
        if a >= b:
            probs["order"] = {"limit": "start<end", "actual": "start>=end"}
        if b > nowt + timedelta(minutes=5):
            probs["future"] = {"limit": "end<=now", "actual": b.isoformat()}
        days = (b - a).total_seconds() / 86400
        if days > p.max_window_days:
            probs["max_window_days"] = {"limit": p.max_window_days, "actual": round(days, 2)}
        age = (nowt - b).total_seconds() / 3600
        if age > p.max_age_hours:
            probs["max_age_hours"] = {"limit": p.max_age_hours, "actual": round(age, 1)}
        if probs:
            return Finding(True, "evidence penceresi geçersiz / bayat", "window_invalid", thresholds=probs)
        return Finding(False)


_SCORE_RX = re.compile(r"(?i)\brisk (?:skoru|score|puanı)\D{0,6}(\d{1,3}(?:[.,]\d)?)(?:\s*/\s*100)?")
_BAND_RX = re.compile(r"\brisk(?:\s+seviyesi|\s+bandı|\s+düzeyi|\s+level)?\s+(?:is\s+)?"
                      r"(düşük|orta|yüksek|kritik|low|medium|high|critical)\b")
_BANDS = {"düşük": ("düşük", "low"), "orta": ("orta", "medium"), "yüksek": ("yüksek", "high"),
          "kritik": ("kritik", "critical")}
_BAND_NORM = {w: b for b, ws in _BANDS.items() for w in ws}


@register
class RiskScoreIntegrity(Evaluator):
    type, title, category = "risk_score_integrity", "Deterministic risk score integrity / metric consistency", "evidence"
    stages = ("final_output",)
    description = ("Risk skoru deterministik scoring engine'den gelir; LLM değiştiremez. Yanıtta hesaplanan "
                   "skorlardan (genel + uygulama) hiçbirine uymayan bir skor ya da risk bandı geçerse düzeltme notu "
                   "eklenir (sanitize). Scoring algoritmasına dokunulmaz.")
    actions = ("sanitize", "warn", "block")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tolerance: float = Field(0.5, ge=0, le=20)

    def evaluate(self, payload, ctx, p, state):
        risk = (payload or {}).get("risk")
        if not risk or risk.get("score") is None:
            return Finding(False, "risk skoru yok")
        t = _text(payload)
        score = float(risk["score"])
        allowed = [float(x) for x in (risk.get("allowed_scores") or [score]) if x is not None]
        bands = {_BAND_NORM.get(str(b).lower(), str(b).lower())
                 for b in (risk.get("allowed_bands") or [risk.get("band")]) if b}
        bad = []
        for m in _SCORE_RX.finditer(t[:50000]):
            v = float(m.group(1).replace(",", "."))
            if v <= 100 and all(abs(v - a) > p.tolerance for a in allowed):
                bad.append({"stated": v, "computed": score})
        for m in _BAND_RX.finditer(t[:50000].lower()):
            b = _BAND_NORM.get(m.group(1))
            if bands and b not in bands:
                bad.append({"stated_band": b, "computed_band": risk.get("band")})
        if not bad:
            return Finding(False)
        note = (f"\n\n> Düzeltme: deterministik risk skoru **{risk['score']}** (band: {risk.get('band') or '-'}). "
                "Yanıttaki farklı değer dikkate alınmamalıdır.")
        return Finding(True, "yanıttaki risk değeri hesaplanan değerlerle tutarsız", "risk_mismatch", matches=bad[:10],
                       thresholds={"tolerance": {"limit": p.tolerance, "actual": bad[0].get("stated")}},
                       transformed=_with_text(payload, t + note), changed=["$.text"], can_transform=True)


# ================================================================================================= memory
@register
class MemoryWriteAuthorization(Evaluator):
    type, title, category = "memory_write_auth", "Memory write authorization / procedural protection", "memory"
    stages = ("memory_write",)
    description = ("Memory yazma rolü doğrulanmış koşu bağlamından gelir; procedural memory'yi (runbook / talimat) "
                   "güvenilmeyen kaynak (agent extraction, MCP, log) değiştiremez.")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        roles: list[str] = Field(default_factory=lambda: ["sre", "admin", "system"])
        procedural_sources: list[str] = Field(default_factory=lambda: ["manual", "import"])

    def evaluate(self, payload, ctx, p, state):
        role = ctx.user_role or "anonymous"
        if role not in p.roles:
            return Finding(True, f"rol '{role}' memory yazamaz", "memory_write_denied")
        if (payload or {}).get("memory_type") == "procedural" and (payload or {}).get("source") not in p.procedural_sources:
            return Finding(True, "güvenilmeyen kaynak procedural memory'yi değiştiremez", "procedural_untrusted",
                           matches=[{"source": payload.get("source")}])
        return Finding(False)


@register
class MemoryPoisoning(Evaluator):
    type, title, category = "memory_poisoning", "Memory poisoning / provenance", "memory"
    stages = ("memory_write", "memory_read")
    description = ("Yazma: talimat benzeri içerik reddedilir. Okuma: doğrulanmamış güvenilmeyen kaynaklı kayıt "
                   "agent bağlamına girmez (quarantine). Memory içeriği hiçbir zaman zorunlu policy'leri geçersiz kılamaz.")
    actions = ("block", "quarantine", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        trusted_sources: list[str] = Field(default_factory=lambda: ["manual", "import", "platform"])

    def evaluate(self, payload, ctx, p, state):
        if ctx.stage == "memory_write":
            t = " ".join(str((payload or {}).get(k) or "") for k in ("title", "content"))
            hits = detect.injection(t)
            if hits:
                return Finding(True, "talimat benzeri memory içeriği", "memory_injection",
                               matches=[{"rule": h["rule"]} for h in hits])
            return Finding(False)
        items = (payload or {}).get("items") or []
        bad = [i for i, x in enumerate(items)
               if (x.get("source") not in p.trusted_sources and not x.get("verified"))]
        if not bad:
            return Finding(False)
        keep = [x for i, x in enumerate(items) if i not in bad]
        return Finding(True, f"{len(bad)} doğrulanmamış güvenilmeyen kayıt", "unverified_memory",
                       matches=[{"items": len(bad)}], transformed={**payload, "items": keep},
                       changed=[f"$.items[{i}]" for i in bad], can_transform=True)


# ================================================================================================= execution
@register
class ExecutionLimits(Evaluator):
    type, title, category = "execution_limits", "Execution limits (iterations, tool / LLM calls, duration, retries)", "execution"
    stages = ("node_start", "mcp_request", "tool_request", "llm_request")
    description = ("Koşu sayaçları atomik (lock ile) tutulur; paralel tool çağrılarında sınır yarış nedeniyle aşılamaz. "
                   "Token / LLM çağrı bütçesi ayrıca RunBudget (Agent Middleware call_limit) ile uygulanır.")
    actions = ("terminate", "block", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_node_executions: int = Field(200, ge=1, le=10000)
        max_tool_calls: int = Field(80, ge=1, le=10000)
        max_llm_calls: int = Field(60, ge=1, le=10000)
        max_duration_s: int = Field(1800, ge=5, le=86400)

    def evaluate(self, payload, ctx, p, state):
        if state is None:
            return Finding(False, "koşu bağlamı yok")
        th = {}
        c = state.counters
        # Bu çağrının kendi sıra numarası (reserve anında atomik verilir). Paylaşılan sayacın o anki değeri okunsaydı
        # paralel çağrılar birbirinin artışını görür ve sınırın altındaki çağrı da engellenirdi.
        seq = (payload or {}).get("_seq") if isinstance(payload, dict) else None

        def n(counter: str) -> int:
            return int(seq) if seq is not None else int(c.get(counter, 0))
        if ctx.stage == "node_start" and n("node_executions") > p.max_node_executions:
            th["max_node_executions"] = {"limit": p.max_node_executions, "actual": n("node_executions")}
        if ctx.stage in ("mcp_request", "tool_request") and n("tool_calls") > p.max_tool_calls:
            th["max_tool_calls"] = {"limit": p.max_tool_calls, "actual": n("tool_calls")}
        if ctx.stage == "llm_request" and n("llm_calls") > p.max_llm_calls:
            th["max_llm_calls"] = {"limit": p.max_llm_calls, "actual": n("llm_calls")}
        el = state.elapsed_s()
        if el > p.max_duration_s:
            th["max_duration_s"] = {"limit": p.max_duration_s, "actual": int(el)}
        if th:
            return Finding(True, "koşu sınırı aşıldı: " + ", ".join(th), "execution_limit", thresholds=th)
        return Finding(False)


@register
class LoopDetection(Evaluator):
    type, title, category = "loop_detection", "Agent loop detection", "execution"
    stages = ("node_start", "mcp_request", "tool_request")
    description = "Aynı node'un ya da aynı tool + argüman kombinasyonunun koşu içinde N kereden fazla tekrarı."
    actions = ("terminate", "block", "warn")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_same_node: int = Field(25, ge=2, le=1000)
        max_same_call: int = Field(4, ge=2, le=1000)

    def evaluate(self, payload, ctx, p, state):
        if state is None:
            return Finding(False)
        if ctx.stage == "node_start":
            n = state.repeat.get(f"node:{ctx.node}", 0)
            if n > p.max_same_node:
                return Finding(True, f"node '{ctx.node}' {n} kez çalıştı", "loop_node",
                               thresholds={"max_same_node": {"limit": p.max_same_node, "actual": n}})
            return Finding(False)
        key = (payload or {}).get("call_key")
        n = state.repeat.get(f"call:{key}", 0) if key else 0
        if n > p.max_same_call:
            return Finding(True, f"aynı tool çağrısı {n} kez", "loop_call",
                           thresholds={"max_same_call": {"limit": p.max_same_call, "actual": n}})
        return Finding(False)


@register
class ConcurrencyLimit(Evaluator):
    type, title, category = "concurrency_limit", "Concurrent execution limit", "execution"
    stages = ("before_agent",)
    description = "Agent başına eşzamanlı koşu sınırı (sayı platform veritabanından, koşu başında)."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_concurrent_per_agent: int = Field(4, ge=1, le=100)

    def evaluate(self, payload, ctx, p, state):
        n = int((payload or {}).get("concurrent_runs") or 0)
        if n >= p.max_concurrent_per_agent:
            return Finding(True, f"bu agent için {n} koşu sürüyor", "concurrency",
                           thresholds={"max_concurrent_per_agent": {"limit": p.max_concurrent_per_agent, "actual": n}})
        return Finding(False)


@register
class KillSwitch(Evaluator):
    type, title, category = "kill_switch", "Kill switch", "execution"
    stages = ("before_agent", "node_start", "llm_request", "mcp_request", "tool_request")
    description = ("Guardrails › Settings'teki global ya da agent bazlı kill switch açıkken yeni koşu başlamaz, süren "
                   "koşu bir sonraki node / LLM / MCP adımında sonlandırılır.")
    actions = ("terminate",)

    def evaluate(self, payload, ctx, p, state):
        ks = state.kill_state() if state is not None else {}
        if ks.get("global") or (ctx.agent_id and ctx.agent_id in (ks.get("agents") or [])):
            return Finding(True, "kill switch açık", "kill_switch",
                           matches=[{"scope": "global" if ks.get("global") else "agent"}])
        return Finding(False)


# ================================================================================================= action
@register
class ActionRisk(Evaluator):
    type, title, category = "action_risk", "Action risk classification / human approval", "action"
    stages = ("tool_request", "mcp_request")
    description = ("Yazma / yıkıcı yetenekli eylemler (platform onayında yazma yetenekli işaretli tool ya da eylem "
                   "listesi) insan onayı gerektirir. Bu sürümde agent salt okunurdur; onaylı eylemi yürüten yol yoktur.")
    actions = ("require_approval", "block")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        approval_tools: list[str] = Field(default_factory=list, max_length=200)
        production_environments: list[str] = Field(default_factory=lambda: ["production", "prod"])
        approver_role: str = "admin"
        approval_ttl_minutes: int = Field(60, ge=1, le=10080)

    def evaluate(self, payload, ctx, p, state):
        write = bool((payload or {}).get("write_capable")) or (ctx.tool in p.approval_tools)
        if not write:
            return Finding(False)
        prod = ctx.environment in p.production_environments
        return Finding(True, f"'{ctx.tool}' yazma yetenekli eylem" + (" (production)" if prod else ""),
                       "approval_required", matches=[{"tool": ctx.tool, "production": prod,
                                                      "approver_role": p.approver_role,
                                                      "ttl_minutes": p.approval_ttl_minutes}])
