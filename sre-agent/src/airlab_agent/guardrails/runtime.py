"""Koşu bağlamı ve entegrasyon noktaları (hook yardımcıları).

`RUN` contextvar'ı koşunun engine snapshot'ını, doğrulanmış bağlamını (kullanıcı, rol, tenant, agent) ve sayaçlarını
taşır. asyncio görevleri ve `asyncio.to_thread` context'i kopyalar; MCP gateway'in `asyncio.run`'ı çağıran thread'in
context'iyle başlar (middleware ile aynı model). Koşu bağlamı yoksa süreç genelindeki son snapshot (yoksa yerleşik
katalog) kullanılır ve kayıt üretilmez; mandatory policy'ler yine uygulanır.

Kayıt: geçen (passed) değerlendirmeler koşu başına (policy, stage) toplanır (sayı, süre, histogram) ve koşu sonunda tek
satır yazılır; geçmeyenler tek tek, güvenli snapshot'larla yazılır. Sink hatası koşuyu etkilemez (sayılır).
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import threading
import time
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from .catalog import BOUND, builtin_engine_policies
from .engine import Engine, safe_snapshot
from .model import (EvalContext, Evaluation, GuardrailBlocked, GuardrailTerminated, Outcome, action_hash, new_id,
                    now)

BUCKETS = (0.1, 0.5, 1, 5, 10, 50, 100)            # ms; son kova: ≥100


def bucket(ms: float) -> int:
    for i, b in enumerate(BUCKETS):
        if ms < b:
            return i
    return len(BUCKETS)


_RATE: dict[str, deque] = {}
_RATE_LOCK = threading.Lock()


@dataclass
class GuardRun:
    engine: Engine
    ctx: EvalContext
    sink: Callable[[dict], None] | None = None
    kill: Callable[[], dict] | None = None
    counters: dict = field(default_factory=dict)
    repeat: dict = field(default_factory=dict)
    passes: dict = field(default_factory=dict)
    lost: int = 0
    started: float = field(default_factory=time.monotonic)
    started_at: Any = field(default_factory=now)
    _lock: Any = field(default_factory=threading.Lock)

    # ---- sayaçlar (atomik: paralel tool çağrılarında sınır aşılamaz)
    def reserve(self, counter: str, repeat_key: str | None = None) -> int:
        with self._lock:
            n = self.counters.get(counter, 0) + 1
            self.counters[counter] = n
            if repeat_key:
                self.repeat[repeat_key] = self.repeat.get(repeat_key, 0) + 1
            return n

    def rate_hit(self, server: str) -> int:
        t = time.monotonic()
        with _RATE_LOCK:
            q = _RATE.setdefault(f"{self.ctx.tenant_id}:{server}", deque())
            q.append(t)
            while q and q[0] < t - 60:
                q.popleft()
            return len(q)

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def kill_state(self) -> dict:
        if self.kill is not None:
            try:
                return self.kill() or {}
            except Exception:  # noqa: BLE001 — okunamazsa snapshot'taki değer
                pass
        return (self.engine.config.get("kill_switch") or {})

    # ---- kayıt
    def _emit(self, rec: dict) -> None:
        if self.sink is None:
            return
        try:
            self.sink(rec)
        except Exception:  # noqa: BLE001 — telemetry kaybı koşuyu etkilemez
            with self._lock:
                self.lost += 1

    def record(self, ev: Evaluation, ctx: EvalContext, before: Any, after: Any) -> None:
        if self.sink is None:
            return
        if ev.decision == "passed" and ev.error is None:
            if not self.engine.config.get("record_passed", True):
                return
            k = (ev.policy.key, ev.stage)
            with self._lock:
                a = self.passes.get(k)
                if a is None:
                    a = self.passes[k] = {"policy": ev.policy, "stage": ev.stage, "count": 0, "total_ms": 0.0,
                                          "max_ms": 0.0, "buckets": [0] * (len(BUCKETS) + 1), "first": ev.ts,
                                          "node": ctx.node}
                a["count"] += 1
                a["total_ms"] += ev.duration_ms
                a["max_ms"] = max(a["max_ms"], ev.duration_ms)
                a["buckets"][bucket(ev.duration_ms)] += 1
            return
        cap = (self.engine.config.get("masking") or {})
        snap = {}
        if cap.get("capture_snapshots", True) and not ctx.dry_run:
            lim = int(cap.get("max_snapshot_bytes", 16000))
            snap = {"before": safe_snapshot(before, original=True, limit=lim)}
            if ev.enforced and ev.finding.changed:
                snap["after"] = safe_snapshot(after, original=False, limit=lim)
        self._emit(event(ev, ctx, snapshot=snap or None))
        if ev.approval_id:
            self._emit(approval_event(ev, ctx, self.engine.config))

    def flush_passes(self) -> None:
        with self._lock:
            items, self.passes = list(self.passes.values()), {}
        for a in items:
            p = a["policy"]
            self._emit({"_type": "evaluation", "id": new_id(), "ts": a["first"].isoformat(),
                        **_ctx_cols(self.ctx), "node": a["node"], "stage": a["stage"], "policy_key": p.key,
                        "policy_id": None if p.id.startswith("builtin:") else p.id, "policy_version": p.version,
                        "category": p.category, "decision": "passed", "enforced": True, "severity": p.severity,
                        "action": "allow", "reason": None, "code": None, "count": a["count"],
                        "duration_ms": round(a["total_ms"] / max(a["count"], 1), 4),
                        "duration_max_ms": round(a["max_ms"], 4), "latency_buckets": a["buckets"],
                        "matched": [], "thresholds": {}, "changed_fields": [], "snapshot": None, "error": None,
                        "bound_to": p.bound_to})


def _ctx_cols(ctx: EvalContext) -> dict:
    return {"run_id": ctx.run_id, "thread_id": ctx.thread_id, "agent_id": ctx.agent_id, "agent_key": ctx.agent_key,
            "agent_version": ctx.agent_version, "run_kind": ctx.run_kind, "user_id": ctx.user_id,
            "user_role": ctx.user_role, "tenant_id": ctx.tenant_id, "environment": ctx.environment,
            "mcp_server": ctx.mcp_server, "tool": ctx.tool, "model": ctx.model, "provider": ctx.provider,
            "correlation_id": ctx.correlation_id, "trace_id": ctx.trace_id, "parent_span_id": ctx.parent_span_id,
            "mcp_call_id": ctx.mcp_call_id, "llm_call_id": ctx.llm_call_id}


def event(ev: Evaluation, ctx: EvalContext, snapshot: dict | None = None) -> dict:
    p, f = ev.policy, ev.finding
    return {"_type": "evaluation", "id": ev.id, "ts": ev.ts.isoformat(), **_ctx_cols(ctx), "node": ctx.node,
            "stage": ev.stage, "policy_key": p.key, "policy_id": None if p.id.startswith("builtin:") else p.id,
            "policy_version": p.version, "category": p.category, "decision": ev.decision, "enforced": ev.enforced,
            "severity": p.severity, "action": p.action if ev.decision != "passed" else "allow",
            "reason": (f.reason or None) and f.reason[:300], "code": f.code, "count": 1,
            "duration_ms": round(ev.duration_ms, 4), "duration_max_ms": round(ev.duration_ms, 4),
            "latency_buckets": None, "matched": f.matches[:50], "thresholds": f.thresholds,
            "changed_fields": f.changed[:50], "snapshot": snapshot, "error": ev.error, "approval_id": ev.approval_id,
            "bound_to": p.bound_to, "context": _safe_ctx(ctx)}


def _safe_ctx(ctx: EvalContext) -> dict:
    v = ctx.view()
    if v.get("tool_args"):
        v["tool_args"] = safe_snapshot(v["tool_args"], original=True, limit=4000)
    return v


def approval_event(ev: Evaluation, ctx: EvalContext, cfg: dict) -> dict:
    m = (ev.finding.matches or [{}])[0]
    params = ctx.tool_args or {}
    ttl = int(m.get("ttl_minutes") or cfg.get("approval_timeout_minutes", 60))
    return {"_type": "approval", "id": ev.approval_id, "evaluation_id": ev.id, "tenant_id": ctx.tenant_id,
            "agent_id": ctx.agent_id, "agent_key": ctx.agent_key, "run_id": ctx.run_id, "thread_id": ctx.thread_id,
            "requested_action": f"{ctx.mcp_server or '-'}:{ctx.tool or '-'}", "target_resource": ctx.mcp_server or "-",
            "risk_level": ev.policy.severity, "requested_by": ctx.user_id or "system",
            "required_role": m.get("approver_role") or "admin", "ttl_minutes": ttl, "reason": ev.finding.reason,
            "action_params": safe_snapshot(params, original=True, limit=8000),
            "action_hash": action_hash(tenant_id=ctx.tenant_id, agent_id=ctx.agent_id,
                                       action=f"{ctx.mcp_server}:{ctx.tool}", resource=ctx.mcp_server or "-",
                                       params=params),
            "policy_key": ev.policy.key, "policy_version": ev.policy.version}


# ================================================================================================= bağlam
RUN: ContextVar[GuardRun | None] = ContextVar("airlab_guardrail_run", default=None)
_FALLBACK: list[Engine] = [Engine(builtin_engine_policies())]
_KILL: list[Callable[[], dict] | None] = [None]


def set_fallback(engine: Engine | None, kill: Callable[[], dict] | None = None) -> None:
    """Süreçte en son yüklenen snapshot (koşu bağlamı dışındaki çağrılar ve DB erişilemezse)."""
    _FALLBACK[0] = engine or Engine(builtin_engine_policies())
    if kill is not None:
        _KILL[0] = kill


def fallback_engine() -> Engine:
    return _FALLBACK[0]


def current() -> GuardRun | None:
    return RUN.get()


def start(engine: Engine | None, ctx: EvalContext, sink=None, kill=None) -> tuple[GuardRun, Any]:
    run = GuardRun(engine=engine or _FALLBACK[0], ctx=ctx, sink=sink, kill=kill or _KILL[0])
    return run, RUN.set(run)


def finish(run: GuardRun, token: Any = None, status: str = "completed") -> None:
    try:
        run.flush_passes()
    finally:
        if token is not None:
            try:
                RUN.reset(token)
            except ValueError:
                pass


def check(stages: str | tuple[str, ...], payload: Any, *, enforce: bool = True, **ctx_updates) -> Outcome:
    run = RUN.get()
    engine = run.engine if run is not None else _FALLBACK[0]
    # Koşu bağlamı yok (platform dışı: eval / test harness): süreç kimliği "system"; platform koşuları her zaman
    # doğrulanmış rolle bağlam kurar.
    base = run.ctx if run is not None else EvalContext(run_kind="unbound", user_role="system")
    ctx = replace(base, **{k: v for k, v in ctx_updates.items() if v is not None})
    if run is None and _KILL[0] is not None:
        ghost = GuardRun(engine=engine, ctx=ctx, kill=_KILL[0])
        out = engine.evaluate(stages, payload, ctx, ghost)
    else:
        out = engine.evaluate(stages, payload, ctx, run)
    if enforce and out.stopped:
        raise (GuardrailTerminated if out.decision == "terminated" else GuardrailBlocked)(out)
    return out


def observe(layer: str, *, decision: str, stage: str, reason: str | None = None, code: str | None = None,
            duration_ms: float = 0.0, policy_key: str | None = None, **ctx_updates) -> None:
    """Başka katmanda uygulanan kuralın kararını kaydeder (yeniden değerlendirmez)."""
    run = RUN.get()
    if run is None or run.sink is None:
        return
    key = policy_key or BOUND.get(layer)
    p = run.engine.by_key.get(key) if key else None
    if p is None:
        return
    from .model import Finding
    ctx = replace(run.ctx, stage=stage, **{k: v for k, v in ctx_updates.items() if v is not None})
    ev = Evaluation(new_id(), p, stage, decision, enforced=True,
                    finding=Finding(decision != "passed", reason or "", code), duration_ms=duration_ms)
    run.record(ev, ctx, {"layer": layer}, None)


# ================================================================================================= hook'lar
def node_start(name: str) -> None:
    run = RUN.get()
    seq = run.reserve("node_executions", f"node:{name}") if run is not None else None
    check("node_start", {"node": name, "_seq": seq, "_view": {"node": name}}, node=name)


def node_end(name: str) -> None:
    check("node_end", {"node": name}, node=name)


def guard_node(name: str, fn: Callable) -> Callable:
    """LangGraph node sarmalayıcı (sync / async). Guardrail hatası node hatası olarak yükselir."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def anode(state, *a, **kw):
            node_start(name)
            out = await fn(state, *a, **kw)
            node_end(name)
            return out
        return anode

    @functools.wraps(fn)
    def node(state, *a, **kw):
        node_start(name)
        out = fn(state, *a, **kw)
        node_end(name)
        return out
    return node


