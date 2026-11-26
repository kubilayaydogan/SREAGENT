"""Agent Middleware: kodda kayıtlı kontrol bileşenleri ve gerçek çağrı yollarına bağlanan yürütme motoru.

Bu agent LangChain `create_agent` KULLANMAZ; iki özel LangGraph StateGraph (analiz, sohbet) vardır. Bu yüzden
LangChain'in yerleşik middleware hook'ları kendiliğinden çalışmaz. Aynı adlı hook'lar burada açık adapter'larla
bağlanır ve arayüzde "LangChain hook değil" diye etiketlenir:

| hook            | bağlandığı gerçek nokta                                                  | tür                    |
|-----------------|--------------------------------------------------------------------------|------------------------|
| before_agent    | platform: InvestigationRunner.run / sohbet turu başlangıcı               | custom run wrapper     |
| after_agent     | aynı yerler, yalnız NORMAL tamamlanmada (hata/kesinti ayrı lifecycle kaydı) | custom run wrapper  |
| before_model    | Llm.interpret / Llm.explain / ClaudePort.route / ClaudePort.astream      | client wrapper         |
| wrap_model_call | aynı çağrılar (iç içe)                                                   | client wrapper         |
| after_model     | aynı çağrılar                                                            | client wrapper         |
| wrap_tool_call  | McpGateway._one_inner (doğrudan MCP istemcisi, tools/call)               | MCP client wrapper     |

Sıralama semantiği (LangChain ile aynı): `before_*` artan `order`; `wrap_*` iç içe — en küçük `order` EN DIŞTA;
`after_*` azalan `order` (ters). Wrapper'lar `pre` (iç katmana inerken) ve `post` (dönerken) adımlarından oluşur;
bu, yalnız önce/sonra çalışan wrapper'lar için iç içe `handler(request)` çağrısıyla aynı yürütme sırasını verir.
Retry yapan bir wrapper YOKTUR (gateway ve Anthropic SDK zaten retry yapar; ikinci katman deneme sayısını katlardı).

Run'a özgü her şey `RUN` contextvar'ındaki `RunMw` nesnesinde ve çağrı başına `Call` nesnesindedir; middleware
instance'larında değişken durum tutulmaz (eşzamanlı run'lar karışmaz). asyncio task'ları ve `asyncio.to_thread`
context'i kopyalar; `asyncio.run` (gateway) çağıran thread'in context'iyle başlar.

Hata politikaları ayrıdır:
* telemetry (kayıt) hatası → çağrı etkilenmez, `RunMw.lost` artar (fail-open);
* güvenlik middleware'i (`security=True`) hata verirse → çağrı ENGELLENİR (fail-closed), sessizce izinli sayılmaz;
* diğer middleware hatası → kayıtta `result=error`, çağrı devam eder.
"""
from __future__ import annotations

import itertools
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import context_window as _cw
from .guardrails import runtime as _gr
from .guardrails.model import GuardrailBlocked

HOOKS = ("before_agent", "before_model", "wrap_model_call", "after_model", "wrap_tool_call", "after_agent")
KINDS = ("logging", "authorization", "validation", "retry", "limit", "redaction", "context", "custom")
DECISIONS = ("allow", "block", "modify", "short_circuit", "not_applicable")

HOOK_INFO: dict[str, dict[str, Any]] = {
    "before_agent": {"supported": True, "attach": "InvestigationRunner.run / ChatOrchestrator turn başlangıcı",
                     "adapter": "custom run wrapper", "langchain_builtin": False, "order": "artan order"},
    "before_model": {"supported": True, "attach": "Llm.interpret, Llm.explain, ClaudePort.route, ClaudePort.astream",
                     "adapter": "client wrapper", "langchain_builtin": False, "order": "artan order"},
    "wrap_model_call": {"supported": True, "attach": "aynı LLM çağrıları; streaming chunk'ları buffer'lanmadan geçer",
                        "adapter": "client wrapper", "langchain_builtin": False,
                        "order": "iç içe: küçük order en dışta"},
    "after_model": {"supported": True, "attach": "aynı LLM çağrıları (yanıt / hata sonrası)",
                    "adapter": "client wrapper", "langchain_builtin": False, "order": "azalan order (ters)"},
    "wrap_tool_call": {"supported": True, "attach": "McpGateway._one_inner (MCP tools/call; paralel çağrılar ayrı)",
                       "adapter": "MCP client wrapper", "langchain_builtin": False,
                       "order": "iç içe: küçük order en dışta"},
    "after_agent": {"supported": True, "attach": "run normal tamamlandığında; hata / iptal ayrı lifecycle kaydıdır",
                    "adapter": "custom run wrapper", "langchain_builtin": False, "order": "azalan order (ters)"},
}
UNSUPPORTED_HOOKS = {
    "wrap_node (graph node)": "Node düzeyi kayıt Agent Trace'te; middleware node'ları sarmaz",
    "LangChain create_agent hooks": "agent create_agent kullanmıyor (özel StateGraph)",
    "response cache": "cache yok; 'cache'den döndü' alanı desteklenmiyor",
    "pending policy decision": "middleware'de bekleyen karar yok; onaylar Approvals sayfasında",
}


