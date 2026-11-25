"""LangSmith → sre.llm_runs senkronu (worker işi `langsmith-sync`).

- Kök trace'ler (kind='trace') ve LLM çağrıları (kind='llm') çekilir; prompt/cevap içeriği ÇEKİLMEZ.
- Kök trace'lerin token/maliyeti LangSmith sorgusunda gelmez; panoda aynı trace_id'li llm satırları toplanır.
- Genel adlı LLM run'ları (ChatAnthropic) en yakın anlamlı atasının adıyla etiketlenir (route_question …).
- İmleç `kv['langsmith_sync']`: her tur son görülen başlangıçtan OVERLAP geri başlar (LangSmith'e geç düşen ve
  hâlâ süren run'lar bir sonraki turda güncellenir; upsert idempotent). İlk turda backfill_days geriye gider.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from airlab_agent.redact import redact_text

from ..db.models import KeyValue, LlmRun
from ..db.session import transaction
from .langsmith_trace import tracer

log = logging.getLogger("sre_platform.langsmith_sync")
KEY = "langsmith_sync"
OVERLAP = timedelta(minutes=20)
MAX_ROWS = 20000
SELECTS = ["ID", "NAME", "RUN_TYPE", "STATUS", "START_TIME", "END_TIME", "LATENCY_SECONDS", "FIRST_TOKEN_TIME",
           "ERROR_PREVIEW", "METADATA", "PARENT_RUN_IDS", "TRACE_ID", "IS_ROOT", "PROMPT_TOKENS",
           "COMPLETION_TOKENS", "TOTAL_COST"]
_project_ids: dict[str, str] = {}
# Metadata'sında tetikleyici olmayan kök trace'ler için ada göre çıkarım. 2026-11-25 öncesi sohbet turları tek trace
# değildi (route_question / answer ayrı kökler); tek başına explain_findings platformun RCA raporu üretimidir.
ROOT_TRIGGER = {"chat-turn": "chat", "route_question": "chat", "answer": "chat", "airlab-chat": "chat",
                "explain_findings": "rca", "interpret_question": "ask"}


def _generic(name: str | None) -> bool:
    return not name or name.startswith(("ChatAnthropic", "Runnable", "ChannelWrite", "Branch"))


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


def _ms(a: datetime | None, b: datetime | None) -> int | None:
    return int((b - a).total_seconds() * 1000) if a and b else None


def to_row(r, kind: str, label: str | None = None) -> dict[str, Any]:
    md = getattr(r, "metadata", None) or {}
    status = (r.status or "pending").lower()
    lat = getattr(r, "latency_seconds", None)
    is_root = str(r.id) == str(r.trace_id) or bool(getattr(r, "is_root", False))
    trigger = md.get("trigger") or (ROOT_TRIGGER.get(r.name) if is_root else None) or md.get("mode")
    err = getattr(r, "error_preview", None)
    return {
        "id": _uuid(r.id), "trace_id": _uuid(r.trace_id) or _uuid(r.id), "kind": kind,
        "name": (label or r.name or "?")[:128],
        "model": (md.get("ls_model_name") or md.get("model") or None),
        "trigger": str(trigger)[:32] if trigger else None,
        "investigation_id": _uuid(md.get("investigation_id")), "conversation_id": _uuid(md.get("conversation_id")),
        "status": status if status in ("success", "error") else "pending",
        "error": redact_text(str(err))[:500] if err and status == "error" else None,
        "start_time": r.start_time,
        "latency_ms": int(lat * 1000) if lat is not None else _ms(r.start_time, getattr(r, "end_time", None)),
        "first_token_ms": _ms(r.start_time, getattr(r, "first_token_time", None)),
        "input_tokens": getattr(r, "prompt_tokens", None) if kind == "llm" else None,
        "output_tokens": getattr(r, "completion_tokens", None) if kind == "llm" else None,
        "cost_usd": getattr(r, "total_cost", None) if kind == "llm" else None,
    }


async def _query(client, project_id: str, *, limit: int = MAX_ROWS, **kw) -> list:
    out = []
    async for r in client.runs.query(project_ids=[project_id], selects=SELECTS, page_size=100, **kw):
        out.append(r)
        if len(out) >= limit:
            log.warning("LangSmith senkronu %d satır sınırına ulaştı", limit)
            break
    return out


async def _labels(client, project_id: str, runs: list, since: datetime) -> dict[str, str]:
    """Genel adlı LLM run'ları için en yakın anlamlı atanın adı."""
    need = {str(p) for r in runs if _generic(r.name) for p in (r.parent_run_ids or [])[-4:]}
    if not need:
        return {}
    # Zaman sınırı verilmezse LangSmith yalnız yakın tarihli run'larda arar; atalar en geç çocukla başlar.
    names = {str(a.id): a.name for a in await _query(client, project_id, ids=sorted(need), limit=len(need) + 10,
                                                      min_start_time=since - timedelta(hours=1))}
    out = {}
    for r in runs:
        if _generic(r.name):
            for p in reversed(r.parent_run_ids or []):
                n = names.get(str(p))
                if n and not _generic(n):
                    out[str(r.id)] = n
                    break
    return out


