"""Araştırma yürütücüsü: mevcut airlab_agent LangGraph analizini (değiştirmeden) bir thread'de çalıştırır,
MCP sorgu olaylarını ve düğüm ilerlemesini asyncio kuyruğuna köprüler; her araç çağrısını
`agent_tool_executions` tablosuna yazar ve SSE olayı olarak yayar. Sonuç raporu araştırma kaydına işlenir.

Sınırlar (Settings > Agent; koşu başında dondurulur, investigations.config_snapshot'a yazılır): genel zaman aşımı,
sorgu bütçesi (max_queries_per_run), ek kanıt turu sınırı (max_iterations), LangGraph recursion_limit.
İptal düğüm sınırlarında uygulanır.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from airlab_agent.graph import run_analysis
from airlab_agent.redact import redact_text

from ..db.models import Investigation, ToolExecution, ToolStatus
from ..db.session import transaction
from ..repositories import investigations
from .events import NODE_MESSAGE, SIGNAL_STEP

log = logging.getLogger("sre.investigation")
FAILED_STATUSES = {"ERROR", "PERMISSION", "UNAVAILABLE"}


class Cancelled(Exception):
    pass


class InvestigationTimeout(Exception):
    pass


class InvestigationRunner:
    def __init__(self, rt):
        self.rt = rt

    async def run(self, inv_id: uuid.UUID, *, emit, cancel: threading.Event | None = None,
                  question: str | None = None, mode: str = "chat", core_llm=None,
                  notes: list[str] | None = None, rc=None) -> tuple[Investigation, dict | None]:
        cancel = cancel or threading.Event()
        sm = self.rt.sm
        from ..settings import runtime as srt
        if rc is None:                                  # sohbet turu kendi yapılandırmasını verir (aynı trace kararı)
            async with transaction(sm) as s:
                trig = (await investigations.get(s, inv_id)).trigger
            # Koşu başına DEĞİŞMEZ yapılandırma: koşu sürerken yapılan ayar değişiklikleri bu koşuyu etkilemez.
            rc = await srt.run_config(self.rt, trigger=getattr(trig, "value", str(trig)))
            srt.apply_llm(self.rt, rc)                  # görev profilleri + arayüzde seçilen model
        notes = list(notes or []) + rc.notes
        if core_llm is not None and not rc.llm_rca:
            core_llm = None
            if not rc.effective["agent"]["llm_enabled"]:
                notes.append("LLM ayarlardan kapalı; deterministik açıklama")
        async with transaction(sm) as s:
            inv = await investigations.get(s, inv_id)
            await investigations.mark_running(s, inv)
            inv.config_snapshot = rc.snapshot
            run_id, start, end = inv.run_id, inv.window_start, inv.window_end
            trace_meta = {"investigation_id": str(inv_id), "trigger": getattr(inv.trigger, "value", str(inv.trigger)),
                          "conversation_id": str(inv.conversation_id) if inv.conversation_id else None,
                          "application_id": inv.application_id}
            trace_tags = [f"trigger:{trace_meta['trigger']}"]
            baseline = (inv.baseline_start, inv.baseline_end) if inv.baseline_start else None
        await emit("investigation_started", {"investigation_id": str(inv_id), "run_id": run_id,
                                             "window": [start.isoformat(), end.isoformat()],
                                             "baseline": [b.isoformat() for b in baseline] if baseline else None})
        await emit("agent_thinking", {"step": "start", "message": "Dynatrace MCP üzerinden uygulama, PostgreSQL, log, "
                                                                  "trace ve host signal'leri için query çalıştırılıyor."})
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def put(item):
            loop.call_soon_threadsafe(q.put_nowait, item)

        def on_tool(ev: dict):
            put(("tool", ev))

        def on_node(name: str, update: dict):
            put(("node", name, {"status": update.get("status"), "iterations": update.get("iterations")}))
            if cancel.is_set():
                raise Cancelled()

        agent = rc.agent
        use_llm = core_llm is not None
        gw_factory = rc.gateway_factory
        callbacks = (self.rt.callbacks or getattr(core_llm, "callbacks", None)) if rc.trace else []
        trace_meta["config_hash"] = rc.snapshot["hash"]
        kw = dict(llm=core_llm, question=question, mode=mode, notes=notes, run_id=run_id, use_llm=use_llm,
                  baseline=baseline, checkpointer=self.rt.saver, persist_files=False, on_node=on_node,
                  callbacks=callbacks, recursion_limit=rc.recursion_limit, trace_run_id=inv_id,
                  trace_metadata=trace_meta, trace_tags=trace_tags)
        args = (agent, lambda: gw_factory(on_event=on_tool), start, end)
        if rc.trace:
            fut = asyncio.ensure_future(asyncio.to_thread(run_analysis, *args, **kw))
        else:                                       # sampling dışı / kapalı: LLM çağrıları da trace göndermez
            from airlab_agent.tracing import without_tracing
            fut = asyncio.ensure_future(asyncio.to_thread(without_tracing, run_analysis, *args, **kw))
        rows: dict[str, uuid.UUID] = {}
        node_t = [time.monotonic()]
        deadline = loop.time() + rc.timeout_s
        timed_out = False
        while True:
            if fut.done() and q.empty():
                break
            try:
                item = await asyncio.wait_for(q.get(), 0.25)
            except TimeoutError:
                if loop.time() > deadline and not fut.done():
                    cancel.set()
                    timed_out = True
                    break
                continue
            try:
                await self._handle(item, inv_id, rows, node_t, emit)
            except Exception:  # noqa: BLE001 — olay işleme hatası analizi durdurmaz
                log.exception("olay işlenemedi")

        error, cancelled, state = None, False, None
        if timed_out:
            error = f"investigation timeout ({rc.timeout_s} sn)"
            fut.add_done_callback(lambda f: f.exception())          # arka plandaki thread'in sonucu yok sayılır
        else:
            try:
                state = fut.result()
            except Cancelled:
                cancelled, error = True, "kullanıcı tarafından iptal edildi"
            except Exception as e:  # noqa: BLE001
                error = redact_text(f"{type(e).__name__}: {e}")
                log.exception("analiz başarısız")
        # açık kalan araç kayıtlarını kapat
        if rows:
            async with transaction(sm) as s:
                for rid in rows.values():
                    t = await s.get(ToolExecution, rid)
                    if t and t.execution_status == ToolStatus.STARTED:
                        await investigations.tool_finished(s, t, ok=False, error=error or "sonuç alınamadı")
        report = (state or {}).get("report")
        async with transaction(sm) as s:
            inv = await investigations.get(s, inv_id)
            if report and not error:
                await investigations.complete(s, inv, report)
            else:
                await investigations.fail(s, inv, error or "rapor üretilmedi", cancelled=cancelled)
        if report and not error and not cancel.is_set():
            try:
                await self._correlate(inv_id, start, end, emit, rc)
            except Exception:  # noqa: BLE001 — ek kanıt toplanamazsa analiz sonucu geçerli kalır
                log.exception("evidence correlation başarısız")
        if report and not error and rc.trace:
            from ..services.langsmith_trace import record_outcome
            await record_outcome(self.rt, inv_id)
        # Sohbette RCA metni bu adımdan sonra yazılır; bildirim orada (chat.respond) gönderilir.
        if report and not error and mode != "chat":
            from ..services.slack_notify import notify_rca
            await notify_rca(self.rt, inv_id)
        if error:
            await emit("error", {"code": "cancelled" if cancelled else "timeout" if timed_out else "analysis_failed",
                                 "message": error, "investigation_id": str(inv_id)})
        return inv, report

    async def _correlate(self, inv_id: uuid.UUID, start, end, emit, rc=None) -> None:
        """Yavaş trace kimlikleri + Davis problemleri → ek kanıt (puanlamayı değiştirmez)."""
        from ..services import correlation
        await emit("agent_thinking", {"step": "traces", "message": "En yavaş isteklerin trace kimlikleri ve Davis "
                                                                   "problem'leri evidence'a ekleniyor."})
        events: list[dict] = []
        gw = (rc.gateway_factory if rc else self.rt.gateway_factory)(on_event=events.append)
        res = await asyncio.to_thread(gw.run_many, correlation.queries(start, end, rc.agent.cfg if rc else self.rt.agent.cfg))
        rows: dict[str, uuid.UUID] = {}
        for ev in events:
            await self._handle(("tool", ev), inv_id, rows, [time.monotonic()], emit)
        items = correlation.evidence(res, end)
        async with transaction(self.rt.sm) as s:
            await investigations.upsert_evidence(s, inv_id, items)
        for it in items:
            if it.get("data_status") == "OK":
                await emit("evidence_found", {"evidence_key": it["key"], "signal": it["signal"], "step": "traces",
                                              "records": 1, "trace_id": it.get("trace_id"),
                                              "dynatrace_query_id": it.get("dynatrace_query_id")})

    async def _handle(self, item: tuple, inv_id: uuid.UUID, rows: dict, node_t: list, emit) -> None:
        kind = item[0]
        if kind == "tool":
            ev = item[1]
            key = ev["key"]
            step = SIGNAL_STEP.get(key, "extra")
            if ev["phase"] == "start":
                async with transaction(self.rt.sm) as s:
                    t = await investigations.tool_started(s, inv_id, ev.get("tool", "execute-dql"), step=step,
                                                          signal=key, arguments={"dqlQueryString": ev["dql"]})
                    rows[key] = t.id
                await emit("tool_started", {"id": str(t.id), "tool": t.tool_name, "signal": key, "step": step})
                return
            status = ev.get("status")
            ok = status not in FAILED_STATUSES
            async with transaction(self.rt.sm) as s:
                if key in rows:
                    t = await s.get(ToolExecution, rows.pop(key))
                    await investigations.tool_finished(s, t, ok=ok, duration_ms=ev.get("elapsed_ms"),
                                                       result_status=status, record_count=ev.get("records"),
                                                       query_id=ev.get("query_id"), evidence_key=ev.get("evidence_id"),
                                                       error=ev.get("error"), skipped=status == "SKIPPED_BUDGET")
                else:
                    t = await investigations.record_tool(s, inv_id, "execute-dql", ok=ok, step=step, signal=key,
                                                         arguments={"dqlQueryString": ev["dql"]}, result_status=status,
                                                         error=ev.get("error"), skipped=status == "SKIPPED_BUDGET",
                                                         duration_ms=ev.get("elapsed_ms"))
            data = {"id": str(t.id), "tool": t.tool_name, "signal": key, "step": step, "status": status,
                    "records": ev.get("records"), "duration_ms": ev.get("elapsed_ms"),
                    "dynatrace_query_id": ev.get("query_id")}
            if ok:
                await emit("tool_completed", data)
                if status == "OK" and ev.get("records"):
                    await emit("evidence_found", {"evidence_key": ev.get("evidence_id"), "signal": key, "step": step,
                                                  "records": ev.get("records"), "dynatrace_query_id": ev.get("query_id")})
            else:
                await emit("tool_failed", {**data, "error": ev.get("error")})
            return
        if kind == "node":
            name, info = item[1], item[2]
            now = time.monotonic()
            dur = int((now - node_t[0]) * 1000)
            node_t[0] = now
            if name == "analyze":
                async with transaction(self.rt.sm) as s:
                    await investigations.record_tool(s, inv_id, "airlab:correlate", ok=True, step="correlate",
                                                     duration_ms=dur, result_status=info.get("status"))
            msg = NODE_MESSAGE.get(name)
            if msg:
                await emit("agent_thinking", {"step": {"analyze": "correlate", "extra_evidence": "extra",
                                                       "explain": "rca", "finalize": "rca"}.get(name, name),
                                              "node": name, "message": msg})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def summary_of(inv: Investigation, report: dict | None) -> dict[str, Any]:
    r = report or {}
    return {"investigation_id": str(inv.id), "run_id": inv.run_id, "status": inv.status.value,
            "severity": inv.severity.value, "risk_score": float(inv.risk_score) if inv.risk_score is not None else None,
            "data_coverage": float(inv.data_coverage) if inv.data_coverage is not None else None,
            "mechanism": inv.primary_mechanism, "confidence": inv.confidence.value if inv.confidence else None,
            "source_apps": inv.source_apps, "affected_apps": inv.affected_apps,
            "window": [inv.window_start.isoformat(), inv.window_end.isoformat()],
            "data_gaps": (r.get("data_gaps") or [])[:10], "impacted": list((r.get("impacted") or {}).keys()),
            "duration_ms": inv.duration_ms, "error": inv.error}
