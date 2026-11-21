"""Ajan çalıştırma kaydı (SSE için). Çalıştırma bir arka plan görevidir; istemci bağlantısı koparsa iş DEVAM
EDER ve sonuç veritabanına yazılır. Olaylar bellekte sıralı numarayla tutulur; istemci `Last-Event-ID` ile kaldığı
yerden yeniden bağlanabilir. Bitmiş çalıştırmalar RETENTION_S sonra bellekten atılır (kalıcı kayıt DB'dedir).

Not: kayıt süreç içidir → backend tek worker (uvicorn --workers 1) çalıştırılır.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..agent.events import event

log = logging.getLogger("sre.runs")
MAX_EVENTS = 5000
RETENTION_S = 900


@dataclass
class Run:
    id: str
    kind: str                                   # chat | investigate
    user_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    investigation_id: str | None = None
    events: list[dict] = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    cancel: threading.Event = field(default_factory=threading.Event)
    done: bool = False
    finished_at: float | None = None
    task: asyncio.Task | None = None
    dropped: int = 0

    async def emit(self, type_: str, data: dict[str, Any]) -> None:
        if type_ == "investigation_started" and data.get("investigation_id"):
            self.investigation_id = data["investigation_id"]
        e = event(type_, data)
        async with self.cond:
            e["seq"] = len(self.events) + self.dropped + 1
            if type_ == "token" and len(self.events) >= MAX_EVENTS:
                self.dropped += 1                 # aşırı uzun akışta tokenlar düşebilir; sonuç DB'de tamdır
                return
            self.events.append(e)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.finished_at = time.monotonic()
            self.cond.notify_all()

    def status(self) -> dict:
        return {"run_id": self.id, "kind": self.kind, "done": self.done, "events": len(self.events),
                "conversation_id": str(self.conversation_id) if self.conversation_id else None,
                "investigation_id": self.investigation_id, "cancelled": self.cancel.is_set()}


class RunRegistry:
    def __init__(self):
        self.runs: dict[str, Run] = {}

    def active_for_conversation(self, conv_id: uuid.UUID) -> Run | None:
        return next((r for r in self.runs.values() if r.conversation_id == conv_id and not r.done), None)

    def active_count(self, user_id: uuid.UUID | None = None) -> int:
        return sum(1 for r in self.runs.values() if not r.done and (user_id is None or r.user_id == user_id))

    def start(self, kind: str, user_id: uuid.UUID, coro_factory, *, conversation_id: uuid.UUID | None = None) -> Run:
        self._gc()
        run = Run(id=f"run-{uuid.uuid4().hex[:16]}", kind=kind, user_id=user_id, conversation_id=conversation_id)

        async def _go():
            try:
                await coro_factory(run)
            except asyncio.CancelledError:
                await run.emit("error", {"code": "cancelled", "message": "çalıştırma durduruldu"})
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("ajan çalıştırması başarısız")
                from airlab_agent.redact import redact_text
                await run.emit("error", {"code": "internal", "message": redact_text(f"{type(e).__name__}: {e}")[:500]})
            finally:
                await run.emit("done", {"run_id": run.id})
                await run.finish()

        run.task = asyncio.create_task(_go(), name=run.id)
        self.runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def _gc(self) -> None:
        now = time.monotonic()
        for k in [k for k, r in self.runs.items() if r.done and r.finished_at and now - r.finished_at > RETENTION_S]:
            self.runs.pop(k, None)

    async def shutdown(self, timeout: float = 20) -> None:
        pending = [r for r in self.runs.values() if not r.done and r.task]
        for r in pending:
            r.cancel.set()
        if pending:
            done, rest = await asyncio.wait([r.task for r in pending], timeout=timeout)
            for t in rest:
                t.cancel()

    async def stream(self, run: Run, after: int = 0, keepalive_s: float = 15):
        """Olay üreteci: `after` numarasından sonraki olaylar; bitince kapanır."""
        idx = 0
        while True:
            async with run.cond:
                while True:
                    pending = [e for e in run.events[idx:] if e["seq"] > after]
                    idx = len(run.events)
                    if pending or run.done:
                        break
                    try:
                        await asyncio.wait_for(run.cond.wait(), keepalive_s)
                    except TimeoutError:
                        pending = None
                        break
            if pending is None:
                yield None                      # canlı tutma
                continue
            for e in pending:
                yield e
            if run.done and idx >= len(run.events):
                return