async def _upsert(s, rows: list[dict]) -> None:
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        stmt = insert(LlmRun).values(chunk)
        cols = {c: stmt.excluded[c] for c in chunk[0] if c != "id"}
        await s.execute(stmt.on_conflict_do_update(index_elements=[LlmRun.id], set_={**cols, "synced_at": datetime.now(timezone.utc)}))


async def _state(rt) -> dict:
    async with transaction(rt.sm) as s:
        kv = await s.get(KeyValue, KEY)
        return dict(kv.value) if kv else {}


async def _save_state(rt, value: dict) -> None:
    async with transaction(rt.sm) as s:
        stmt = insert(KeyValue).values(key=KEY, value=value)
        await s.execute(stmt.on_conflict_do_update(index_elements=[KeyValue.key],
                                                   set_={"value": stmt.excluded.value, "updated_at": datetime.now(timezone.utc)}))


async def sync(rt, params: dict | None = None) -> dict[str, Any]:
    """Tek senkron turu. Hata olursa imleç ilerlemez ve hata kv'ye yazılır (pano son durumu gösterir)."""
    t = tracer(rt)
    if t is None:
        return {"skipped": "LangSmith tracing kapalı"}
    params = params or {}
    now = datetime.now(timezone.utc)
    state = await _state(rt)
    floor = now - timedelta(days=int(params.get("backfill_days", rt.ps.langsmith_backfill_days)))
    since = max(datetime.fromisoformat(state["since"]), floor) if state.get("since") else floor
    try:
        pid = _project_ids.get(t.project_name)
        if pid is None:
            pid = str((await asyncio.to_thread(t.client.read_project, project_name=t.project_name)).id)
            _project_ids[t.project_name] = pid
        roots = await _query(t.client, pid, is_root=True, min_start_time=since)
        llms = await _query(t.client, pid, run_type="llm", min_start_time=since)
        labels = await _labels(t.client, pid, llms, since)
        # Tek başına çalışan LLM çağrısı (ör. RCA raporu) hem kök hem llm döner: tek satır, kind='llm'
        # (panoda id = trace_id olan llm satırı kök trace sayılır). Sayfalama tekrarları da burada elenir.
        uniq: dict = {}
        for row in [to_row(r, "trace") for r in roots] + [to_row(r, "llm", labels.get(str(r.id))) for r in llms]:
            if row["id"] and row["start_time"]:
                uniq[row["id"]] = row
        rows = list(uniq.values())
        async with transaction(rt.sm) as s:
            if rows:
                await _upsert(s, rows)
    except Exception as e:  # noqa: BLE001
        err = redact_text(f"{type(e).__name__}: {e}")[:300]
        await _save_state(rt, {**state, "last_error": err, "last_error_at": now.isoformat()})
        raise RuntimeError(err) from None
    starts = [r["start_time"] for r in rows]
    pending = [r["start_time"] for r in rows if r["status"] == "pending"]
    nxt = max(starts) - OVERLAP if starts else since
    if pending:
        nxt = min(nxt, min(pending))
    nxt = max(min(nxt, now - OVERLAP), since)                 # imleç geri gitmez
    result = {"traces": sum(r["kind"] == "trace" for r in rows), "llm_calls": sum(r["kind"] == "llm" for r in rows),
              "pending": len(pending), "since": since.isoformat()}
    await _save_state(rt, {"since": nxt.isoformat(), "last_ok_at": now.isoformat(), "last_result": result,
                           "last_error": None, "project": t.project_name})
    return result