class MiddlewareBlocked(RuntimeError):
    def __init__(self, decision: "Decision"):
        super().__init__(f"{decision.code or 'blocked'}: {decision.reason or ''}".strip())
        self.decision = decision


@dataclass
class Decision:
    decision: str = "allow"
    reason: str | None = None
    code: str | None = None
    changed: list[str] = field(default_factory=list)
    policy: str | None = None
    exc: BaseException | None = None           # engellenince çağırana aynen fırlatılacak hata (ör. LlmBudgetExceeded)
    result: Any = None                          # short_circuit sonucu

    @property
    def stops(self) -> bool:
        return self.decision in ("block", "short_circuit")


@dataclass
class Call:
    """Tek bir model / tool çağrısının çağrıya özgü verisi (paylaşılmaz)."""
    kind: str                                  # model | tool | agent
    target: str                                # model adı / tool adı / graph adı
    task: str | None = None                    # interpret_question, explain_findings, route_question, answer, query key
    connector: str | None = None
    tool_call_id: str | None = None
    request: dict = field(default_factory=dict)
    response: dict | None = None
    error: BaseException | None = None
    state: dict = field(default_factory=dict)   # çağıranın sağladığı sayaçlar (ör. queries_used, max_queries)
    attempts: list[dict] = field(default_factory=list)
    stream: dict | None = None
    context_hints: list = field(default_factory=list)   # context window: [(bileşen, istemdeki alt metin)]


class Scope(BaseModel):
    """Boş liste = hepsi. Zorunlu middleware'lerin kapsamı daraltılamaz."""
    model_config = ConfigDict(extra="forbid")
    kinds: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list, max_length=50)
    tasks: list[str] = Field(default_factory=list, max_length=50)
    connectors: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("kinds")
    @classmethod
    def _k(cls, v):
        bad = [x for x in v if x not in ("model", "tool", "agent")]
        if bad:
            raise ValueError(f"geçersiz kind: {bad}")
        return v

    def matches(self, call: Call) -> bool:
        return ((not self.kinds or call.kind in self.kinds) and (not self.targets or call.target in self.targets)
                and (not self.tasks or (call.task or "") in self.tasks)
                and (not self.connectors or (call.connector or "") in self.connectors))


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Middleware:
    name: ClassVar[str]
    title: ClassVar[str]
    kind: ClassVar[str]
    description: ClassVar[str]
    hooks: ClassVar[tuple[str, ...]]
    mandatory: ClassVar[bool] = False
    security: ClassVar[bool] = False            # True → hata = engelle (fail-closed)
    default_order: ClassVar[int] = 100
    Params: ClassVar[type[BaseModel]] = NoParams
    dry_run_support: ClassVar[str] = "supported"   # supported | simulated | unsupported
    policy_ref: ClassVar[str | None] = None        # kuralın asıl tanımlandığı yer (çoğaltılmaz)
    default_scope: ClassVar[dict] = {}

    def pre(self, hook: str, call: Call, p: BaseModel, run: "RunMw") -> Decision | None:
        return None

    def post(self, hook: str, call: Call, p: BaseModel, run: "RunMw") -> Decision | None:
        return None


# ================================================================================================= yerleşik middleware'ler
class CallLogging(Middleware):
    name, title, kind = "call_logging", "Call metadata logging", "logging"
    description = ("Model / tool çağrısının metadata'sını (görev, hedef, istek boyutu, sonuç) execution kaydına "
                   "yazar. İçerik değil metadata; payload yakalama Settings › agent_middleware'dedir.")
    hooks = ("before_agent", "after_agent", "wrap_model_call", "wrap_tool_call")
    default_order = 10
    dry_run_support = "simulated"

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        include_request_size: bool = True

    def pre(self, hook, call, p, run):
        if p.include_request_size:
            import json
            call.state.setdefault("_log", {})["request_bytes"] = len(json.dumps(call.request, default=str).encode())
        return Decision("allow", reason="metadata kaydedildi")


class Timing(Middleware):
    name, title, kind = "timing", "Model / tool duration", "logging"
    description = ("Sardığı çağrının süresini ölçer; streaming'de ilk chunk zamanı ve chunk sayısı (yanıt "
                   "buffer'lanmaz). Süre değerleri her execution kaydında ayrıca overhead / wrapped olarak bulunur.")
    hooks = ("wrap_model_call", "wrap_tool_call")
    default_order = 20
    dry_run_support = "unsupported"

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        slow_call_ms: int = Field(30000, ge=100, le=600000, description="Bu süreyi aşan çağrı 'slow' işaretlenir")

    def post(self, hook, call, p, run):
        d = call.state.get("_call_ms")
        slow = d is not None and d > p.slow_call_ms
        return Decision("allow", reason=f"{d} ms" + (" (slow)" if slow else "") if d is not None else "ölçülmedi",
                        code="slow_call" if slow else None)


