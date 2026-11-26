"""Guardrail okuma query'leri: Overview KPI'ları, Evaluation Logs, Analytics.

Kurallar:
* Geçen değerlendirmeler koşu başına toplu satırdır → sayımlar `count` toplamıdır (satır sayısı değil).
* Süre yüzdelikleri histogramdan yaklaşık hesaplanır (kova üst sınırı); "yaklaşık" olarak işaretlenir.
* False positive / false negative oranları YALNIZ etiketli veriden (ihlal triage'ı: false_positive / resolved;
  Policy Tester'da beklenen karar girilmiş testler) hesaplanır. Etiket yoksa değer `null` döner (uydurulmaz).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from airlab_agent.guardrails.runtime import BUCKETS
from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (GuardrailApproval, GuardrailEvaluation as E, GuardrailPolicy, GuardrailTestRun,
                         GuardrailViolation as V)

RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
NON_PASS = ("blocked", "warned", "redacted", "sanitized", "approval_required", "terminated", "error")
STOPPED = ("blocked", "terminated", "approval_required")
BUCKET_LABELS = [f"≤{b}ms" for b in BUCKETS] + [f">{BUCKETS[-1]}ms"]


def since(range_: str) -> datetime:
    return datetime.now(timezone.utc) - RANGES.get(range_, RANGES["24h"])


def _cnt():
    return func.coalesce(func.sum(E.count), 0)


def _bucket_expr():
    cases = [(E.duration_ms <= b, i) for i, b in enumerate(BUCKETS)]
    return case(*cases, else_=len(BUCKETS))


async def histogram(s: AsyncSession, where: list) -> list[int]:
    """Toplu satırların histogramı + tekil satırların süresi → tek histogram."""
    h = [0] * (len(BUCKETS) + 1)
    rows = (await s.execute(select(_bucket_expr().label("i"), func.count()).where(*where, E.latency_buckets.is_(None),
                                                                                 E.duration_ms.is_not(None))
                            .group_by(literal_column("i")))).all()
    for i, n in rows:
        h[int(i)] += int(n)
    for lb in (await s.execute(select(E.latency_buckets).where(*where, E.latency_buckets.is_not(None))
                               .order_by(E.ts.desc()).limit(50000))).scalars():
        for i, n in enumerate(lb or []):
            if i < len(h):
                h[i] += int(n or 0)
    return h


def percentile(h: list[int], q: float) -> float | None:
    total = sum(h)
    if not total:
        return None
    need, acc = q * total, 0
    for i, n in enumerate(h):
        acc += n
        if acc >= need:
            return float(BUCKETS[i]) if i < len(BUCKETS) else float(BUCKETS[-1])
    return float(BUCKETS[-1])


async def overview(s: AsyncSession, range_: str = "24h") -> dict:
    t0 = since(range_)
    w = [E.ts >= t0]
    tot = dict((await s.execute(select(E.decision, _cnt()).where(*w).group_by(E.decision))).all())
    total = sum(int(v) for v in tot.values())
    viol = sum(int(tot.get(d, 0)) for d in NON_PASS if d != "error")
    lat = (await s.execute(select(func.sum(E.duration_ms * E.count) / func.nullif(func.sum(E.count), 0),
                                  func.max(func.coalesce(E.duration_max_ms, E.duration_ms))).where(*w))).one()
    h = await histogram(s, w)
    errors = int((await s.execute(select(_cnt()).where(*w, E.error.is_not(None),
                                                       E.error != "timeout_exceeded"))).scalar_one())
    timeouts = int((await s.execute(select(_cnt()).where(*w, E.error == "timeout_exceeded"))).scalar_one())
    pol = (await s.execute(select(func.count(), func.count().filter(GuardrailPolicy.mandatory),
                                  func.count().filter(GuardrailPolicy.mode == "monitor"),
                                  func.count().filter(GuardrailPolicy.mode == "disabled"))
                           .where(GuardrailPolicy.status == "active"))).one()
    pend = int((await s.execute(select(func.count()).select_from(GuardrailApproval).where(
        GuardrailApproval.status == "pending", GuardrailApproval.expires_at > datetime.now(timezone.utc))))
        .scalar_one())
    open_v = dict((await s.execute(select(V.severity, func.count()).where(
        V.status.in_(("open", "acknowledged", "investigating"))).group_by(V.severity))).all())
    step = "date_trunc('hour', ts)" if range_ in ("1h", "24h") else "date_trunc('day', ts)"
    series = [{"t": t.isoformat(), "decision": d, "n": int(n)} for t, d, n in (await s.execute(
        select(literal_column(step).label("t"), E.decision, _cnt()).where(*w).group_by(literal_column("t"), E.decision)
        .order_by(literal_column("t")))).all()]
    top = [{"policy_key": k, "category": c, "n": int(n)} for k, c, n in (await s.execute(
        select(E.policy_key, E.category, _cnt()).where(*w, E.decision.in_(NON_PASS))
        .group_by(E.policy_key, E.category).order_by(_cnt().desc()).limit(8))).all()]
    agents = [{"agent": a or "sre-agent (built-in)", "n": int(n)} for a, n in (await s.execute(
        select(func.coalesce(E.agent_key, E.agent_id), _cnt()).where(*w, E.decision.in_(NON_PASS))
        .group_by(func.coalesce(E.agent_key, E.agent_id)).order_by(_cnt().desc()).limit(8))).all()]
    recent = (await s.execute(select(V).where(V.severity.in_(("critical", "high")))
                              .order_by(V.last_seen.desc()).limit(8))).scalars()
    from .service import violation_view
    from ..guardrails import runtime as g_rt
    w_ = g_rt.writer()
    return {"range": range_, "total_evaluations": total, "violations": viol,
            "blocked": int(tot.get("blocked", 0)) + int(tot.get("terminated", 0)),
            "redacted": int(tot.get("redacted", 0)) + int(tot.get("sanitized", 0)),
            "warned": int(tot.get("warned", 0)), "approval_required": int(tot.get("approval_required", 0)),
            "errors": errors, "timeouts": timeouts, "by_decision": {k: int(v) for k, v in tot.items()},
            "avg_ms": round(float(lat[0]), 4) if lat[0] is not None else None,
            "max_ms": round(float(lat[1]), 4) if lat[1] is not None else None,
            "p95_ms_approx": percentile(h, 0.95), "histogram": {"labels": BUCKET_LABELS, "counts": h},
            "policies": {"active": int(pol[0]), "mandatory": int(pol[1]), "monitor": int(pol[2]),
                         "disabled": int(pol[3])},
            "pending_approvals": pend, "open_violations": {k: int(v) for k, v in open_v.items()},
            "series": series, "top_policies": top, "top_agents": agents,
            "recent_critical": [violation_view(v) for v in recent],
            "health": {"writer_lost": getattr(w_, "lost", None), "writer_written": getattr(w_, "written", None),
                       "snapshot_revision": (g_rt._CACHE or {}).get("rev")}}


def _filters(range_: str | None, decision, policy, category, stage, agent, run_id, q, enforced, since_ts=None):
    w = [E.ts >= (since_ts or since(range_ or "24h"))]
    if decision:
        w.append(E.decision == decision) if decision != "violations" else w.append(E.decision.in_(NON_PASS))
    if policy:
        w.append(E.policy_key == policy)
    if category:
        w.append(E.category == category)
    if stage:
        w.append(E.stage == stage)
    if agent:
        w.append(or_(E.agent_id == agent, E.agent_key == agent))
    if run_id:
        w.append(E.run_id == run_id)
    if enforced is not None:
        w.append(E.enforced.is_(enforced))
    if q:
        like = f"%{q.lower()}%"
        w.append(or_(func.lower(E.reason).like(like), func.lower(E.policy_key).like(like), E.run_id == q,
                     E.tool == q, E.mcp_server == q))
    return w


def _row(e: E, full: bool = False) -> dict:
    d = {"id": str(e.id), "ts": e.ts.isoformat(), "run_id": e.run_id, "thread_id": e.thread_id, "run_kind": e.run_kind,
         "agent_id": e.agent_id, "agent_key": e.agent_key, "agent_version": e.agent_version, "user_id": e.user_id,
         "node": e.node, "stage": e.stage, "policy_key": e.policy_key, "policy_version": e.policy_version,
         "category": e.category, "decision": e.decision, "enforced": e.enforced, "severity": e.severity,
         "action": e.action, "reason": e.reason, "code": e.code, "count": e.count, "duration_ms": e.duration_ms,
         "duration_max_ms": e.duration_max_ms, "mcp_server": e.mcp_server, "tool": e.tool, "model": e.model,
         "error": e.error, "bound_to": e.bound_to, "approval_id": str(e.approval_id) if e.approval_id else None,
         "violation_id": str(e.violation_id) if e.violation_id else None}
    if full:
        d.update(matched=e.matched, thresholds=e.thresholds, changed_fields=e.changed_fields, snapshot=e.snapshot,
                 context=e.context, latency_buckets=e.latency_buckets, provider=e.provider,
                 correlation_id=e.correlation_id, trace_id=e.trace_id, parent_span_id=e.parent_span_id,
                 mcp_call_id=e.mcp_call_id, llm_call_id=e.llm_call_id, user_role=e.user_role,
                 environment=e.environment, tenant_id=e.tenant_id, latency_labels=BUCKET_LABELS)
    return d


async def logs(s: AsyncSession, *, range_: str = "24h", decision=None, policy=None, category=None, stage=None,
               agent=None, run_id=None, q=None, enforced=None, limit: int = 50, offset: int = 0) -> dict:
    w = _filters(range_, decision, policy, category, stage, agent, run_id, q, enforced)
    total = int((await s.execute(select(func.count()).select_from(E).where(*w))).scalar_one())
    rows = (await s.execute(select(E).where(*w).order_by(E.ts.desc()).limit(limit).offset(offset))).scalars()
    return {"total": total, "items": [_row(e) for e in rows], "limit": limit, "offset": offset}


async def log_detail(s: AsyncSession, eid: str) -> dict | None:
    try:
        e = await s.get(E, uuid.UUID(eid))
    except ValueError:
        return None
    if e is None:
        return None
    d = _row(e, full=True)
    pol = (await s.execute(select(GuardrailPolicy.id, GuardrailPolicy.name, GuardrailPolicy.mandatory)
                           .where(GuardrailPolicy.key == e.policy_key))).first()
    d["policy"] = {"id": str(pol[0]), "name": pol[1], "mandatory": pol[2]} if pol else None
    if e.run_id:
        d["run_evaluations"] = [_row(x) for x in (await s.execute(
            select(E).where(E.run_id == e.run_id).order_by(E.ts).limit(200))).scalars()]
    return d


async def analytics(s: AsyncSession, range_: str = "7d") -> dict:
    t0 = since(range_)
    w = [E.ts >= t0]
    by_cat = [{"category": c, "decision": d, "n": int(n)} for c, d, n in (await s.execute(
        select(E.category, E.decision, _cnt()).where(*w).group_by(E.category, E.decision))).all()]
    by_stage = [{"stage": st, "decision": d, "n": int(n)} for st, d, n in (await s.execute(
        select(E.stage, E.decision, _cnt()).where(*w).group_by(E.stage, E.decision))).all()]
    pol_rows = (await s.execute(select(E.policy_key, E.category, _cnt(),
                                       func.coalesce(func.sum(E.count).filter(E.decision.in_(NON_PASS)), 0),
                                       func.sum(E.duration_ms * E.count) / func.nullif(func.sum(E.count), 0),
                                       func.max(func.coalesce(E.duration_max_ms, E.duration_ms)))
                                .where(*w).group_by(E.policy_key, E.category).order_by(_cnt().desc()).limit(100))).all()
    policies = []
    for k, c, n, nv, avg, mx in pol_rows:
        h = await histogram(s, [*w, E.policy_key == k])
        policies.append({"policy_key": k, "category": c, "evaluations": int(n), "violations": int(nv),
                         "rate": round(int(nv) / int(n), 4) if n else None,
                         "avg_ms": round(float(avg), 4) if avg is not None else None,
                         "max_ms": round(float(mx), 4) if mx is not None else None,
                         "p50_ms_approx": percentile(h, 0.5), "p95_ms_approx": percentile(h, 0.95)})
    mode = [{"enforced": bool(en), "decision": d, "n": int(n)} for en, d, n in (await s.execute(
        select(E.enforced, E.decision, _cnt()).where(*w, E.decision.in_(NON_PASS)).group_by(E.enforced, E.decision)))
        .all()]
    # Etiketli veri: ihlal triage'ı
    lab = (await s.execute(select(func.count().filter(V.status == "false_positive"),
                                  func.count().filter(V.status == "resolved")).where(V.last_seen >= t0))).one()
    fp, tp = int(lab[0]), int(lab[1])
    # Etiketli test koşuları (Policy Tester'da beklenen karar girilmiş)
    tr = (await s.execute(select(GuardrailTestRun.decision, GuardrailTestRun.expected_decision)
                          .where(GuardrailTestRun.created_at >= t0, GuardrailTestRun.expected_decision.is_not(None))))\
        .all()
    stop = set(STOPPED) | {"redacted", "sanitized"}
    t_fp = sum(1 for d, x in tr if d in stop and x == "passed")
    t_fn = sum(1 for d, x in tr if d == "passed" and x != "passed")
    t_neg = sum(1 for _, x in tr if x == "passed")
    t_pos = sum(1 for _, x in tr if x != "passed")
    return {"range": range_, "by_category": by_cat, "by_stage": by_stage, "policies": policies,
            "monitor_vs_enforce": mode, "latency_note": "yüzdelikler histogram kovasının üst sınırıdır (yaklaşık)",
            "labels": {
                "triage": {"false_positive": fp, "true_positive": tp,
                           "false_positive_rate": round(fp / (fp + tp), 4) if fp + tp else None},
                "tests": {"labeled": len(tr), "false_positive": t_fp, "false_negative": t_fn,
                          "false_positive_rate": round(t_fp / t_neg, 4) if t_neg else None,
                          "false_negative_rate": round(t_fn / t_pos, 4) if t_pos else None},
                "note": "Oranlar yalnız etiketli veriden hesaplanır; etiket yoksa değer gösterilmez."}}