def _est_tokens(msgs: list) -> int:
    n = 0
    for m in msgs or []:
        t = m[1] if isinstance(m, (list, tuple)) and len(m) == 2 else m
        n += len(t) if isinstance(t, str) else len(json.dumps(t, default=str))
    return n // 4


def llm_request(call, envelope_id: str | None = None) -> None:
    """before_model: call.request['messages'] gerekirse sanitize edilir."""
    run = RUN.get()
    seq = run.reserve("llm_calls") if run is not None else None
    msgs = call.request.get("messages")
    payload = {"messages": msgs if isinstance(msgs, list) else [], "est_tokens": _est_tokens(msgs or []), "_seq": seq,
               "_view": {"task": call.task, "messages": len(msgs or []), "est_tokens": _est_tokens(msgs or [])}}
    out = check("llm_request", payload, model=call.target, llm_call_id=envelope_id, parent_span_id=envelope_id)
    if out.changed and isinstance(msgs, list):
        call.request["messages"] = out.payload.get("messages", msgs)


def _system_of(call) -> str:
    for m in call.request.get("messages") or []:
        if isinstance(m, (list, tuple)) and len(m) == 2 and m[0] == "system" and isinstance(m[1], str):
            return m[1]
    return ""


def llm_response_text(call, text: str, *, structured: bool = False) -> str:
    out = check("llm_response", {"text": text, "system": _system_of(call), "structured": structured},
                model=call.target)
    return out.payload.get("text", text) if isinstance(out.payload, dict) else text