class Redaction(Middleware):
    name, title, kind = "redaction", "Sensitive data redaction", "redaction"
    description = ("LLM'e gidecek mesajlarda DLP kalıplarını (secret, PII) tarar; kalan varsa maskeler (modify). "
                   "Kurallar airlab_agent/redact.py'dedir (çoğaltılmaz). Execution payload'ları da kayıttan önce "
                   "aynı kurallarla maskelenir.")
    hooks = ("before_model",)
    mandatory = security = True
    default_order = 30
    policy_ref = "airlab_agent/redact.py (DLP, sink=llm)"

    def pre(self, hook, call, p, run):
        from .redact import redact_text
        msgs = call.request.get("messages")
        if not isinstance(msgs, list):
            return Decision("not_applicable", reason="mesaj yok")
        changed, out = [], []
        for i, m in enumerate(msgs):
            role, text = (m[0], m[1]) if isinstance(m, (list, tuple)) and len(m) == 2 else (None, m)
            if role == "system":                     # sistem prompt'u kodda sabittir; müşteri verisi taşımaz
                out.append(m)
                continue
            if isinstance(text, str):
                red = redact_text(text, "llm")
                if red != text:
                    changed.append(f"messages[{i}]")
                    text = red
            out.append((role, text) if role is not None else text)
        if changed:
            call.request["messages"] = out
            return Decision("modify", reason="hassas veri maskelendi", changed=changed, policy=self.policy_ref)
        return Decision("allow", reason="maskelenecek veri yok", policy=self.policy_ref)


class CallLimit(Middleware):
    name, title, kind = "call_limit", "Model / tool call limit", "limit"
    description = ("Run başına LLM çağrı / token sınırı (RunBudget) ve MCP query sınırı. Değerler Settings › Agent "
                   "(llm_max_calls_per_run, llm_max_tokens_per_run, max_queries_per_run) — burada çoğaltılmaz.")
    hooks = ("before_model", "wrap_tool_call")
    mandatory = security = True
    default_order = 60
    policy_ref = "Settings › Agent (budgets)"

    def pre(self, hook, call, p, run):
        if call.kind == "model":
            from .llm import LlmBudgetExceeded, budget_check
            try:
                budget_check(call.task or "model")
            except LlmBudgetExceeded as e:
                return Decision("block", reason=str(e), code="llm_budget", policy=self.policy_ref, exc=e)
            return Decision("allow", policy=self.policy_ref)
        used, mx = call.state.get("queries_used"), call.state.get("max_queries")
        if used is None or mx is None:
            return Decision("not_applicable", reason="sayaç yok")
        if used >= mx:
            return Decision("block", reason=f"query budget doldu ({used}/{mx})", code="query_budget",
                            policy=self.policy_ref)
        return Decision("allow", reason=f"{used}/{mx}", policy=self.policy_ref)


class ToolAllowlist(Middleware):
    name, title, kind = "tool_allowlist", "Tool allowlist & authorization", "authorization"
    description = ("Yalnız izinli connector + tool çağrılabilir. Tool tanımının bütünlüğü (onaylı hash) ayrıca "
                   "her MCP oturumunda doğrulanır (McpGateway._verify_tool, Settings › Connectors).")
    hooks = ("wrap_tool_call",)
    mandatory = security = True
    default_order = 40
    policy_ref = "Settings › Connectors (approved tool hash)"

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        allowed_connectors: list[str] = Field(default_factory=lambda: ["dynatrace"], max_length=20)
        allowed_tools: list[str] = Field(default_factory=lambda: ["execute-dql"], max_length=50)

    def pre(self, hook, call, p, run):
        if call.connector not in p.allowed_connectors or call.target not in p.allowed_tools:
            return Decision("block", reason=f"{call.connector}/{call.target} izinli değil", code="tool_not_allowed",
                            policy="tool_allowlist")
        return Decision("allow", policy="tool_allowlist")


class ToolParamValidation(Middleware):
    name, title, kind = "tool_param_validation", "Tool parameter validation (DQL)", "validation"
    description = ("tools/call gönderilmeden DQL denetimi: boş değil, uzunluk, kontrol karakteri, yalnız salt okuma "
                   "komutları. Kural mcp_gateway.validate_dql'dir; parametreler yalnız daha KISITLAYICI olabilir.")
    hooks = ("wrap_tool_call",)
    mandatory = security = True
    default_order = 50
    policy_ref = "airlab_agent/mcp_gateway.py validate_dql"

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        max_chars: int = Field(8000, ge=100, le=8000)
        allowed_commands: list[str] = Field(default_factory=lambda: ["fetch", "timeseries"], min_length=1)

        @field_validator("allowed_commands")
        @classmethod
        def _c(cls, v):
            bad = [x for x in v if x not in ("fetch", "timeseries")]
            if bad:
                raise ValueError(f"yalnız fetch / timeseries izinli olabilir: {bad}")
            return v

    def pre(self, hook, call, p, run):
        from .mcp_gateway import validate_dql
        dql = call.request.get("_dql")
        if call.target != "execute-dql":
            return Decision("not_applicable", reason="DQL tool değil")
        bad = validate_dql(dql)
        if not bad and len(dql) > p.max_chars:
            bad = f"query çok uzun ({len(dql)} > {p.max_chars})"
        if not bad and dql.lstrip().split(None, 1)[0] not in p.allowed_commands:
            bad = f"izin verilmeyen başlangıç komutu (izinli: {', '.join(p.allowed_commands)})"
        if bad:
            return Decision("block", reason=bad, code="dql_rejected", policy=self.policy_ref)
        return Decision("allow", policy=self.policy_ref)


