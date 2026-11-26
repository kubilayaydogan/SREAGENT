"""Guardrail domain modeli: aşamalar, kararlar, aksiyonlar, policy ve değerlendirme bağlamı.

LLM sağlayıcısından ve LangGraph'tan bağımsızdır; yalnız standart kütüphane + pydantic kullanır.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Execution stage'leri. Aynı entegrasyon noktasını paylaşan aşamalar tek değerlendirmede birleşir (duplicate yok):
#   before_agent = before_graph (koşu başlangıcı), tool_request + mcp_request (MCP tools/call),
#   tool_response + mcp_response (MCP yanıtı).
STAGES = ("before_agent", "node_start", "node_end", "llm_request", "llm_response", "tool_request", "mcp_request",
          "tool_response", "mcp_response", "memory_read", "memory_write", "checkpoint", "final_output", "after_agent")
STAGE_TR = {
    "before_agent": "Before agent / graph execution", "node_start": "Before node execution",
    "node_end": "After node execution", "llm_request": "Before LLM invocation", "llm_response": "After LLM invocation",
    "tool_request": "Before tool execution", "mcp_request": "Before MCP request",
    "tool_response": "After tool execution", "mcp_response": "After MCP response",
    "memory_read": "Before memory read (results)", "memory_write": "Before memory write",
    "checkpoint": "Before checkpoint persistence", "final_output": "Before final output",
    "after_agent": "After agent execution",
}
CATEGORIES = ("input", "model_context", "tool", "mcp", "data_protection", "output", "evidence", "memory",
              "execution", "action")
DECISIONS = ("passed", "blocked", "warned", "redacted", "sanitized", "approval_required", "terminated", "error")
ACTIONS = ("allow", "block", "warn", "redact", "sanitize", "retry", "fallback", "require_approval", "quarantine",
           "terminate")
MODES = ("monitor", "enforce", "disabled")
SEVERITIES = ("info", "low", "medium", "high", "critical")
SCOPES = ("global", "tenant", "environment", "agent", "mcp_server", "tool", "graph_node")
# Daha özel kapsam önce sıralanmaz: deny-overrides'ta sıra sonucu değiştirmez; yalnız dönüşüm (redact/sanitize)
# zincirinin deterministik olması için (priority, scope rank, key) kullanılır.
SCOPE_RANK = {s: i for i, s in enumerate(SCOPES)}

# Aksiyonun ürettiği karar. retry: çağıran retry'ı destekliyorsa kullanır, aksi halde engel (fail-safe).
ACTION_DECISION = {"allow": "passed", "block": "blocked", "warn": "warned", "redact": "redacted",
                   "sanitize": "sanitized", "retry": "blocked", "fallback": "sanitized",
                   "require_approval": "approval_required", "quarantine": "sanitized", "terminate": "terminated"}
# Deny-overrides: en güçlü karar kazanır
STRENGTH = {"passed": 0, "warned": 1, "redacted": 2, "sanitized": 3, "error": 4, "blocked": 5,
            "approval_required": 6, "terminated": 7}
STOPS = {"blocked", "approval_required", "terminated"}
# Fail-closed zorunlu kategoriler (ayardan değiştirilemez)
FAIL_CLOSED_FIXED = {"tool", "mcp", "data_protection", "memory", "execution", "action"}


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Assignment:
    scope: str
    value: str | None = None           # global için None

    def matches(self, ctx: "EvalContext") -> bool:
        if self.scope == "global":
            return True
        v = {"tenant": ctx.tenant_id, "environment": ctx.environment, "agent": ctx.agent_id,
             "mcp_server": ctx.mcp_server, "tool": ctx.tool, "graph_node": ctx.node}.get(self.scope)
        return v is not None and str(v) == str(self.value)


@dataclass(frozen=True)
class Policy:
    id: str
    key: str
    name: str
    category: str
    type: str                           # evaluator türü
    severity: str = "medium"
    mode: str = "enforce"
    mandatory: bool = False
    stages: tuple[str, ...] = ()
    action: str = "block"
    priority: int = 100
    version: int = 1
    params: dict = field(default_factory=dict)
    assignments: tuple[Assignment, ...] = (Assignment("global"),)
    status: str = "active"
    bound_to: str | None = None         # uygulaması başka katmanda (ör. middleware:redaction) → yalnız kayıt

    @property
    def scope_level(self) -> str:
        return min((a.scope for a in self.assignments), key=lambda s: SCOPE_RANK.get(s, 99), default="global")

    def applies(self, ctx: "EvalContext") -> bool:
        return any(a.matches(ctx) for a in self.assignments)

    def effective_mode(self) -> str:
        """Mandatory policy her koşulda enforce çalışır (snapshot bozuk olsa bile)."""
        return "enforce" if self.mandatory else (self.mode if self.mode in MODES else "enforce")

    def version_tag(self) -> str:
        return f"{self.key}@v{self.version}"


@dataclass
class EvalContext:
    """Değerlendirme bağlamı. Kimlik / rol / tenant / yetki alanları YALNIZ backend'in doğruladığı koşu bağlamından
    gelir; LLM çıktısından ya da istemciden alınmaz."""
    run_id: str | None = None
    thread_id: str | None = None
    agent_id: str | None = None
    agent_key: str | None = None
    agent_version: str | None = None
    run_kind: str | None = None         # investigation | chat_turn | custom_agent | test
    user_id: str | None = None
    user_role: str | None = None
    tenant_id: str = "default"
    environment: str = "default"
    node: str | None = None
    stage: str | None = None
    mcp_server: str | None = None
    tool: str | None = None
    tool_args: dict | None = None       # güvenli önizleme
    provider: str | None = None
    model: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    parent_span_id: str | None = None   # middleware çağrı zarfı / mcp_call_id
    mcp_call_id: str | None = None
    llm_call_id: str | None = None
    dry_run: bool = False

    def view(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, {}, [])}


@dataclass
class Finding:
    """Evaluator sonucu (policy aksiyonundan bağımsız). matched=False → policy geçti."""
    matched: bool
    reason: str = ""
    code: str | None = None
    matches: list[dict] = field(default_factory=list)       # eşleşen koşullar (değerler maskeli)
    thresholds: dict = field(default_factory=dict)          # tetiklenen eşikler {ad: {limit, actual}}
    transformed: Any = None                                 # redact / sanitize / quarantine sonrası payload
    changed: list[str] = field(default_factory=list)        # değişen alan yolları
    fallback: Any = None
    can_transform: bool = False


@dataclass
class Evaluation:
    id: str
    policy: Policy
    stage: str
    decision: str
    enforced: bool
    finding: Finding
    duration_ms: float
    error: str | None = None
    approval_id: str | None = None
    ts: datetime = field(default_factory=now)


@dataclass
class Outcome:
    """Bir aşamanın birleşik sonucu (deny-overrides)."""
    stage: str
    decision: str
    payload: Any
    evaluations: list[Evaluation]
    message: str | None = None
    approval_id: str | None = None

    @property
    def stopped(self) -> bool:
        return self.decision in STOPS

    @property
    def changed(self) -> bool:
        return any(e.enforced and e.decision in ("redacted", "sanitized") for e in self.evaluations)


class GuardrailBlocked(RuntimeError):
    """Enforce edilen bir policy yürütmeyi durdurdu (blocked / approval_required)."""

    def __init__(self, outcome: Outcome):
        super().__init__(outcome.message or "guardrail engelledi")
        self.outcome = outcome
        self.code = "guardrail_" + outcome.decision


class GuardrailTerminated(GuardrailBlocked):
    """Koşu sonlandırıldı (terminate aksiyonu, kill switch)."""


def new_id() -> str:
    return str(uuid.uuid4())


def action_hash(*, tenant_id: str, agent_id: str | None, action: str, resource: str, params: Any) -> str:
    """Onayın bağlandığı değişmez özet (tenant + agent + eylem + kaynak + kanonik parametreler)."""
    raw = json.dumps({"t": tenant_id, "a": agent_id, "x": action, "r": resource, "p": params}, sort_keys=True,
                     default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()