def mcp_request(*, server: str, tool: str, args: dict, dql: str | None = None, call_id: str | None = None,
                view: dict | None = None, write_capable: bool = False, annotations: dict | None = None) -> Outcome:
    run = RUN.get()
    key = hashlib.sha1(json.dumps([server, tool, args], sort_keys=True, default=str).encode()).hexdigest()[:16]
    seq = run.reserve("tool_calls", f"call:{key}") if run is not None else None
    payload = {"args": args, "dql": dql, "call_key": key, "write_capable": write_capable, "_seq": seq,
               "server_annotations": annotations or {}, "tenant_id": None, "_view": view or {"tool": tool}}
    return check(("tool_request", "mcp_request"), payload, mcp_server=server, tool=tool,
                 tool_args=view or None, mcp_call_id=call_id, parent_span_id=call_id)


def mcp_response(*, server: str, tool: str, status: str, records: list, call_id: str | None = None) -> list:
    payload = {"status": status, "records": records,
               "_view": {"status": status, "records": records[:20], "record_count": len(records)}}
    out = check(("tool_response", "mcp_response"), payload, mcp_server=server, tool=tool, mcp_call_id=call_id,
                parent_span_id=call_id)
    return out.payload.get("records", records) if isinstance(out.payload, dict) else records


def memory_read(items: list[dict], **ctx) -> list[dict]:
    out = check("memory_read", {"items": items, "_view": {"items": items[:20], "count": len(items)}}, **ctx)
    return out.payload.get("items", items)


def memory_write(*, title: str, content: str, memory_type: str, source: str, **ctx) -> Outcome:
    return check("memory_write", {"title": title, "content": content, "memory_type": memory_type, "source": source},
                 **ctx)


def checkpoint(data: Any) -> Any:
    out = check("checkpoint", {"data": data, "_view": {"channels": sorted(data)[:50] if isinstance(data, dict) else None}})
    return out.payload.get("data", data) if out.changed else data


def final_output(text: str, facts: dict | None = None, **ctx) -> Outcome:
    """Kullanıcıya / rapora giden son metin. facts: evidence, evidence_ids, risk, window, schema, data."""
    return check("final_output", {"text": text, **(facts or {})}, **ctx)