class RetryObserver(Middleware):
    name, title, kind = "retry_observer", "Transient-error retry (observed)", "retry"
    description = ("Retry'ı YAPMAZ, gözlemler: MCP gateway'in kendi retry'ı (timeout / erişilemez; yetki hatası "
                   "tekrar denenmez, aynı call_id) ve Anthropic SDK max_retries. İkinci bir retry katmanı deneme "
                   "sayısını katlayacağı için eklenmedi. Dynatrace execute-dql salt okunurdur.")
    hooks = ("wrap_tool_call",)
    default_order = 90
    dry_run_support = "unsupported"
    policy_ref = "Settings › Agent (query_retries) · LLM profile (retries)"

    def post(self, hook, call, p, run):
        n = len(call.attempts)
        if n <= 1:
            return Decision("not_applicable", reason="tek deneme" if n else "deneme bilgisi yok")
        return Decision("allow", reason=f"{n} deneme: " + ", ".join(a.get("outcome", "?") for a in call.attempts),
                        code="retried")


REGISTRY: dict[str, Middleware] = {m.name: m for m in (CallLogging(), Timing(), Redaction(), CallLimit(),
                                                        ToolAllowlist(), ToolParamValidation(), RetryObserver())}


def default_config() -> dict:
    return {n: {"enabled": True, "order": m.default_order, "params": m.Params().model_dump(),
                "scope": dict(m.default_scope), "version": 0} for n, m in REGISTRY.items()}


DEFAULT_SETTINGS = {"record_executions": True, "capture_payloads": False, "max_payload_bytes": 16000}


def describe(name: str) -> dict:
    m = REGISTRY[name]
    return {"name": m.name, "title": m.title, "kind": m.kind, "description": m.description, "hooks": list(m.hooks),
            "mandatory": m.mandatory, "security": m.security, "default_order": m.default_order,
            "dry_run": m.dry_run_support, "policy_ref": m.policy_ref,
            "params_schema": m.Params.model_json_schema(), "scope_schema": Scope.model_json_schema()}


def validate_config(name: str, cfg: dict) -> dict:
    """Backend doğrulaması (arayüz yalnız şemadan form üretir). Geçersiz → ValueError."""
    m = REGISTRY.get(name)
    if m is None:
        raise ValueError("kayıtlı olmayan middleware")
    params = m.Params(**(cfg.get("params") or {})).model_dump()
    scope = Scope(**(cfg.get("scope") or {})).model_dump()
    if m.mandatory:
        if not cfg.get("enabled", True):
            raise ValueError("zorunlu güvenlik middleware'i devre dışı bırakılamaz")
        if any(scope.values()):
            raise ValueError("zorunlu middleware'in kapsamı daraltılamaz")
    order = int(cfg.get("order", m.default_order))
    if not 1 <= order <= 1000:
        raise ValueError("order 1..1000 olmalı")
    return {"enabled": bool(cfg.get("enabled", True)), "order": order, "params": params, "scope": scope}


def check_orders(cfgs: dict[str, dict]) -> None:
    seen: dict[int, str] = {}
    for n, c in cfgs.items():
        o = int(c["order"])
        if o in seen:
            raise ValueError(f"sıralama çakışması: {seen[o]} ve {n} aynı order ({o})")
        seen[o] = n


# ================================================================================================= run bağlamı
@dataclass
class RunMw:
    run_id: str | None
    thread_id: str | None = None
    trace_id: str | None = None
    kind: str | None = None
    user_id: str | None = None
    config: dict = field(default_factory=default_config)
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    sink: Callable[[dict], None] | None = None
    parent_id: str | None = None
    root_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    lost: int = 0
    started: float = field(default_factory=time.monotonic)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _seq: Any = field(default_factory=lambda: itertools.count(1))
    _lock: Any = field(default_factory=threading.Lock)

    def next_seq(self) -> int:
        with self._lock:
            return next(self._seq)

    def emit(self, rec: dict) -> None:
        if self.sink is None or not self.settings.get("record_executions", True):
            return
        try:
            self.sink(rec)
        except Exception:  # noqa: BLE001 — telemetry hatası çağrıyı etkilemez
            with self._lock:
                self.lost += 1


RUN: ContextVar[RunMw | None] = ContextVar("airlab_middleware_run", default=None)
_DEFAULT_RUN_CFG = default_config()
_FALLBACK_CFG: dict = _DEFAULT_RUN_CFG


def set_fallback_config(cfg: dict | None) -> None:
    """Run bağlamı DIŞINDAKİ çağrılar (ör. watch poller) için süreçte en son yüklenen yapılandırma. Kayıt üretilmez.
    Zorunlu middleware'ler her durumda çalışır."""
    global _FALLBACK_CFG
    _FALLBACK_CFG = {n: dict(c) for n, c in (cfg or {}).items()} or _DEFAULT_RUN_CFG


