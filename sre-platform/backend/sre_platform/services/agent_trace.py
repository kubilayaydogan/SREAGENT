"""Agent Trace kalıcı kaydı: airlab_agent.agent_trace.TraceRecorder olaylarını sre.agent_trace_* tablolarına yazar.

* Olaylar kaydedicide MASKELENMİŞ ve boyutu sınırlanmış olarak üretilir; burada yalnız kuyruğa alınıp toplu yazılır.
* Kaydedici hem analiz thread'inden hem sohbet görevinden çağrılır → thread-safe kuyruk; bir asyncio görevi
  kuyruğu periyodik olarak boşaltır, `finish` kalan her şeyi yazar.
* Yazma hatası agent'ı durdurmaz: kayıp olay sayısı run satırına (`lost_events`) ve uygulama loguna (payload'sız)
  yazılır. Run satırı hiç açılamazsa kayıt yapılmaz (agent normal çalışır).
* `SRE_AGENT_TRACE=0` kaydı kapatır.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import uuid
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from airlab_agent.agent_trace import TraceRecorder

from ..db.models import AgentTraceEvent, AgentTraceRun
from ..db.session import transaction

log = logging.getLogger("sre.agent_trace")
FLUSH_S = 0.3
BATCH = 200


def enabled() -> bool:
    return os.getenv("SRE_AGENT_TRACE", "1").lower() not in ("0", "false", "no", "off")


class TraceSession:
    """Bir run'ın kaydedicisi + kalıcı yazıcısı."""

    def __init__(self, rt, row_id: uuid.UUID, run_id: str, graph: str):
        self.rt, self.row_id, self.run_id = rt, row_id, run_id
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.lost = 0
        self.recorder = TraceRecorder(run_id, self.q.put, graph=graph)
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._pump(), name=f"agent-trace-{run_id}")
        self._t0 = datetime.now(timezone.utc)

    async def _pump(self) -> None:
        """Periyodik boşaltma. İptal edilmez: `finish` durdurma sinyali verir ve sürmekte olan batch yazımını bekler
        (iptal, kuyruktan alınmış ama yazılmamış olayları sessizce kaybettirirdi)."""
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), FLUSH_S)
            except asyncio.TimeoutError:
                pass
            await self.flush()

    async def flush(self) -> None:
        while True:
            rows = []
            while len(rows) < BATCH:
                try:
                    ev = self.q.get_nowait()
                except queue.Empty:
                    break
                rows.append({"id": uuid.UUID(ev["event_id"]), "trace_run_id": self.row_id,
                             "sequence": ev["sequence"], "ts": datetime.fromisoformat(ev["timestamp"]),
                             "event_type": ev["event_type"], "node": ev["node"], "node_exec_id": ev["node_exec_id"],
                             "step": ev["step"], "attempt": ev["attempt"], "call_id": ev["call_id"],
                             "related_call_id": ev["related_call_id"], "status": ev["status"],
                             "duration_ms": ev["duration_ms"], "payload": ev["payload"], "truncated": ev["truncated"],
                             "size_bytes": ev["size_bytes"]})
            if not rows:
                return
            try:
                async with transaction(self.rt.sm) as s:
                    await s.execute(insert(AgentTraceEvent), rows)
            except asyncio.CancelledError:
                self.lost += len(rows)
                raise
            except Exception as e:  # noqa: BLE001 — gözlem kaybı; agent devam eder
                self.lost += len(rows)
                log.warning("agent trace: %d olay yazılamadı run=%s seq=%s..%s hata=%s", len(rows), self.run_id,
                            rows[0]["sequence"], rows[-1]["sequence"], type(e).__name__)

    async def finish(self, status: str, error: str | None = None) -> None:
        self._stop.set()
        try:
            await self._task
        except Exception:  # noqa: BLE001
            pass
        r = self.recorder
        try:
            r.emit("run_end", {"status": status, "error": error, "events": r.events + 1,
                               "capture_errors": r.capture_errors, "truncated": r.truncated},
                   status="error" if status == "failed" else "ok")
        except Exception:  # noqa: BLE001
            pass
        await self.flush()
        now = datetime.now(timezone.utc)
        try:
            from airlab_agent.redact import redact_text
            async with transaction(self.rt.sm) as s:
                await s.execute(update(AgentTraceRun).where(AgentTraceRun.id == self.row_id).values(
                    status=status, finished_at=now, duration_ms=int((now - self._t0).total_seconds() * 1000),
                    event_count=r.events - self.lost, truncated_count=r.truncated, capture_errors=r.capture_errors,
                    lost_events=self.lost, error=redact_text(error)[:2000] if error else None))
        except Exception as e:  # noqa: BLE001
            log.warning("agent trace: run kapanışı yazılamadı run=%s hata=%s", self.run_id, type(e).__name__)


async def start(rt, *, run_id: str, kind: str, graph: str, trigger: str | None = None,
                investigation_id: uuid.UUID | None = None, conversation_id: uuid.UUID | None = None,
                user_id: uuid.UUID | None = None, thread_id: str | None = None, parent_run_id: str | None = None,
                platform_run_id: str | None = None, attempt: int = 1, agent_id: uuid.UUID | None = None,
                agent_version: str | None = None, agent_mode: str | None = None) -> TraceSession | None:
    """Run satırını açar ve kaydediciyi döner. Kapalıysa ya da açılamazsa None (agent kayıtsız çalışır)."""
    if not enabled():
        return None
    try:
        async with transaction(rt.sm) as s:
            row = AgentTraceRun(run_id=run_id, kind=kind, graph=graph, trigger=trigger,
                                investigation_id=investigation_id, conversation_id=conversation_id, user_id=user_id,
                                thread_id=thread_id, parent_run_id=parent_run_id, platform_run_id=platform_run_id,
                                attempt=attempt, status="running", agent_id=agent_id, agent_version=agent_version,
                                agent_mode=agent_mode)
            s.add(row)
            await s.flush()
            row_id = row.id
        sess = TraceSession(rt, row_id, run_id, graph)
        sess.recorder.emit("run_start", {"run_id": run_id, "kind": kind, "graph": graph, "trigger": trigger,
                                         "thread_id": thread_id, "parent_run_id": parent_run_id, "attempt": attempt},
                           status="running")
        return sess
    except Exception as e:  # noqa: BLE001
        log.warning("agent trace: run kaydı açılamadı run=%s hata=%s", run_id, type(e).__name__)
        return None


STALE_AFTER_S = 3 * 3600     # bu süreden eski 'running' run yarıda kalmıştır (süreç yeniden başladı / öldü)


def effective_status(row: AgentTraceRun, now: datetime | None = None) -> str:
    """Backend ve worker ayrı süreçlerdir: başlangıçta toplu işaretleme diğer sürecin canlı run'ını bozar;
    yarıda kalma gösterimde zaman eşiğiyle belirlenir."""
    now = now or datetime.now(timezone.utc)
    if row.status == "running" and (now - row.started_at).total_seconds() > STALE_AFTER_S:
        return "interrupted"
    return row.status


async def attempt_for(sm, base: str) -> int:
    async with transaction(sm) as s:
        rows = (await s.execute(select(AgentTraceRun.run_id).where(AgentTraceRun.run_id.like(base + "%")))).all()
    return len(rows) + 1
