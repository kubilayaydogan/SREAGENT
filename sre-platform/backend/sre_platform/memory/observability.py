"""Memory observability query'leri: Overview metrikleri, Short-Term (thread) ve Checkpoint Explorer, Working Memory,
Memory Activity ve Retrieval kayıtları.

* Checkpoint Explorer yalnız LangGraph'ın desteklenen API'lerini kullanır (`aget_state_history`, `aget_state`,
  `aget_tuple`); veriye doğrudan yazılmaz, replay / fork / resume SUNULMAZ (dış sistemde yan etki riski).
* Explorer okumaları memory event'i üretmez (`events.suppressed`).
* Working Memory, Agent Trace olaylarından okunur (kopya yok); node ayrıntısı Agent Trace node-exec API'sindedir.
* Tüm değerler maskeli kopyadır (safe_copy); ham telemetri `records` özetlenir.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.agent_trace import safe_copy, state_diff

from ..db.models import (AgentTraceEvent, AgentTraceRun, Conversation, Investigation, InvestigationStatus, Message,
                         MemoryEvent, MemoryRetrieval, Role, TraceLink)
from . import events
from .context import estimate_tokens
from .registry import EMBEDDED_AGENT

RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def since_of(rng: str) -> datetime:
    return datetime.now(timezone.utc) - RANGES.get(rng, RANGES["24h"])


def _iso(d) -> str | None:
    return d.astimezone(timezone.utc).isoformat() if d else None


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


# ------------------------------------------------------------------------------------------------ Overview
METRIC_DEFS = {
    "total_memory_records": "Long-term kayıtlar: sre.memory_records (aktif) + known issue + runbook + episode "
                            "(arşivsiz) + Store notları (app/user). Kaynaklar ayrık tablolardır; checkpoint ve "
                            "thread sayılmaz.",
    "active_threads": "Seçili aralıkta checkpoint yazılmış thread sayısı.",
    "total_checkpoints": "langgraph.checkpoints satır sayısı (tüm thread'ler, tüm namespace'ler).",
    "semantic": "memory_records(semantic) + known issue + Store kullanıcı bağlamı.",
    "episodic": "episode + memory_records(episodic) + Store uygulama bulguları.",
    "procedural": "runbook + memory_records(procedural).",
    "reads": "MEMORY_READ + MEMORY_SEARCH olayları (checkpoint yüklemeleri ayrı).",
    "writes": "MEMORY_WRITE + MEMORY_UPDATE olayları (checkpoint yazımları ayrı).",
    "retrievals": "Retrieval pipeline kayıtları (agent + analysis; playground hariç).",
    "retrieval_success_rate": "Hatasız retrieval / tüm retrieval.",
    "empty_retrieval_rate": "Hiç kayıt dönmeyen / hatasız retrieval.",
    "read_latency": "MEMORY_READ/SEARCH süreleri (ms, p50/p95).",
    "write_latency": "MEMORY_WRITE süreleri (ms, p50/p95).",
    "retrieval_latency": "Retrieval pipeline süresi (ms, p50/p95/p99).",
    "context_tokens": "Context'e eklenen memory bölümlerinin tahmini token toplamı (karakter/4).",
    "storage": "PostgreSQL tablo boyutları (indeks dahil).",
    "expired": "status=expired memory_records.",
    "rejected": "Aralıktaki MEMORY_REJECTED + MEMORY_POLICY_VIOLATION olayları.",
}


async def _count(s, sql: str, **p) -> int:
    return int(await s.scalar(text(sql), p) or 0)


async def health(rt, s: AsyncSession) -> dict:
    out: dict[str, Any] = {}
    for name, sql in (("checkpointer", "SELECT 1 FROM langgraph.checkpoints LIMIT 1"),
                      ("store", "SELECT 1 FROM langgraph.store LIMIT 1"),
                      ("memory_tables", "SELECT 1 FROM sre.memory_records LIMIT 1")):
        t0 = asyncio.get_running_loop().time()
        try:
            await s.execute(text(sql))
            out[name] = {"status": "healthy", "latency_ms": round((asyncio.get_running_loop().time() - t0) * 1000, 1)}
        except Exception as e:  # noqa: BLE001
            out[name] = {"status": "down", "error": type(e).__name__}
    pub = events.publisher()
    out["event_publisher"] = {"status": "healthy" if pub is not None else "down",
                              "published": getattr(pub, "published", 0), "lost": getattr(pub, "lost", 0),
                              "observability_enabled": bool(pub and pub.enabled)}
    out["vector_index"] = {"status": "not_configured", "note": "embedding modeli / pgvector yok"}
    out["backends"] = {"checkpointer": type(rt.asaver).__mro__[1].__name__ if rt.asaver else None,
                       "checkpointer_sync": type(rt.saver).__mro__[1].__name__ if rt.saver else None,
                       "store": type(rt.store).__name__ if rt.store else None,
                       "memory_store": "PostgreSQL sre.memory_records", "vector_store": None}
    return out


async def overview(rt, s: AsyncSession, *, agent_id: str, rng: str) -> dict:
    since = since_of(rng)
    emb = agent_id == EMBEDDED_AGENT
    c = {}
    mr = dict((await s.execute(text(
        "SELECT memory_type, count(*) FROM sre.memory_records WHERE agent_id = :a AND status = 'active' "
        "GROUP BY memory_type"), {"a": agent_id})).all())
    c["memory_records"] = sum(mr.values())
    c["known_issues"] = await _count(s, "SELECT count(*) FROM sre.known_issues WHERE archived_at IS NULL") if emb else 0
    c["runbooks"] = await _count(s, "SELECT count(*) FROM sre.runbooks WHERE archived_at IS NULL") if emb else 0
    c["episodes"] = await _count(s, "SELECT count(*) FROM sre.agent_episodes WHERE archived_at IS NULL") if emb else 0
    c["store_app"] = await _count(s, "SELECT count(*) FROM langgraph.store WHERE prefix LIKE 'app.%'") if emb else 0
    c["store_user"] = await _count(s, "SELECT count(*) FROM langgraph.store WHERE prefix LIKE 'user.%'") if emb else 0
    c["store_conversation"] = await _count(
        s, "SELECT count(*) FROM langgraph.store WHERE prefix LIKE 'conversation.%'") if emb else 0
    semantic = mr.get("semantic", 0) + c["known_issues"] + c["store_user"]
    episodic = mr.get("episodic", 0) + c["episodes"] + c["store_app"]
    procedural = mr.get("procedural", 0) + c["runbooks"]
    ck = (await s.execute(text(
        "SELECT count(*), count(DISTINCT thread_id), count(DISTINCT thread_id) FILTER "
        "(WHERE (checkpoint->>'ts')::timestamptz >= :since) FROM langgraph.checkpoints"), {"since": since})).one()
    ev = dict((await s.execute(select(MemoryEvent.event_type, func.count()).where(
        MemoryEvent.ts >= since, MemoryEvent.agent_id == agent_id).group_by(MemoryEvent.event_type))).all())

    async def durs(types: tuple[str, ...]) -> list[float]:
        return [float(x) for x in (await s.execute(select(MemoryEvent.duration_ms).where(
            MemoryEvent.ts >= since, MemoryEvent.agent_id == agent_id, MemoryEvent.event_type.in_(types),
            MemoryEvent.duration_ms.is_not(None)).limit(20000))).scalars()]
    rd, wd = await durs(("MEMORY_READ", "MEMORY_SEARCH")), await durs(("MEMORY_WRITE", "MEMORY_UPDATE"))
    cw = await durs(("MEMORY_CHECKPOINT_CREATED",))
    rq = select(MemoryRetrieval.status, MemoryRetrieval.empty, MemoryRetrieval.duration_ms,
                MemoryRetrieval.injection).where(MemoryRetrieval.ts >= since, MemoryRetrieval.agent_id == agent_id,
                                                 MemoryRetrieval.origin != "playground")
    rrows = (await s.execute(rq.limit(20000))).all()
    ok = [r for r in rrows if r[0] == "ok"]
    tokens = sum(int((r[3] or {}).get("token_estimate") or 0) for r in rrows
                 if (r[3] or {}).get("status") == "injected")
    sizes = dict((await s.execute(text(
        "SELECT t, pg_total_relation_size(t::regclass) FROM unnest(ARRAY['langgraph.checkpoints',"
        "'langgraph.checkpoint_blobs','langgraph.checkpoint_writes','langgraph.store','sre.memory_records',"
        "'sre.memory_versions','sre.memory_events','sre.memory_retrievals','sre.agent_episodes','sre.known_issues',"
        "'sre.runbooks']) AS t"))).all())
    expired = await _count(s, "SELECT count(*) FROM sre.memory_records WHERE agent_id = :a AND status = 'expired'",
                           a=agent_id)
    return {
        "agent_id": agent_id, "range": rng, "since": _iso(since), "definitions": METRIC_DEFS,
        "metrics": {
            "total_memory_records": c["memory_records"] + c["known_issues"] + c["runbooks"] + c["episodes"]
            + c["store_app"] + c["store_user"],
            "by_backing": c, "active_threads": int(ck[2]) if emb else 0, "total_threads": int(ck[1]) if emb else 0,
            "total_checkpoints": int(ck[0]) if emb else 0,
            "semantic": semantic, "episodic": episodic, "procedural": procedural,
            "reads": ev.get("MEMORY_READ", 0) + ev.get("MEMORY_SEARCH", 0),
            "writes": ev.get("MEMORY_WRITE", 0) + ev.get("MEMORY_UPDATE", 0),
            "checkpoint_writes": ev.get("MEMORY_CHECKPOINT_CREATED", 0),
            "checkpoint_loads": ev.get("MEMORY_CHECKPOINT_LOADED", 0),
            "retrievals": len(rrows),
            "retrieval_success_rate": round(len(ok) / len(rrows), 4) if rrows else None,
            "empty_retrieval_rate": round(sum(1 for r in ok if r[1]) / len(ok), 4) if ok else None,
            "read_latency": {"p50": _pct(rd, .5), "p95": _pct(rd, .95), "n": len(rd)},
            "write_latency": {"p50": _pct(wd, .5), "p95": _pct(wd, .95), "n": len(wd)},
            "checkpoint_write_latency": {"p50": _pct(cw, .5), "p95": _pct(cw, .95), "n": len(cw)},
            "retrieval_latency": {"p50": _pct([float(r[2]) for r in rrows if r[2] is not None], .5),
                                  "p95": _pct([float(r[2]) for r in rrows if r[2] is not None], .95),
                                  "p99": _pct([float(r[2]) for r in rrows if r[2] is not None], .99)},
            "context_tokens": tokens,
            "storage_bytes": {k: int(v) for k, v in sizes.items()}, "storage_total": int(sum(sizes.values())),
            "expired": expired,
            "rejected": ev.get("MEMORY_REJECTED", 0) + ev.get("MEMORY_POLICY_VIOLATION", 0),
            "events_by_type": ev,
        },
        "health": await health(rt, s),
        "charts": await charts(s, agent_id=agent_id, since=since, rng=rng, dist={"semantic": semantic,
                                                                                "episodic": episodic,
                                                                                "procedural": procedural}),
    }


async def charts(s: AsyncSession, *, agent_id: str, since: datetime, rng: str, dist: dict) -> dict:
    bucket = "hour" if rng in ("1h", "24h") else "day"
    rw = (await s.execute(text(
        f"SELECT date_trunc('{bucket}', ts) b, "
        "count(*) FILTER (WHERE event_type IN ('MEMORY_READ','MEMORY_SEARCH')) r, "
        "count(*) FILTER (WHERE event_type IN ('MEMORY_WRITE','MEMORY_UPDATE')) w, "
        "count(*) FILTER (WHERE event_type = 'MEMORY_CHECKPOINT_CREATED') c "
        "FROM sre.memory_events WHERE ts >= :s AND agent_id = :a GROUP BY 1 ORDER BY 1"),
        {"s": since, "a": agent_id})).all()
    rl = (await s.execute(text(
        f"SELECT date_trunc('{bucket}', ts) b, percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms), "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), "
        "sum(COALESCE((injection->>'token_estimate')::int, 0)) FILTER (WHERE injection->>'status' = 'injected') "
        "FROM sre.memory_retrievals WHERE ts >= :s AND agent_id = :a AND origin <> 'playground' "
        "GROUP BY 1 ORDER BY 1"), {"s": since, "a": agent_id})).all()
    growth = (await s.execute(text(
        f"SELECT date_trunc('{bucket}', c) b, count(*) FROM ("
        "SELECT created_at c FROM sre.memory_records WHERE agent_id = :a UNION ALL "
        "SELECT created_at FROM sre.agent_episodes WHERE :emb UNION ALL "
        "SELECT created_at FROM sre.known_issues WHERE :emb UNION ALL "
        "SELECT created_at FROM sre.runbooks WHERE :emb) x WHERE c >= :s GROUP BY 1 ORDER BY 1"),
        {"s": since, "a": agent_id, "emb": agent_id == EMBEDDED_AGENT})).all()
    val = dict((await s.execute(text(
        "SELECT event_type, count(*) FROM sre.memory_events WHERE ts >= :s AND agent_id = :a AND event_type IN "
        "('MEMORY_VALIDATED','MEMORY_REJECTED','MEMORY_POLICY_VIOLATION','MEMORY_APPROVED') GROUP BY 1"),
        {"s": since, "a": agent_id})).all())
    by_agent = dict((await s.execute(text(
        "SELECT agent_id, count(*) FROM sre.memory_events WHERE ts >= :s GROUP BY 1"), {"s": since})).all())
    by_ns = (await s.execute(text(
        "SELECT COALESCE(namespace, '-'), count(*) FROM sre.memory_events WHERE ts >= :s AND agent_id = :a "
        "AND event_type NOT IN ('MEMORY_CHECKPOINT_CREATED','MEMORY_CHECKPOINT_LOADED') "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 10"), {"s": since, "a": agent_id})).all()
    return {
        "bucket": bucket,
        "growth": [{"t": _iso(b), "created": int(n)} for b, n in growth],
        "reads_writes": [{"t": _iso(b), "reads": int(r), "writes": int(w), "checkpoints": int(c)}
                         for b, r, w, c in rw],
        "distribution": [{"memory_type": k, "count": v} for k, v in dist.items()],
        "retrieval_latency": [{"t": _iso(b), "p50": round(float(p50), 2) if p50 is not None else None,
                               "p95": round(float(p95), 2) if p95 is not None else None} for b, p50, p95, _ in rl],
        "context_tokens": [{"t": _iso(b), "tokens": int(tk or 0)} for b, _, _, tk in rl],
        "validation": [{"result": k, "count": int(v)} for k, v in val.items()],
        "by_agent": [{"agent_id": k, "count": int(v)} for k, v in by_agent.items()],
        "by_namespace": [{"namespace": k, "count": int(v)} for k, v in by_ns],
    }


# ------------------------------------------------------------------------------------------------ threads
async def threads(s: AsyncSession, *, user, admin: bool, q: str | None, kind: str | None, page: int, size: int
                  ) -> dict:
    """Checkpoint'i olan thread'ler (sohbet = konuşma thread'i; analysis = investigation run_id)."""
    params: dict[str, Any] = {"lim": size, "off": (page - 1) * size}
    where = []
    if q:
        where.append("c.thread_id ILIKE :q")
        params["q"] = f"%{q}%"
    vis = "" if admin else " AND (cv.user_id IS NULL OR cv.user_id = :uid)"
    params["uid"] = getattr(user, "id", None) if not admin else None
    kind_sql = {"chat": " AND cv.id IS NOT NULL", "analysis": " AND inv.id IS NOT NULL"}.get(kind or "", "")
    sql = f"""
      WITH t AS (SELECT thread_id, count(*) n, min((checkpoint->>'ts')::timestamptz) first_ts,
                        max((checkpoint->>'ts')::timestamptz) last_ts
                 FROM langgraph.checkpoints c {"WHERE " + " AND ".join(where) if where else ""}
                 GROUP BY thread_id)
      SELECT t.thread_id, t.n, t.first_ts, t.last_ts, cv.id conv_id, cv.user_id, cv.title conv_title,
             inv.id inv_id, inv.trigger::text inv_trigger, inv.status::text inv_status,
             (SELECT count(*) FROM sre.messages m WHERE m.conversation_id = cv.id) msg_count,
             (SELECT COALESCE(sum(length(m.content)), 0) FROM sre.messages m WHERE m.conversation_id = cv.id) chars
      FROM t LEFT JOIN sre.conversations cv ON cv.thread_id = t.thread_id
             LEFT JOIN sre.investigations inv ON inv.run_id = t.thread_id
      WHERE true {vis}{kind_sql}
    """
    total = int(await s.scalar(text(f"SELECT count(*) FROM ({sql}) x"),
                               {k: v for k, v in params.items() if k not in ("lim", "off")}) or 0)
    rows = (await s.execute(text(sql + " ORDER BY t.last_ts DESC NULLS LAST LIMIT :lim OFFSET :off"),
                            params)).mappings().all()
    items = []
    for r in rows:
        ex = "chat" if r["conv_id"] else ("analysis" if r["inv_id"] else "unknown")
        status = "idle"
        if r["inv_status"] in ("pending", "running"):
            status = "active"
        elif ex == "unknown":
            status = "orphan"
        items.append({"thread_id": r["thread_id"], "agent_id": EMBEDDED_AGENT,
                      "user_id": str(r["user_id"]) if r["user_id"] else None,
                      "created_at": _iso(r["first_ts"]), "last_activity": _iso(r["last_ts"]),
                      "message_count": int(r["msg_count"] or 0) if ex == "chat" else None,
                      "checkpoint_count": int(r["n"]),
                      "estimated_tokens": (int(r["chars"] or 0) + 3) // 4 if ex == "chat" else None,
                      "execution_type": ex if ex != "analysis" else f"analysis:{r['inv_trigger']}",
                      "status": status, "title": r["conv_title"],
                      "conversation_id": str(r["conv_id"]) if r["conv_id"] else None,
                      "investigation_id": str(r["inv_id"]) if r["inv_id"] else None})
    return {"items": items, "total": total, "page": page, "size": size, "pages": max(1, -(-total // size))}


async def _owner_check(s: AsyncSession, thread_id: str, user, admin: bool):
    conv = (await s.execute(select(Conversation).where(Conversation.thread_id == thread_id))).scalar_one_or_none()
    inv = (await s.execute(select(Investigation).where(Investigation.run_id == thread_id))).scalar_one_or_none()
    if conv is not None and not admin and conv.user_id != getattr(user, "id", None):
        return None, None, False
    return conv, inv, True


async def thread_detail(rt, s: AsyncSession, thread_id: str, *, user, admin: bool, runs=None) -> dict | None:
    from ..agent.chat import MAX_STATE_MESSAGES
    from ..agent import prompts
    conv, inv, ok = await _owner_check(s, thread_id, user, admin)
    if not ok:
        return None
    n = await s.scalar(text("SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = :t"), {"t": thread_id})
    if not n and conv is None and inv is None:
        return None
    state: dict = {}
    nxt: list = []
    with events.suppressed():
        tup = await rt.asaver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    if tup is not None:
        state = (tup.checkpoint or {}).get("channel_values") or {}
    out: dict[str, Any] = {"thread_id": thread_id, "agent_id": EMBEDDED_AGENT, "checkpoint_count": int(n or 0),
                           "execution_type": "chat" if conv else "analysis" if inv else "unknown"}
    if conv is not None:
        msgs = list((await s.execute(select(Message).where(Message.conversation_id == conv.id)
                                     .order_by(Message.created_at))).scalars())
        in_state = state.get("messages") or []
        state_set = {(m.get("role"), (m.get("content") or "")[:200]) for m in in_state if isinstance(m, dict)}
        links = dict((await s.execute(select(TraceLink.platform_run_id, TraceLink.langsmith_run_id).where(
            TraceLink.conversation_id == conv.id))).all())
        from ..services.langsmith_trace import run_url
        out_msgs = []
        for m in msgs:
            role = m.role.value
            key = (role, (m.content or "")[:200])
            mid = str(m.id)
            ls = links.get(mid)
            out_msgs.append({
                "message_id": mid, "role": role, "type": {"user": "human", "assistant": "ai"}.get(role, role),
                "created_at": _iso(m.created_at), "content": safe_copy(m.content)[0],
                "token_estimate": estimate_tokens(m.content or ""),
                "context_status": "in_state_window" if key in state_set else "trimmed (state penceresi dışında)",
                "investigation_id": str(m.investigation_id) if m.investigation_id else None,
                "agent_trace_run": f"chat-{mid}" if role == "user" else None,
                "langsmith_url": await run_url(rt, ls) if ls else None,
                "memory_counts": (m.meta or {}).get("memory_counts")})
        tools = (await s.execute(text(
            "SELECT t.id, t.tool_name, t.mcp_method, t.execution_status::text, t.outcome, t.record_count, "
            "t.duration_ms, t.started_at, t.investigation_id FROM sre.agent_tool_executions t "
            "JOIN sre.messages m ON m.investigation_id = t.investigation_id WHERE m.conversation_id = :c "
            "ORDER BY t.started_at LIMIT 300"), {"c": conv.id})).mappings().all()
        system = [{"name": n_, "length": len(getattr(prompts, n_, "") or ""),
                   "content": getattr(prompts, n_) if admin else None}
                  for n_ in ("ANALYSIS_SYSTEM", "HISTORY_SYSTEM", "FOLLOWUP_SYSTEM") if hasattr(prompts, n_)]
        st_tokens = estimate_tokens(in_state)
        out.update({
            "conversation_id": str(conv.id), "title": conv.title, "user_id": str(conv.user_id),
            "messages": out_msgs, "message_count": len(out_msgs),
            "system_messages": system,
            "system_messages_note": None if admin else "Sistem prompt metni yalnız admin'e gösterilir",
            "tool_calls": [{"id": str(t["id"]), "tool": t["tool_name"], "method": t["mcp_method"],
                            "status": t["execution_status"], "outcome": t["outcome"],
                            "record_count": t["record_count"], "duration_ms": t["duration_ms"],
                            "started_at": _iso(t["started_at"]),
                            "investigation_id": str(t["investigation_id"])} for t in tools],
            "tool_calls_note": "Sohbet graph'ı LLM tool calling kullanmaz; araç çağrıları investigation'ların MCP "
                               "çağrılarıdır (ayrıntı: MCP Çağrıları sayfası).",
            "conversation_summary": list(reversed(state.get("digests") or [])),
            "current_context": safe_copy({"focus": state.get("focus"),
                                          "last_investigation_id": state.get("last_investigation_id"),
                                          "messages_in_state": len(in_state)})[0],
            "context_window": {"messages_in_state": len(in_state), "max_messages": MAX_STATE_MESSAGES,
                               "token_estimate": st_tokens, "token_estimate_method": "characters / 4",
                               "trimming": f"state'te son {MAX_STATE_MESSAGES} mesaj tutulur; daha eskiler "
                                           "digest (tur özeti) olarak hatırlanır, LLM çağrısı yapılmaz"},
        })
    elif inv is not None:
        out.update({"investigation_id": str(inv.id), "title": inv.title, "trigger": inv.trigger.value,
                    "status": inv.status.value, "messages": [],
                    "current_context": safe_copy({k: state.get(k) for k in ("run_id", "status", "iterations",
                                                                            "windows")})[0],
                    "note": "Analiz thread'i mesaj içermez; state ayrıntısı Checkpoints ve Agent Trace'te."})
    active = inv is not None and inv.status in (InvestigationStatus.PENDING, InvestigationStatus.RUNNING)
    if conv is not None and runs is not None:
        active = active or bool(runs.active_for_conversation(conv.id))
    out["active"] = active
    out["pending_next"] = nxt
    return out


# ------------------------------------------------------------------------------------------------ checkpoints
async def _graph_for(rt, s: AsyncSession, thread_id: str):
    conv = (await s.execute(select(Conversation.id).where(Conversation.thread_id == thread_id))).scalar_one_or_none()
    if conv is not None:
        from ..agent.chat import ChatOrchestrator, TurnContext

        async def _noop(*a, **k):
            return None
        g = ChatOrchestrator(rt)._graph(TurnContext(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
                                                    thread_id=thread_id, emit=_noop))
        return g, "chat-turn", False
    inv = (await s.execute(select(Investigation.id).where(Investigation.run_id == thread_id))).scalar_one_or_none()
    if inv is not None:
        from airlab_agent.graph import build_graph
        g = build_graph(rt.agent, rt.gateway_factory, llm=None, checkpointer=rt.saver, persist_files=False)
        return g, "airlab-sre-analysis", True
    return None, None, False


def _snap_row(sn) -> dict:
    cfgc = (sn.config or {}).get("configurable", {})
    par = (sn.parent_config or {}).get("configurable", {}) if sn.parent_config else {}
    md = sn.metadata or {}
    return {"checkpoint_id": cfgc.get("checkpoint_id"), "checkpoint_ns": cfgc.get("checkpoint_ns") or "",
            "parent_checkpoint_id": par.get("checkpoint_id"), "step": md.get("step"), "source": md.get("source"),
            "created_at": sn.created_at, "next": list(sn.next or []),
            "tasks": [{"name": t.name, "id": t.id, "error": bool(getattr(t, "error", None)),
                       "interrupts": len(getattr(t, "interrupts", None) or ())} for t in (sn.tasks or ())],
            "metadata": safe_copy({k: v for k, v in md.items() if k not in ("writes",)})[0],
            "writes": sorted((md.get("writes") or {}).keys()) if isinstance(md.get("writes"), dict) else None}


async def checkpoints(rt, s: AsyncSession, thread_id: str, *, user, admin: bool, limit: int = 100) -> dict | None:
    conv, inv, ok = await _owner_check(s, thread_id, user, admin)
    if not ok:
        return None
    g, gname, sync = await _graph_for(rt, s, thread_id)
    cfg = {"configurable": {"thread_id": thread_id}}
    rows: list[dict] = []
    with events.suppressed():
        if g is None:
            async for t in rt.asaver.alist(cfg, limit=limit):
                c = t.config.get("configurable", {})
                p = (t.parent_config or {}).get("configurable", {})
                rows.append({"checkpoint_id": c.get("checkpoint_id"), "checkpoint_ns": c.get("checkpoint_ns") or "",
                             "parent_checkpoint_id": p.get("checkpoint_id"), "step": (t.metadata or {}).get("step"),
                             "source": (t.metadata or {}).get("source"), "created_at": t.checkpoint.get("ts"),
                             "next": None, "tasks": [], "metadata": safe_copy(t.metadata or {})[0],
                             "writes": None})
        elif sync:
            def _hist():
                return [_snap_row(sn) for sn in g.get_state_history(cfg, limit=limit)]
            token = events.SUPPRESS.set(True)
            try:
                rows = await asyncio.to_thread(_hist)
            finally:
                events.SUPPRESS.reset(token)
        else:
            async for sn in g.aget_state_history(cfg, limit=limit):
                rows.append(_snap_row(sn))
    if not rows and conv is None and inv is None:
        return None
    backend = type(rt.saver if sync else rt.asaver).__mro__[1].__name__
    return {"thread_id": thread_id, "graph": gname, "storage_backend": backend, "items": rows,
            "next_nodes_note": None if g is not None else "graph bilinmiyor (sahipsiz thread): next gözlemlenemedi",
            "read_only": True,
            "replay": "sunulmuyor — replay / fork / resume dış sistemlerde (MCP, Slack) yan etki oluşturabilir"}


async def _values_at(rt, thread_id: str, checkpoint_id: str, ns: str = "") -> tuple[Any, Any]:
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}}
    with events.suppressed():
        tup = await rt.asaver.aget_tuple(cfg)
    return tup, cfg


async def checkpoint_detail(rt, s: AsyncSession, thread_id: str, checkpoint_id: str, *, user, admin: bool,
                            ns: str = "") -> dict | None:
    conv, inv, ok = await _owner_check(s, thread_id, user, admin)
    if not ok:
        return None
    tup, cfg = await _values_at(rt, thread_id, checkpoint_id, ns)
    if tup is None:
        return None
    cp = tup.checkpoint or {}
    values = cp.get("channel_values") or {}
    parent_id = ((tup.parent_config or {}).get("configurable") or {}).get("checkpoint_id")
    prev_values = {}
    if parent_id:
        pt, _ = await _values_at(rt, thread_id, parent_id, ns)
        prev_values = ((pt.checkpoint or {}).get("channel_values") or {}) if pt else {}
    nxt_id = await s.scalar(text(
        "SELECT checkpoint_id FROM langgraph.checkpoints WHERE thread_id = :t AND checkpoint_ns = :ns "
        "AND parent_checkpoint_id = :c ORDER BY checkpoint_id LIMIT 1"), {"t": thread_id, "ns": ns, "c": checkpoint_id})
    children = [r[0] for r in (await s.execute(text(
        "SELECT checkpoint_id FROM langgraph.checkpoints WHERE thread_id = :t AND checkpoint_ns = :ns "
        "AND parent_checkpoint_id = :c"), {"t": thread_id, "ns": ns, "c": checkpoint_id})).all()]
    sub_ns = [r[0] for r in (await s.execute(text(
        "SELECT DISTINCT checkpoint_ns FROM langgraph.checkpoints WHERE thread_id = :t AND checkpoint_ns <> ''"),
        {"t": thread_id})).all()]
    g, gname, sync = await _graph_for(rt, s, thread_id)
    nxt: list | None = None
    if g is not None:
        try:
            with events.suppressed():
                if sync:
                    token = events.SUPPRESS.set(True)
                    try:
                        sn = await asyncio.to_thread(g.get_state, cfg)
                    finally:
                        events.SUPPRESS.reset(token)
                else:
                    sn = await g.aget_state(cfg)
            nxt = list(sn.next or [])
        except Exception:  # noqa: BLE001
            nxt = None
    v_safe, trunc, size = safe_copy(values)
    p_safe = safe_copy(prev_values)[0]
    diff = state_diff(p_safe, v_safe) if parent_id else {"available": False, "reason": "ilk checkpoint"}
    md = tup.metadata or {}
    return {"thread_id": thread_id, "checkpoint_id": checkpoint_id, "checkpoint_ns": ns,
            "parent_checkpoint_id": parent_id, "next_checkpoint_id": nxt_id, "children": children,
            "subgraph_namespaces": sub_ns, "step": md.get("step"), "source": md.get("source"),
            "created_at": cp.get("ts"), "metadata": safe_copy(md)[0], "channel_values": v_safe,
            "channel_versions": cp.get("channel_versions"), "versions_seen": safe_copy(cp.get("versions_seen"))[0],
            "pending_writes": [{"task_id": w[0], "channel": w[1], "value": safe_copy(w[2])[0]}
                               for w in (tup.pending_writes or [])][:200],
            "next_nodes": nxt, "graph": gname, "truncated": trunc, "size_bytes": size, "diff": diff,
            "storage_backend": type(rt.saver if sync else rt.asaver).__mro__[1].__name__, "read_only": True}


async def checkpoint_compare(rt, s: AsyncSession, thread_id: str, a: str, b: str, *, user, admin: bool) -> dict | None:
    conv, inv, ok = await _owner_check(s, thread_id, user, admin)
    if not ok:
        return None
    ta, _ = await _values_at(rt, thread_id, a)
    tb, _ = await _values_at(rt, thread_id, b)
    if ta is None or tb is None:
        return None
    va = safe_copy((ta.checkpoint or {}).get("channel_values") or {})[0]
    vb = safe_copy((tb.checkpoint or {}).get("channel_values") or {})[0]
    return {"thread_id": thread_id, "a": a, "b": b, "diff": state_diff(va, vb)}


# ------------------------------------------------------------------------------------------------ working memory
async def working_runs(s: AsyncSession, *, user, admin: bool, limit: int = 30) -> list[dict]:
    from ..services.agent_trace import effective_status
    q = select(AgentTraceRun).order_by(AgentTraceRun.started_at.desc()).limit(limit)
    if not admin:
        from sqlalchemy import or_
        q = q.where(or_(AgentTraceRun.kind != "chat_turn", AgentTraceRun.user_id == getattr(user, "id", None)))
    rows = list((await s.execute(q)).scalars())
    rows.sort(key=lambda r: (effective_status(r) != "running", -r.started_at.timestamp()))
    return [{"run_id": r.run_id, "kind": r.kind, "status": effective_status(r), "thread_id": r.thread_id,
             "started_at": _iso(r.started_at), "agent_id": EMBEDDED_AGENT} for r in rows]


SRE_FIELDS = {"analysis_time_window": ("windows",), "collected_evidence": ("evidence", "extra", "results"),
              "risk_score": ("scores", "risk"), "root_cause_candidates": ("mechanisms", "alternatives", "attribution"),
              "tool_results": ("results",)}


async def working_memory(s: AsyncSession, run_id: str, *, user, admin: bool) -> dict | None:
    from ..services.agent_trace import effective_status
    run = (await s.execute(select(AgentTraceRun).where(AgentTraceRun.run_id == run_id))).scalar_one_or_none()
    if run is None or (run.kind == "chat_turn" and not admin and run.user_id != getattr(user, "id", None)):
        return None
    evs = list((await s.execute(select(AgentTraceEvent).where(AgentTraceEvent.trace_run_id == run.id,
                                                              AgentTraceEvent.event_type.in_(
                                                                  ["node_start", "node_end", "state_out"]))
                                .order_by(AgentTraceEvent.sequence))).scalars())
    nodes: dict[str, dict] = {}
    last_state, last_next, cur = None, [], None
    for e in evs:
        if e.event_type == "node_start":
            nodes[e.node_exec_id] = {"node": e.node, "node_exec_id": e.node_exec_id, "step": e.step,
                                     "attempt": e.attempt, "started_at": _iso(e.ts), "status": "running"}
            cur = e.node
        elif e.event_type == "node_end" and e.node_exec_id in nodes:
            n = nodes[e.node_exec_id]
            n.update(status=e.status or "ok", duration_ms=e.duration_ms, ended_at=_iso(e.ts))
            upd = (e.payload or {}).get("node_update")
            if isinstance(upd, dict):
                n["update_keys"] = sorted(upd)
            n["has_error"] = bool((e.payload or {}).get("error"))
        elif e.event_type == "state_out":
            last_state = (e.payload or {}).get("values")
            last_next = (e.payload or {}).get("next") or []
    st = last_state if isinstance(last_state, dict) else {}
    running = [n for n in nodes.values() if n["status"] == "running"]
    return {"run_id": run.run_id, "thread_id": run.thread_id, "agent_id": EMBEDDED_AGENT, "kind": run.kind,
            "execution_status": effective_status(run), "current_node": running[-1]["node"] if running else cur,
            "current_iteration": st.get("iterations"),
            "fields": {k: {f: st.get(f) for f in fs if f in st} for k, fs in SRE_FIELDS.items()},
            "pending_tasks": last_next, "completed_tasks": [n["node"] for n in nodes.values()
                                                            if n["status"] not in ("running",)],
            "current_state": st, "nodes": list(nodes.values()),
            "source": "Agent Trace olayları (State Out = son graph adımı); kopya yok",
            "trace_link": f"/agent-trace/{run.run_id}", "read_only": True}


# ------------------------------------------------------------------------------------------------ olaylar / retrieval
def event_out(e: MemoryEvent, full: bool = False) -> dict:
    d = {"event_id": str(e.id), "event_type": e.event_type, "ts": _iso(e.ts), "agent_id": e.agent_id,
         "tenant_id": e.tenant_id, "run_id": e.run_id, "thread_id": e.thread_id, "trace_id": e.trace_id,
         "node": e.node, "memory_id": e.memory_id, "memory_type": e.memory_type, "namespace": e.namespace,
         "status": e.status, "duration_ms": e.duration_ms, "error": e.error, "correlation_id": e.correlation_id,
         "retrieval_id": str(e.retrieval_id) if e.retrieval_id else None, "actor": e.actor}
    if full:
        d["payload"] = e.payload
    return d


def _visible_threads_cond(user, admin: bool):
    """Sohbet thread'lerine ait olaylar yalnız sahibine / admin'e."""
    if admin:
        return text("true")
    return text("(thread_id IS NULL OR thread_id NOT IN (SELECT thread_id FROM sre.conversations "
                "WHERE user_id <> :vuid))").bindparams(vuid=getattr(user, "id", None))


async def list_events(s: AsyncSession, *, agent_id: str, user, admin: bool, types: list[str] | None,
                      run_id: str | None, thread_id: str | None, memory_id: str | None, status: str | None,
                      since: datetime | None, after_ts: datetime | None, page: int, size: int) -> dict:
    q = select(MemoryEvent).where(MemoryEvent.agent_id == agent_id, _visible_threads_cond(user, admin))
    if types:
        q = q.where(MemoryEvent.event_type.in_(types))
    if run_id:
        q = q.where(MemoryEvent.run_id == run_id)
    if thread_id:
        q = q.where(MemoryEvent.thread_id == thread_id)
    if memory_id:
        q = q.where(MemoryEvent.memory_id == memory_id)
    if status:
        q = q.where(MemoryEvent.status == status)
    if since:
        q = q.where(MemoryEvent.ts >= since)
    if after_ts:
        q = q.where(MemoryEvent.ts > after_ts)
    total = int(await s.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = (await s.execute(q.order_by(MemoryEvent.ts.desc()).offset((page - 1) * size).limit(size))).scalars()
    return {"items": [event_out(e) for e in rows], "total": total, "page": page, "size": size,
            "pages": max(1, -(-total // size)), "event_types": list(events.EVENT_TYPES),
            "not_emitted": events.NOT_EMITTED}


async def get_event(s: AsyncSession, eid: str, *, user, admin: bool) -> dict | None:
    try:
        e = await s.get(MemoryEvent, uuid.UUID(eid))
    except ValueError:
        return None
    if e is None:
        return None
    if not admin and e.thread_id:
        owner = await s.scalar(select(Conversation.user_id).where(Conversation.thread_id == e.thread_id))
        if owner is not None and owner != getattr(user, "id", None):
            return None
    return event_out(e, full=True)


def retrieval_out(r: MemoryRetrieval, full: bool = False) -> dict:
    inj = r.injection or {}
    d = {"retrieval_id": str(r.id), "ts": _iso(r.ts), "agent_id": r.agent_id, "origin": r.origin,
         "run_id": r.run_id, "thread_id": r.thread_id, "trace_id": r.trace_id, "node": r.node,
         "strategy": r.strategy, "namespaces": r.namespaces, "memory_types": r.memory_types, "top_k": r.top_k,
         "returned_count": r.returned_count, "empty": r.empty, "duration_ms": r.duration_ms, "status": r.status,
         "error": r.error, "injection_status": inj.get("status"), "injected_tokens": inj.get("token_estimate")}
    if full:
        d.update({"query": r.query, "filters": r.filters, "selected": r.selected, "excluded": r.excluded,
                  "stages": r.stages, "injection": r.injection,
                  "query_embedding": {"status": "not_applicable",
                                      "note": "embedding kullanılmıyor (fts + parmak izi)"}})
    return d


async def list_retrievals(s: AsyncSession, *, agent_id: str, user, admin: bool, run_id: str | None,
                          origin: str | None, since: datetime | None, page: int, size: int) -> dict:
    q = select(MemoryRetrieval).where(MemoryRetrieval.agent_id == agent_id)
    if not admin:
        q = q.where(_visible_threads_cond(user, admin))
    if run_id:
        q = q.where(MemoryRetrieval.run_id == run_id)
    if origin:
        q = q.where(MemoryRetrieval.origin == origin)
    if since:
        q = q.where(MemoryRetrieval.ts >= since)
    total = int(await s.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = (await s.execute(q.order_by(MemoryRetrieval.ts.desc()).offset((page - 1) * size).limit(size))).scalars()
    return {"items": [retrieval_out(r) for r in rows], "total": total, "page": page, "size": size,
            "pages": max(1, -(-total // size))}


async def get_retrieval(s: AsyncSession, rid: str, *, user, admin: bool) -> dict | None:
    try:
        r = await s.get(MemoryRetrieval, uuid.UUID(rid))
    except ValueError:
        return None
    if r is None:
        return None
    if not admin and r.thread_id:
        owner = await s.scalar(select(Conversation.user_id).where(Conversation.thread_id == r.thread_id))
        if owner is not None and owner != getattr(user, "id", None):
            return None
    out = retrieval_out(r, full=True)
    evs = (await s.execute(select(MemoryEvent).where(MemoryEvent.retrieval_id == r.id)
                           .order_by(MemoryEvent.ts))).scalars()
    out["events"] = [event_out(e) for e in evs]
    return out


def is_admin(user) -> bool:
    return getattr(user, "role", None) == Role.ADMIN