def current() -> RunMw:
    r = RUN.get()
    return r if r is not None else RunMw(run_id=None, config=_FALLBACK_CFG)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active(run: RunMw, hook: str, call: Call) -> list[tuple[Middleware, dict, BaseModel]]:
    out = []
    for n, m in REGISTRY.items():
        if hook not in m.hooks:
            continue
        c = run.config.get(n) or _DEFAULT_RUN_CFG[n]
        if not (c.get("enabled", True) or m.mandatory):          # zorunlu olan her koşulda çalışır
            continue
        if not m.mandatory and not Scope(**(c.get("scope") or {})).matches(call):
            continue
        try:
            p = m.Params(**(c.get("params") or {}))
        except Exception:  # noqa: BLE001 — bozuk snapshot: varsayılan (güvenli) parametre
            p = m.Params()
        out.append((m, c, p))
    out.sort(key=lambda x: (int(x[1].get("order", x[0].default_order)), x[0].name))
    return out


def _payload(run: RunMw, obj: Any) -> tuple[Any, bool]:
    """Kayıttan ÖNCE maskeleme + boyut sınırı. Yakalama kapalıysa None."""
    if not run.settings.get("capture_payloads"):
        return None, False
    from .agent_trace import safe_copy
    from .redact import redact
    data, trunc, size = safe_copy(redact(obj, "trace"))
    if size > int(run.settings.get("max_payload_bytes", 16000)):
        import json
        raw = json.dumps(data, ensure_ascii=False, default=str)
        return {"_truncated": True, "_original_bytes": size, "preview": raw[:2000]}, True
    return data, trunc


def _req_view(call: Call) -> dict:
    return {k: v for k, v in call.request.items() if not k.startswith("_")}


def _record(run: RunMw, *, rid: str, seq: int, parent: str | None, depth: int, entry: str, call: Call | None,
            hook: str | None, m: Middleware | None, conf: dict | None, t0: datetime, t1: datetime,
            overhead_ms: float | None, wrapped_ms: float | None, d: Decision | None, error: BaseException | None,
            outcome: str | None, before: Any = None, after: Any = None, truncated: bool = False,
            extra: dict | None = None) -> None:
    run.emit({
        "execution_id": rid, "run_id": run.run_id, "run_kind": run.kind, "user_id": run.user_id,
        "thread_id": run.thread_id, "trace_id": run.trace_id,
        "parent_execution_id": parent, "sequence": seq, "depth": depth, "entry_type": entry,
        "middleware": m.name if m else None, "middleware_version": (conf or {}).get("version") if m else None,
        "hook": hook, "target_kind": call.kind if call else None, "target": call.target if call else None,
        "task": call.task if call else None, "connector": call.connector if call else None,
        "tool_call_id": call.tool_call_id if call else None,
        "attempt_no": len(call.attempts) if call and call.attempts else None,
        "started_at": t0.isoformat(), "ended_at": t1.isoformat(),
        "overhead_ms": round(overhead_ms, 3) if overhead_ms is not None else None,
        "wrapped_ms": round(wrapped_ms, 3) if wrapped_ms is not None else None,
        "result": "error" if error is not None else "success",
        "decision": d.decision if d else None, "reason": (d.reason or "")[:300] if d and d.reason else None,
        "error_code": (d.code if d and d.code else (type(error).__name__ if error is not None else None)),
        "policy": d.policy if d else None, "changed_fields": list(d.changed) if d else [],
        "outcome": outcome, "before": before, "after": after, "truncated": truncated,
        "extra": extra or {}})


def _run_step(m: Middleware, fn, hook: str, call: Call, p, run: RunMw) -> tuple[Decision, BaseException | None, float]:
    t = time.perf_counter()
    try:
        d = fn(hook, call, p, run) or Decision("not_applicable")
        err = None
    except Exception as e:  # noqa: BLE001
        err = e
        d = (Decision("block", reason="güvenlik middleware'i hata verdi; çağrı engellendi (fail-closed)",
                      code="middleware_error") if m.security
             else Decision("not_applicable", reason="middleware hatası; çağrı devam etti", code="middleware_error"))
    return d, err, (time.perf_counter() - t) * 1000


def _merge(pre: Decision, post: Decision | None) -> Decision:
    """Bir wrapper'ın pre + post kararından tek kayıt kararı: block/modify/short_circuit önceliklidir; sonra post'un
    anlamlı kararı (ör. Timing: allow + süre); ikisi de not_applicable ise not_applicable."""
    if pre.decision in ("block", "modify", "short_circuit") or post is None:
        return pre
    if post.decision != "not_applicable":
        return post if pre.decision != "allow" or post.decision != "allow" or post.code or post.reason else pre
    return pre


