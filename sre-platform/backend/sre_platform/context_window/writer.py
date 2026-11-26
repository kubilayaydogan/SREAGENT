"""Context window kayıtlarının kalıcı yazıcısı (süreç başına bir tane; API ve worker).

Ölçüm katmanı satırı thread-safe kuyruğa bırakır; pump görevi toplu INSERT yapar. Yazma hatası LLM çağrısını ve agent
çalışmasını etkilemez: satır kayıp sayılır ve loglanır. Pump görevi iptal edilmez (stop event); kapanışta kuyruk
boşaltılır.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import uuid

from sqlalchemy import insert

from airlab_agent import context_window as cw

from ..db.models import LlmContextCall
from ..db.session import transaction

log = logging.getLogger("sre.context_window")
FLUSH_S = 1.0
BATCH = 200
_COLS = {c.name for c in LlmContextCall.__table__.columns}


def to_row(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k in _COLS}
    for k in ("call_id", "logical_call_id"):
        out[k] = uuid.UUID(str(out[k]))
    for k, n in (("run_id", 160), ("thread_id", 160), ("user_id", 64), ("node", 64), ("task", 64), ("model", 128),
                 ("served_model", 128), ("error_code", 64), ("error_message", 300), ("stop_reason", 48),
                 ("capacity_source", 64), ("capacity_note", 160), ("estimate_method", 96), ("request_id", 80),
                 ("trace_call_id", 64), ("middleware_execution_id", 64)):
        if out.get(k) is not None:
            out[k] = str(out[k])[:n]
    if out.get("run_kind") is not None:
        out["run_kind"] = str(out["run_kind"])[:16]
    return out


class ContextWindowWriter:
    def __init__(self, sm):
        self.sm = sm
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.lost = 0
        self.written = 0
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    def put(self, rec: dict) -> None:
        self.q.put(to_row(rec))

    async def flush(self) -> int:
        n = 0
        while True:
            rows = []
            while len(rows) < BATCH:
                try:
                    rows.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if not rows:
                return n
            try:
                async with transaction(self.sm) as s:
                    await s.execute(insert(LlmContextCall), rows)
                n += len(rows)
                self.written += len(rows)
            except asyncio.CancelledError:
                self.lost += len(rows)
                raise
            except Exception as e:  # noqa: BLE001
                self.lost += len(rows)
                log.warning("context window: %d kayıt yazılamadı hata=%s", len(rows), type(e).__name__)

    async def _pump(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), FLUSH_S)
            except asyncio.TimeoutError:
                pass
            await self.flush()

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._pump(), name="context-window")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass
        await self.flush()


_WRITER: ContextWindowWriter | None = None


async def install(sm, llm_core=None) -> ContextWindowWriter:
    """Yazıcıyı başlatır, ölçüm katmanına bağlar; kapasite kaynağı olarak Models API'yi (varsa) kaydeder."""
    global _WRITER
    w = ContextWindowWriter(sm)
    await w.start()
    _WRITER = w
    cw.set_sink(w.put)
    if llm_core is not None and hasattr(llm_core, "model_info"):
        models = [m for m in {getattr(llm_core, "model_name", None), getattr(llm_core, "rca_model_name", None)} if m]
        cw.set_capacity_resolver(llm_core.model_info, prefetch=models)
    return w


async def uninstall(w: ContextWindowWriter) -> None:
    global _WRITER
    if _WRITER is w:
        cw.set_sink(None)
        _WRITER = None
    await w.stop()


def writer() -> ContextWindowWriter | None:
    return _WRITER
