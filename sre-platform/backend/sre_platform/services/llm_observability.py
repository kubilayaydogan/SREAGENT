"""LLM izleme panosu: sre.llm_runs üzerinden özet (LangSmith'e istek atmaz; veri worker senkronundan gelir)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from ..db.models import KeyValue
from .langsmith_sync import KEY

RANGES = {"24h": (timedelta(hours=24), "1 hour"), "7d": (timedelta(days=7), "6 hours"),
          "30d": (timedelta(days=30), "1 day")}

_LLM = "FROM sre.llm_runs WHERE kind = 'llm' AND start_time >= :start AND start_time < :end"
_P = ("percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms, "
      "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms")
_AGG = ("count(*) AS calls, count(*) FILTER (WHERE status = 'error') AS errors, "
        "coalesce(sum(input_tokens), 0) AS input_tokens, coalesce(sum(output_tokens), 0) AS output_tokens, "
        "coalesce(sum(cost_usd), 0) AS cost_usd, " + _P)

SQL = {
    "totals": f"SELECT {_AGG}, percentile_cont(0.95) WITHIN GROUP (ORDER BY first_token_ms) AS p95_first_token_ms {_LLM}",
    "by_model": f"SELECT coalesce(model, 'bilinmiyor') AS model, {_AGG} {_LLM} GROUP BY 1 ORDER BY cost_usd DESC",
    "by_call": f"SELECT name, {_AGG} {_LLM} GROUP BY 1 ORDER BY cost_usd DESC",
    "series": f"""SELECT date_bin(CAST(:bucket AS interval), start_time, TIMESTAMPTZ '2000-01-01') AS t, {_AGG}
                  {_LLM} GROUP BY 1 ORDER BY 1""",
    "llm_errors": f"""SELECT id, trace_id, name, model, error, start_time, latency_ms, investigation_id
                      {_LLM} AND status = 'error' ORDER BY start_time DESC LIMIT 10""",
    "traces": """WITH c AS (SELECT trace_id, sum(cost_usd) AS cost_usd,
                                   sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)) AS tokens,
                                   count(*) AS llm_calls, max(model) AS model
                            FROM sre.llm_runs WHERE kind = 'llm' AND start_time >= :start - interval '1 hour'
                            GROUP BY trace_id)
                 SELECT r.id, r.name, r.trigger, coalesce(r.model, c.model) AS model, r.status, r.error, r.start_time, r.latency_ms,
                        r.investigation_id, r.conversation_id,
                        coalesce(c.cost_usd, 0) AS cost_usd, coalesce(c.tokens, 0) AS tokens,
                        coalesce(c.llm_calls, 0) AS llm_calls
                 FROM sre.llm_runs r LEFT JOIN c ON c.trace_id = r.trace_id
                 WHERE (r.kind = 'trace' OR r.id = r.trace_id) AND r.start_time >= :start AND r.start_time < :end""",
}


def _num(v):
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() and abs(f) < 1e15 else round(f, 6)


def _val(v):
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return _num(v)


def _rows(res) -> list[dict[str, Any]]:
    return [{k: _val(v) for k, v in m.items()} for m in res.mappings().all()]


def _by_trigger(traces: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for t in traces:
        groups.setdefault(t["trigger"] or "diğer", []).append(t)
    out = []
    for k, items in groups.items():
        lat = sorted(t["latency_ms"] for t in items if t["latency_ms"] is not None)
        out.append({"trigger": k, "traces": len(items), "errors": sum(t["status"] == "error" for t in items),
                    "cost_usd": round(sum(t["cost_usd"] or 0 for t in items), 6),
                    "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None})
    return sorted(out, key=lambda x: -x["cost_usd"])


async def summary(s, range_: str, now: datetime | None = None) -> dict[str, Any]:
    span, bucket = RANGES[range_]
    end = now or datetime.now(timezone.utc)
    p = {"start": end - span, "end": end, "bucket": bucket}
    totals = _rows(await s.execute(text(SQL["totals"]), p))[0]
    traces = _rows(await s.execute(text(SQL["traces"]), p))
    kv = await s.get(KeyValue, KEY)
    state = dict(kv.value) if kv else {}
    return {
        "range": range_, "start": p["start"].isoformat(), "end": end.isoformat(), "bucket": bucket,
        "sync": {"last_ok_at": state.get("last_ok_at"), "last_error": state.get("last_error"),
                 "last_error_at": state.get("last_error_at"), "project": state.get("project")},
        "totals": {**totals, "traces": len(traces), "trace_errors": sum(t["status"] == "error" for t in traces),
                   "error_rate": (totals["errors"] / totals["calls"]) if totals["calls"] else None},
        "by_model": _rows(await s.execute(text(SQL["by_model"]), p)),
        "by_call": _rows(await s.execute(text(SQL["by_call"]), p)),
        "by_trigger": _by_trigger(traces),
        "series": _rows(await s.execute(text(SQL["series"]), p)),
        "top_cost": sorted(traces, key=lambda t: -(t["cost_usd"] or 0))[:10],
        "slowest": sorted(traces, key=lambda t: -(t["latency_ms"] or 0))[:10],
        "llm_errors": _rows(await s.execute(text(SQL["llm_errors"]), p)),
        "errors": sorted((t for t in traces if t["status"] == "error"), key=lambda t: t["start_time"], reverse=True)[:10],
    }
