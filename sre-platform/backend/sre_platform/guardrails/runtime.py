"""Platform tarafı guardrail runtime'ı: policy snapshot yükleyici, kill switch okuyucu ve kalıcı yazıcı.

* `load_engine` DB'deki aktif policy'lerden (config revision'a göre cache'li) bir `Engine` kurar. DB okunamazsa son
  başarılı snapshot, o da yoksa koddaki yerleşik katalog kullanılır (mandatory policy'ler her durumda enforce).
* Kill switch süreç başına arka plan görevinde (yazıcının pump'ı) birkaç saniyede bir okunur; koşu sürerken de etkilidir.
* Yazıcı: değerlendirmeler thread-safe kuyruğa düşer; pump toplu INSERT yapar, ihlalleri fingerprint ile tekilleştirir
  (açık kayıt varsa sayaç artar), onay isteklerini oluşturur ve policy `last_triggered_at`'i günceller. Yazma hatası
  agent çalışmasını etkilemez (kayıp sayılır, loglanır).
* `start` koşuyu guardrail bağlamına alır ve before_agent (girdi, eşzamanlılık, kill switch) değerlendirmesini yapar.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import time
import uuid
from datetime import datetime, timedelta, timezone

from airlab_agent.guardrails import runtime as gr
from airlab_agent.guardrails.engine import Engine, merged_config
from airlab_agent.guardrails.model import Assignment, EvalContext, GuardrailBlocked, Policy
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..core.logging import correlation_id
from ..db.models import (AgentRun, GuardrailApproval, GuardrailConfiguration, GuardrailEvaluation, GuardrailPolicy,
                         GuardrailPolicyAssignment, GuardrailViolation, Investigation, InvestigationStatus, User)
from ..db.session import transaction

log = logging.getLogger("sre.guardrails")
FLUSH_S = 1.0
KILL_POLL_S = 3.0
BATCH = 300
_EV_COLS = {c.name for c in GuardrailEvaluation.__table__.columns}
_STR = {"run_id": 160, "thread_id": 160, "agent_id": 64, "agent_key": 64, "agent_version": 32, "user_id": 64,
        "user_role": 16, "node": 64, "stage": 24, "policy_key": 80, "category": 32, "decision": 24, "severity": 16,
        "action": 24, "reason": 300, "code": 64, "error": 200, "mcp_server": 64, "tool": 128, "model": 128,
        "provider": 32, "correlation_id": 64, "trace_id": 64, "parent_span_id": 64, "mcp_call_id": 64,
        "llm_call_id": 64, "bound_to": 64, "environment": 64, "tenant_id": 64, "run_kind": 24}

_CACHE: dict = {"rev": None, "engine": None}
_KILL: dict = {"state": {}, "at": 0.0}


def kill_state() -> dict:
    return _KILL["state"]


def _policy(p: GuardrailPolicy, asg: list[GuardrailPolicyAssignment]) -> Policy:
    return Policy(id=str(p.id), key=p.key, name=p.name, category=p.category, type=p.policy_type, severity=p.severity,
                  mode=p.mode, mandatory=p.mandatory, stages=tuple(p.stages or ()), action=p.action,
                  priority=p.priority, version=p.version, params=p.params or {},
                  assignments=tuple(Assignment(a.scope, a.value) for a in asg) or (Assignment("global"),),
                  status=p.status, bound_to=p.bound_to)


async def load_engine(sm, *, force: bool = False) -> Engine:
    """Aktif policy snapshot'ı (config revision değişmedikçe cache'ten)."""
    try:
        async with transaction(sm) as s:
            row = (await s.execute(select(GuardrailConfiguration).where(GuardrailConfiguration.id == 1)))\
                .scalar_one_or_none()
            rev = row.revision if row else 0
            if not force and _CACHE["engine"] is not None and _CACHE["rev"] == rev:
                return _CACHE["engine"]
            ps = list((await s.execute(select(GuardrailPolicy))).scalars())
            asg: dict = {}
            for a in (await s.execute(select(GuardrailPolicyAssignment))).scalars():
                asg.setdefault(a.policy_id, []).append(a)
            policies = [_policy(p, asg.get(p.id, [])) for p in ps]
            if not policies:                       # henüz seed edilmemiş: yerleşik katalog
                from airlab_agent.guardrails.catalog import builtin_engine_policies
                policies = builtin_engine_policies()
            eng = Engine(policies, (row.data if row else {}) or {}, rev)
            _KILL["state"], _KILL["at"] = eng.config.get("kill_switch") or {}, time.monotonic()
        _CACHE["rev"], _CACHE["engine"] = rev, eng
        gr.set_fallback(eng, kill_state)
        return eng
    except Exception as e:  # noqa: BLE001 — DB erişilemezse son snapshot / yerleşik katalog
        log.warning("guardrail snapshot yüklenemedi: %s", type(e).__name__)
        return _CACHE["engine"] or gr.fallback_engine()


async def refresh_kill(sm) -> None:
    try:
        async with transaction(sm) as s:
            data = (await s.execute(select(GuardrailConfiguration.data).where(GuardrailConfiguration.id == 1)))\
                .scalar_one_or_none()
        _KILL["state"] = merged_config(data or {}).get("kill_switch") or {}
        _KILL["at"] = time.monotonic()
    except Exception as e:  # noqa: BLE001 — okunamazsa son değer korunur
        log.debug("kill switch okunamadı: %s", type(e).__name__)


# ================================================================================================= yazıcı
def _ts(v) -> datetime:
    return datetime.fromisoformat(v) if isinstance(v, str) else (v or datetime.now(timezone.utc))


def _uuid(v):
    if v is None:
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None


def to_eval_row(ev: dict) -> dict:
    r = {k: v for k, v in ev.items() if k in _EV_COLS}
    r["id"] = _uuid(ev["id"]) or uuid.uuid4()
    r["ts"] = _ts(ev.get("ts"))
    r["policy_id"] = _uuid(ev.get("policy_id"))
    r["approval_id"] = _uuid(ev.get("approval_id"))
    r["tenant_id"] = r.get("tenant_id") or "default"
    for k, n in _STR.items():
        if r.get(k) is not None:
            r[k] = str(r[k])[:n]
    for k, d in (("matched", []), ("thresholds", {}), ("changed_fields", [])):
        if r.get(k) is None:
            r[k] = d
    return r


class GuardrailWriter:
    def __init__(self, sm):
        self.sm = sm
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.lost = 0
        self.written = 0
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    def put(self, rec: dict) -> None:
        self.q.put(rec)

    async def flush(self) -> int:
        n = 0
        while True:
            recs = []
            while len(recs) < BATCH:
                try:
                    recs.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if not recs:
                return n
            try:
                await self._write(recs)
                n += len(recs)
                self.written += len(recs)
            except asyncio.CancelledError:
                self.lost += len(recs)
                raise
            except Exception as e:  # noqa: BLE001
                self.lost += len(recs)
                log.warning("guardrail: %d kayıt yazılamadı hata=%s", len(recs), type(e).__name__)

    async def _write(self, recs: list[dict]) -> None:
        evals = [r for r in recs if r.get("_type", "evaluation") == "evaluation"]
        approvals = [r for r in recs if r.get("_type") == "approval"]
        rows = [to_eval_row(r) for r in evals]
        async with transaction(self.sm) as s:
            trig: dict[str, datetime] = {}
            for r in rows:
                if r["decision"] == "passed":
                    continue
                trig[r["policy_key"]] = max(trig.get(r["policy_key"], r["ts"]), r["ts"])
                r["violation_id"] = await upsert_violation(s, r)
            if rows:
                await s.execute(insert(GuardrailEvaluation), rows)
            for a in approvals:
                at = datetime.now(timezone.utc)
                s.add(GuardrailApproval(
                    id=_uuid(a["id"]) or uuid.uuid4(), tenant_id=a.get("tenant_id") or "default",
                    evaluation_id=_uuid(a.get("evaluation_id")), agent_id=(a.get("agent_id") or None),
                    agent_key=a.get("agent_key"), run_id=(a.get("run_id") or "")[:160] or None,
                    thread_id=(a.get("thread_id") or "")[:160] or None,
                    requested_action=str(a["requested_action"])[:200], target_resource=str(a["target_resource"])[:200],
                    risk_level=a.get("risk_level") or "high", requested_by=str(a.get("requested_by") or "system")[:64],
                    required_role=a.get("required_role") or "admin", requested_at=at,
                    expires_at=at + timedelta(minutes=int(a.get("ttl_minutes") or 60)), status="pending",
                    reason=(a.get("reason") or "")[:300] or None, action_params=a.get("action_params") or {},
                    action_hash=a["action_hash"], policy_key=a.get("policy_key"),
                    policy_version=a.get("policy_version")))
            for k, ts in trig.items():
                await s.execute(update(GuardrailPolicy).where(GuardrailPolicy.key == k)
                                .values(last_triggered_at=func.greatest(
                                    func.coalesce(GuardrailPolicy.last_triggered_at, ts), ts)))

    async def _pump(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), FLUSH_S)
            except asyncio.TimeoutError:
                pass
            await self.flush()
            if time.monotonic() - _KILL["at"] > KILL_POLL_S:
                await refresh_kill(self.sm)

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._pump(), name="guardrails")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass
        await self.flush()


async def upsert_violation(s, r: dict) -> uuid.UUID | None:
    """Açık (open / acknowledged / investigating) aynı fingerprint'li ihlal varsa sayaç artar; yoksa yeni kayıt."""
    from .service import fingerprint
    fp = fingerprint(r)
    ins = pg_insert(GuardrailViolation).values(
        id=uuid.uuid4(), tenant_id=r["tenant_id"], fingerprint=fp, severity=r.get("severity") or "medium",
        category=r["category"], policy_key=r["policy_key"], policy_id=r.get("policy_id"),
        agent_id=r.get("agent_id"), agent_key=r.get("agent_key"), run_id=r.get("run_id"), node=r.get("node"),
        stage=r.get("stage"), mcp_server=r.get("mcp_server"), tool=r.get("tool"), reason=r.get("reason"),
        code=r.get("code"), decision=r["decision"], action_taken=r.get("action"), enforced=r.get("enforced", True),
        first_seen=r["ts"], last_seen=r["ts"], occurrences=max(1, int(r.get("count") or 1)), status="open",
        history=[], last_evaluation_id=r["id"])
    ins = ins.on_conflict_do_update(
        index_elements=["tenant_id", "fingerprint"],
        index_where=GuardrailViolation.status.in_(("open", "acknowledged", "investigating")),
        set_={"occurrences": GuardrailViolation.occurrences + ins.excluded.occurrences,
              "last_seen": func.greatest(GuardrailViolation.last_seen, ins.excluded.last_seen),
              "run_id": ins.excluded.run_id, "reason": ins.excluded.reason, "decision": ins.excluded.decision,
              "action_taken": ins.excluded.action_taken, "enforced": ins.excluded.enforced,
              "severity": ins.excluded.severity, "last_evaluation_id": ins.excluded.last_evaluation_id},
    ).returning(GuardrailViolation.id)
    return (await s.execute(ins)).scalar_one_or_none()