class _CallExec:
    """Bir model/tool çağrısının middleware zinciri: before → wrap pre (dıştan içe) → çağrı → wrap post (içten dışa)
    → after. Her adım execution kaydıdır; parent ilişkisi gerçek iç içeliği taşır."""

    def __init__(self, call: Call, before: str | None, wrap: str, after: str | None):
        self.run = current()
        self.call = call
        self.hooks = (before, wrap, after)
        self.env_id, self.env_seq = str(uuid.uuid4()), self.run.next_seq()
        self.t0, self.p0 = _now(), time.perf_counter()
        self.stack: list[tuple] = []
        self.blocked: Decision | None = None
        self.call_ms: float | None = None
        self.mw_ms = 0.0

    def _rec(self, **kw):
        _record(self.run, **kw)

    def enter(self) -> Decision | None:
        run, call = self.run, self.call
        before, wrap, _ = self.hooks
        parent = self.env_id
        if before:
            for m, c, p in _active(run, before, call):
                rid, seq, t0 = str(uuid.uuid4()), run.next_seq(), _now()
                b, _ = _payload(run, _req_view(call)) if m.kind in ("redaction", "context") else (None, False)
                d, err, ms = _run_step(m, m.pre, before, call, p, run)
                self.mw_ms += ms
                _observe(m, d, call, ms, self.env_id)
                a, tr = _payload(run, _req_view(call)) if d.decision == "modify" else (None, False)
                self._rec(rid=rid, seq=seq, parent=self.env_id, depth=1, entry="middleware", call=call, hook=before,
                          m=m, conf=c, t0=t0, t1=_now(), overhead_ms=ms, wrapped_ms=None, d=d, error=err,
                          outcome="blocked" if d.stops else "continued", before=b, after=a, truncated=tr)
                if d.stops:
                    self.blocked = d
                    return d
        depth = 1
        for m, c, p in _active(run, wrap, call):
            rid, seq, t0 = str(uuid.uuid4()), run.next_seq(), _now()
            b, _ = _payload(run, _req_view(call)) if run.settings.get("capture_payloads") else (None, False)
            d, err, ms = _run_step(m, m.pre, wrap, call, p, run)
            self.mw_ms += ms
            _observe(m, d, call, ms, self.env_id)
            self.stack.append((m, c, p, rid, seq, t0, parent, depth, d, err, ms, time.perf_counter(), b))
            parent, depth = rid, depth + 1
            if d.stops:
                self.blocked = d
                return d
        self.inner_parent, self.inner_depth = parent, depth
        if call.kind == "model":
            # Guardrails before_model: zorunlu middleware'lerden SONRA (maskelenmiş mesajlar üzerinde), sağlayıcıdan önce
            try:
                _gr.llm_request(call, envelope_id=self.env_id)
            except GuardrailBlocked as e:
                self.blocked = Decision("block", reason=str(e)[:300], code=e.code, policy="guardrails", exc=e)
                return self.blocked
        return None

    def exit(self, error: BaseException | None) -> None:
        run, call = self.run, self.call
        _, wrap, after = self.hooks
        call.error = error
        call.state["_call_ms"] = round(self.call_ms, 1) if self.call_ms is not None else None
        for i, (m, c, p, rid, seq, t0, parent, depth, d, err, pre_ms, t_in, b) in enumerate(reversed(self.stack)):
            blocking_layer = i == 0 and self.blocked is not None and d is self.blocked
            if blocking_layer:
                post_ms, d2, err2 = 0.0, None, None
                wrapped = None
            else:
                tp = time.perf_counter()
                wrapped = (tp - t_in) * 1000
                d2, err2, post_ms = _run_step(m, m.post, wrap, call, p, run)
                self.mw_ms += post_ms
            final = _merge(d, d2)
            outcome = "blocked" if blocking_layer and d.decision == "block" else \
                "short_circuit" if blocking_layer else ("error" if error is not None else
                                                        "blocked_inner" if self.blocked else "continued")
            a, tr = _payload(run, {"request": _req_view(call), "response": call.response}) \
                if run.settings.get("capture_payloads") else (None, False)
            self._rec(rid=rid, seq=seq, parent=parent, depth=depth, entry="middleware", call=call, hook=wrap, m=m,
                      conf=c, t0=t0, t1=_now(), overhead_ms=pre_ms + post_ms, wrapped_ms=wrapped, d=final,
                      error=err or err2, outcome=outcome, before=b, after=a, truncated=tr,
                      extra={"pre_ms": round(pre_ms, 3), "post_ms": round(post_ms, 3)})
        if after and self.blocked is None:
            for m, c, p in reversed(_active(run, after, call)):
                rid, seq, t0 = str(uuid.uuid4()), run.next_seq(), _now()
                d, err, ms = _run_step(m, m.post, after, call, p, run)
                self.mw_ms += ms
                self._rec(rid=rid, seq=seq, parent=self.env_id, depth=1, entry="middleware", call=call, hook=after,
                          m=m, conf=c, t0=t0, t1=_now(), overhead_ms=ms, wrapped_ms=None, d=d, error=err,
                          outcome="continued")
        total = (time.perf_counter() - self.p0) * 1000
        env_d = self.blocked or Decision("allow")
        self._rec(rid=self.env_id, seq=self.env_seq, parent=run.root_id if run.run_id else None, depth=0,
                  entry="call", call=call, hook=None, m=None, conf=None, t0=self.t0, t1=_now(),
                  overhead_ms=self.mw_ms, wrapped_ms=self.call_ms, d=env_d, error=error,
                  outcome="blocked" if self.blocked and self.blocked.decision == "block" else
                  "short_circuit" if self.blocked else ("error" if error is not None else "completed"),
                  extra={"total_ms": round(total, 3), "stream": call.stream, "attempts": call.attempts or None,
                         "cache": "not_supported"})

    def raise_blocked(self):
        d = self.blocked
        if d.exc is not None:
            raise d.exc
        raise MiddlewareBlocked(d)


# Güvenlik middleware'i → guardrail policy (kural middleware'de uygulanır; burada yalnız kaydedilir)
_MW_DECISION = {"allow": "passed", "modify": "redacted", "block": "blocked", "short_circuit": "blocked"}