_WRITER: GuardrailWriter | None = None


async def install(sm) -> GuardrailWriter:
    """Süreç açılışı: yerleşik policy'leri seed eder, snapshot'ı yükler ve yazıcıyı başlatır."""
    global _WRITER
    try:
        from .service import ensure_builtins
        async with transaction(sm) as s:
            await ensure_builtins(s)
    except Exception as e:  # noqa: BLE001 — seed edilemezse kod kataloğu (fallback) kullanılır
        log.warning("guardrail policy seed edilemedi: %s", type(e).__name__)
    await load_engine(sm, force=True)
    w = GuardrailWriter(sm)
    await w.start()
    _WRITER = w
    return w


async def uninstall(w: GuardrailWriter) -> None:
    global _WRITER
    if _WRITER is w:
        _WRITER = None
    await w.stop()


def writer() -> GuardrailWriter | None:
    return _WRITER


# ================================================================================================= koşu bağlamı
class GuardSession:
    def __init__(self, run: gr.GuardRun, token):
        self.run, self._token = run, token
        self.closed = False

    @property
    def ctx(self) -> EvalContext:
        return self.run.ctx

    def finish(self, status: str = "completed", output: str | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            prev = gr.RUN.get()
            tok = gr.RUN.set(self.run) if prev is not self.run else None
            try:
                gr.check("after_agent", {"status": status, "text": output or ""}, enforce=False)
            finally:
                if tok is not None:
                    gr.RUN.reset(tok)
        except Exception:  # noqa: BLE001 — after_agent kaydı koşu sonucunu değiştirmez
            log.debug("after_agent guardrail değerlendirmesi başarısız", exc_info=True)
        gr.finish(self.run, self._token, status)
        self._token = None


async def concurrent_runs(sm, agent_id: str | None = None, *, self_counted: bool = False) -> int:
    """Aynı agent'ın şu an süren DİĞER koşuları (built-in SRE agent → RUNNING investigation; custom agent → agent_runs)."""
    try:
        async with transaction(sm) as s:
            if agent_id:
                n = (await s.execute(select(func.count()).select_from(AgentRun).where(
                    AgentRun.status == "running", AgentRun.agent_id == uuid.UUID(str(agent_id))))).scalar_one()
            else:
                n = (await s.execute(select(func.count()).select_from(Investigation)
                                     .where(Investigation.status == InvestigationStatus.RUNNING))).scalar_one()
        return max(0, int(n) - (1 if self_counted else 0))
    except Exception:  # noqa: BLE001
        return 0


async def user_role(sm, user_id) -> str | None:
    if not user_id:
        return None
    try:
        async with transaction(sm) as s:
            r = (await s.execute(select(User.role).where(User.id == uuid.UUID(str(user_id))))).scalar_one_or_none()
        return getattr(r, "value", r).lower() if r is not None else None
    except Exception:  # noqa: BLE001
        return None


async def start(rt, *, kind: str, run_id: str, thread_id: str | None = None, trace_id: str | None = None,
                user_id=None, user_role_: str | None = None, agent_id: str | None = None,
                agent_key: str | None = None, agent_version: str | None = None, environment: str | None = None,
                input_text: str | None = None, input_schema: dict | None = None, variables: dict | None = None,
                check_input: bool = True, self_counted: bool = False) -> GuardSession:
    """Koşuyu guardrail bağlamına alır + before_agent. Engellenirse GuardrailBlocked / GuardrailTerminated yükselir
    (bağlam kapatılmış olarak)."""
    eng = await load_engine(rt.sm)
    cid = correlation_id.get()
    ctx = EvalContext(run_id=str(run_id)[:160], thread_id=thread_id, agent_id=agent_id, agent_key=agent_key,
                      agent_version=agent_version, run_kind=kind, user_id=str(user_id) if user_id else None,
                      # kullanıcısız koşu (schedule / watch) = system; kullanıcı var ama rolü bulunamazsa None (deny)
                      user_role=user_role_ or (await user_role(rt.sm, user_id) if user_id else "system"),
                      environment=environment or "default",
                      correlation_id=None if cid == "-" else cid, trace_id=trace_id)
    w = _WRITER
    run, token = gr.start(eng, ctx, sink=w.put if w is not None else None, kill=kill_state)
    sess = GuardSession(run, token)
    try:
        payload = {"text": input_text or "",
                   "concurrent_runs": await concurrent_runs(rt.sm, agent_id, self_counted=self_counted)}
        if not check_input:
            payload["text"] = ""
        if input_schema:
            payload["input_schema"] = input_schema
        if variables:
            payload["variables"] = variables
        gr.check("before_agent", payload, enforce=True)
    except BaseException as e:
        sess.finish("rejected" if isinstance(e, GuardrailBlocked) else "failed")
        raise
    return sess


def blocked_message(e: GuardrailBlocked) -> str:
    out = getattr(e, "outcome", None)
    return (getattr(out, "message", None) or str(e) or "guardrail engelledi")[:500]