def _observe(m: Middleware, d: Decision, call: Call, ms: float, envelope_id: str) -> None:
    if not m.security or d.decision not in _MW_DECISION:
        return
    key = None
    if m.name == "call_limit":
        key = "llm_call_budget" if call.kind == "model" else "tool_call_budget"
    try:
        _gr.observe(f"middleware:{m.name}", policy_key=key, decision=_MW_DECISION[d.decision],
                    stage="llm_request" if call.kind == "model" else "mcp_request", reason=d.reason, code=d.code,
                    duration_ms=ms, model=call.target if call.kind == "model" else None,
                    tool=call.target if call.kind == "tool" else None, mcp_server=call.connector,
                    llm_call_id=envelope_id if call.kind == "model" else None,
                    mcp_call_id=call.tool_call_id if call.kind == "tool" else None)
    except Exception:  # noqa: BLE001 — kayıt hatası çağrıyı etkilemez
        pass


def _guard_output(call: Call, out: Any) -> Any:
    """after_model: model yanıtındaki metin guardrail'den geçer (sızıntı, hassas veri). Yapılandırılmış çıktı
    (structured) yalnız değerlendirilir; dönüşüm uygulanmaz (iç yönlendirme verisidir, kullanıcıya gitmez)."""
    content = getattr(out, "content", None)
    if isinstance(content, str):
        new = _gr.llm_response_text(call, content)
        if new != content:
            try:
                out.content = new
            except Exception:  # noqa: BLE001
                pass
        return out
    if isinstance(content, list):
        parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)]
        if parts:
            joined = "".join(p["text"] for p in parts)
            new = _gr.llm_response_text(call, joined)
            if new != joined:
                parts[0]["text"] = new
                for p in parts[1:]:
                    p["text"] = ""
        return out
    if isinstance(out, dict) and "raw" in out:
        raw = out.get("raw")
        text = getattr(raw, "content", None)
        if isinstance(text, (str, list)):
            import json as _json
            _gr.check("llm_response", {"text": text if isinstance(text, str) else _json.dumps(text, default=str)[:20000],
                                       "structured": True}, enforce=True, model=call.target)
    return out


# ------------------------------------------------------------------------------------------------ çağrı API'leri
def run_model_sync(call: Call, fn: Callable[[dict], Any]) -> Any:
    ex = _CallExec(call, "before_model", "wrap_model_call", "after_model")
    d = ex.enter()
    if d is not None:
        ex.exit(None)
        if d.decision == "short_circuit":
            return d.result
        ex.raise_blocked()
    t = time.perf_counter()
    tok = _cw.begin(call, envelope_id=ex.env_id)        # context window: yalnız sağlayıcıya giden çağrı
    try:
        out = fn(call.request)
    except BaseException as e:
        ex.call_ms = (time.perf_counter() - t) * 1000
        _cw.end(tok, error=e)
        ex.exit(e)
        raise
    ex.call_ms = (time.perf_counter() - t) * 1000
    _cw.end(tok, result=out)
    try:
        out = _guard_output(call, out)
    except BaseException as e:
        ex.exit(e)
        raise
    ex.exit(None)
    return out


async def run_model_async(call: Call, fn: Callable[[dict], Awaitable[Any]]) -> Any:
    ex = _CallExec(call, "before_model", "wrap_model_call", "after_model")
    d = ex.enter()
    if d is not None:
        ex.exit(None)
        if d.decision == "short_circuit":
            return d.result
        ex.raise_blocked()
    t = time.perf_counter()
    tok = _cw.begin(call, envelope_id=ex.env_id)
    try:
        out = await fn(call.request)
    except BaseException as e:
        ex.call_ms = (time.perf_counter() - t) * 1000
        _cw.end(tok, error=e)
        ex.exit(e)
        raise
    ex.call_ms = (time.perf_counter() - t) * 1000
    _cw.end(tok, result=out)
    try:
        out = _guard_output(call, out)
    except BaseException as e:
        ex.exit(e)
        raise
    ex.exit(None)
    return out


async def stream_model(call: Call, fn: Callable[[dict], AsyncIterator[Any]]) -> AsyncIterator[Any]:
    """Chunk'lar aynen ve anında iletilir; yalnız sayılır ve ilk chunk zamanı ölçülür (buffer yok)."""
    ex = _CallExec(call, "before_model", "wrap_model_call", "after_model")
    d = ex.enter()
    if d is not None:
        ex.exit(None)
        ex.raise_blocked()
    t = time.perf_counter()
    call.stream = {"chunks": 0, "first_chunk_ms": None, "completed": False}
    err: BaseException | None = None
    tok = _cw.begin(call, envelope_id=ex.env_id, streaming=True)
    try:
        async for chunk in fn(call.request):
            if call.stream["first_chunk_ms"] is None:
                call.stream["first_chunk_ms"] = round((time.perf_counter() - t) * 1000, 1)
            call.stream["chunks"] += 1
            _cw.on_chunk(chunk)                        # final usage (içerik tutulmaz)
            yield chunk
        call.stream["completed"] = True
    except GeneratorExit:
        call.stream["interrupted"] = True           # tüketici erken bıraktı (iptal)
        raise
    except BaseException as e:
        err = e
        raise
    finally:
        ex.call_ms = (time.perf_counter() - t) * 1000
        _cw.end(tok, error=err, stream=dict(call.stream))
        ex.exit(err)


async def run_tool_async(call: Call, fn: Callable[[dict], Awaitable[Any]],
                         on_blocked: Callable[[Decision], Any]) -> Any:
    """Engellenen tool ÇAĞRILMAZ; `on_blocked(decision)` çağıranın eski davranışıyla aynı sonucu üretir."""
    ex = _CallExec(call, None, "wrap_tool_call", None)
    d = ex.enter()
    if d is not None:
        ex.exit(None)
        return d.result if d.decision == "short_circuit" else on_blocked(d)
    t = time.perf_counter()
    try:
        out = await fn(call.request)
    except BaseException as e:
        ex.call_ms = (time.perf_counter() - t) * 1000
        ex.exit(e)
        raise
    ex.call_ms = (time.perf_counter() - t) * 1000
    ex.exit(None)
    return out


# ------------------------------------------------------------------------------------------------ agent lifecycle
def agent_start(run: RunMw, *, graph: str) -> None:
    """before_agent hook'ları (custom run wrapper). Kayıtlar run kökünün çocuklarıdır."""
    call = Call(kind="agent", target=graph)
    for m, c, p in _active(run, "before_agent", call):
        rid, seq, t0 = str(uuid.uuid4()), run.next_seq(), _now()
        d, err, ms = _run_step(m, m.pre, "before_agent", call, p, run)
        _record(run, rid=rid, seq=seq, parent=run.root_id, depth=1, entry="middleware", call=call,
                hook="before_agent", m=m, conf=c, t0=t0, t1=_now(), overhead_ms=ms, wrapped_ms=None, d=d, error=err,
                outcome="continued")


def agent_end(run: RunMw, *, graph: str, status: str, error: str | None = None) -> None:
    """Normal tamamlanmada after_agent hook'ları; her durumda run kökü (lifecycle) kaydı. after_agent bir finally
    mechanism'i DEĞİLDİR: hata / iptal durumunda yalnız kök kayıt `status` ile yazılır."""
    call = Call(kind="agent", target=graph)
    if status == "completed":
        for m, c, p in reversed(_active(run, "after_agent", call)):
            rid, seq, t0 = str(uuid.uuid4()), run.next_seq(), _now()
            d, err, ms = _run_step(m, m.post, "after_agent", call, p, run)
            _record(run, rid=rid, seq=seq, parent=run.root_id, depth=1, entry="middleware", call=call,
                    hook="after_agent", m=m, conf=c, t0=t0, t1=_now(), overhead_ms=ms, wrapped_ms=None, d=d,
                    error=err, outcome="continued")
    _record(run, rid=run.root_id, seq=0, parent=run.parent_id, depth=0, entry="agent", call=call, hook=None, m=None,
            conf=None, t0=run.started_at, t1=_now(), overhead_ms=None,
            wrapped_ms=(time.monotonic() - run.started) * 1000, d=None,
            error=RuntimeError(error) if error else None, outcome=status,
            extra={"lost_records": run.lost, "config_versions": {n: c.get("version") for n, c in run.config.items()},
                   "kind": run.kind})


def dry_run(name: str, hook: str, call_input: dict, params: dict | None, state: dict | None) -> dict:
    """Sentetik girdiyle karar üretir; gerçek LLM / MCP çağrısı veya yan etki yoktur."""
    m = REGISTRY[name]
    if hook not in m.hooks:
        return {"supported": False, "reason": f"{name} '{hook}' hook'unda çalışmaz", "hooks": list(m.hooks)}
    try:
        p = m.Params(**(params or {}))
    except Exception as e:  # noqa: BLE001
        return {"supported": True, "param_errors": [str(e)[:500]]}
    if m.dry_run_support == "unsupported":
        return {"supported": False, "reason": "sardığı gerçek çağrının sonucuna dayanır; dry-run desteklenmiyor"}
    call = Call(kind=call_input.get("kind") or ("tool" if hook == "wrap_tool_call" else "model"),
                target=call_input.get("target") or "", task=call_input.get("task"),
                connector=call_input.get("connector"), request=dict(call_input.get("request") or {}),
                state=dict(state or {}))
    if "dql" in call_input:
        call.request["_dql"] = call_input["dql"]
    before = _req_view(call)
    fake = RunMw(run_id=None)
    from . import llm as _llm
    token = _llm.RUN_BUDGET.set(None)                # dry-run gerçek run bütçesine dokunmaz
    try:
        d, err, ms = _run_step(m, m.pre if not hook.startswith("after") else m.post, hook, call, p, fake)
    finally:
        _llm.RUN_BUDGET.reset(token)
    return {"supported": True, "mode": m.dry_run_support, "hook": hook, "decision": d.decision, "reason": d.reason,
            "code": d.code, "changed_fields": d.changed, "policy": d.policy,
            "error": type(err).__name__ if err else None, "before": before, "after": _req_view(call),
            "duration_ms": round(ms, 3), "note": "gerçek LLM / MCP çağrısı yapılmadı"}
