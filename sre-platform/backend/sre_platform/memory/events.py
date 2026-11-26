"""Memory Event Publisher: memory işlemlerinin merkezi olay kaydı (sre.memory_events + sre.memory_retrievals).

* Olay payload'ı oluşturulurken maskelenir ve boyutu sınırlanır (airlab_agent.agent_trace.safe_copy: redaksiyon
  kuralları, hassas anahtar adları, ham telemetri özeti). Checkpoint verisi kopyalanmaz; yalnız kimlikler yazılır.
* Thread-safe kuyruk: analiz graph'ı ayrı thread'de (sync PostgresSaver) çalışır. Bir asyncio görevi kuyruğu
  periyodik toplu yazar. Yazma hatası agent'ı durdurmaz; kayıp olaylar sayılır ve payload'sız loglanır.
* Gözlem `agent_memory.memory_observability_enabled` (ve `memory_enabled`) ile açılır/kapanır; ayar ~30 sn'de bir
  okunur. Gözlem kapalıyken agent'ın memory davranışı değişmez.
* Korelasyon: `bind()` ile kurulan context (run_id, thread_id, trace_id, node, correlation_id) olaylara eklenir.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import queue
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, update

from airlab_agent.agent_trace import safe_copy

from ..db.models import MemoryEvent, MemoryRetrieval
from ..db.session import transaction
from .registry import DEFAULT_TENANT, EMBEDDED_AGENT

log = logging.getLogger("sre.memory.events")

EVENT_TYPES = (
    "MEMORY_READ", "MEMORY_WRITE", "MEMORY_UPDATE", "MEMORY_DELETE", "MEMORY_ARCHIVE", "MEMORY_SEARCH",
    "MEMORY_RETRIEVED", "MEMORY_REJECTED", "MEMORY_VALIDATED", "MEMORY_APPROVED", "MEMORY_EXPIRED",
    "MEMORY_CONSOLIDATED", "MEMORY_CONTEXT_INJECTED", "MEMORY_CONTEXT_TRUNCATED", "MEMORY_CHECKPOINT_CREATED",
    "MEMORY_CHECKPOINT_LOADED", "MEMORY_CHECKPOINT_RESTORED", "MEMORY_BACKEND_ERROR", "MEMORY_POLICY_VIOLATION",
)
# Kodda hiç üretilmeyen türler (arayüzde "bu sürümde üretilmez" olarak gösterilir)
NOT_EMITTED = {"MEMORY_CONSOLIDATED": "consolidation implement edilmedi",
               "MEMORY_CHECKPOINT_RESTORED": "replay / fork / restore sunulmuyor (yan etki riski)"}

FLUSH_S = 1.0
BATCH = 500
CFG_TTL_S = 30.0


@dataclass(frozen=True)
class MemCtx:
    run_id: str | None = None
    thread_id: str | None = None
    trace_id: str | None = None
    node: str | None = None
    correlation_id: str | None = None
    user_id: str | None = None
    agent_id: str = EMBEDDED_AGENT
    tenant_id: str = DEFAULT_TENANT
    cfg: Any = None              # koşu başındaki memory ayarı snapshot'ı (dict)


CTX: contextvars.ContextVar[MemCtx | None] = contextvars.ContextVar("airlab_memory_ctx", default=None)
SUPPRESS: contextvars.ContextVar[bool] = contextvars.ContextVar("airlab_memory_suppress", default=False)


def current() -> MemCtx:
    return CTX.get() or MemCtx()


@contextmanager
def bind(**kw):
    """Korelasyon bağlamı: verilen alanlar mevcut bağlamın üzerine yazılır (None olanlar korunur)."""
    base = current()
    token = CTX.set(replace(base, **{k: v for k, v in kw.items() if v is not None}))
    try:
        yield CTX.get()
    finally:
        CTX.reset(token)


@contextmanager
def suppressed():
    """Platformun kendi okumaları (Checkpoint Explorer, istatistik) agent memory olayı üretmez."""
    token = SUPPRESS.set(True)
    try:
        yield
    finally:
        SUPPRESS.reset(token)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEventPublisher:
    def __init__(self, rt=None, sm=None):
        self.rt = rt
        self.sm = sm if sm is not None else getattr(rt, "sm", None)
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.lost = 0
        self.published = 0
        self.cfg: dict[str, Any] = {}
        self._cfg_at = 0.0
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ ayar
    @property
    def enabled(self) -> bool:
        c = self.cfg
        return bool(c.get("memory_enabled", True)) and bool(c.get("memory_observability_enabled", True))

    async def refresh_cfg(self, force: bool = False) -> dict:
        if not force and time.monotonic() - self._cfg_at < CFG_TTL_S:
            return self.cfg
        self._cfg_at = time.monotonic()
        if self.rt is None:
            return self.cfg
        try:
            from ..settings import service
            async with transaction(self.sm) as s:
                self.cfg = (await service.effective(self.rt, s)).get("agent_memory", {})
        except Exception as e:  # noqa: BLE001 — ayar okunamazsa son bilinen değer
            log.warning("memory ayarı okunamadı: %s", type(e).__name__)
        return self.cfg

    def set_cfg(self, cfg: dict) -> None:
        self.cfg, self._cfg_at = dict(cfg), time.monotonic()

    # ------------------------------------------------------------ yayın
    def publish(self, event_type: str, *, memory_id: str | None = None, memory_type: str | None = None,
                namespace: str | None = None, status: str = "ok", duration_ms: float | None = None,
                error: BaseException | dict | str | None = None, payload: Any = None,
                retrieval_id: uuid.UUID | None = None, node: str | None = None, actor: str | None = None,
                force: bool = False, **ctx_over) -> uuid.UUID | None:
        """Olayı kuyruğa alır. Hiçbir koşulda exception fırlatmaz (gözlem agent'ı durdurmaz)."""
        if SUPPRESS.get() and not force:
            return None
        try:
            if not (self.enabled or force):
                return None
            if event_type not in EVENT_TYPES:
                raise ValueError(f"bilinmeyen memory event türü: {event_type}")
            c = replace(current(), **{k: v for k, v in ctx_over.items() if v is not None})
            data, truncated, size = safe_copy(payload if payload is not None else {})
            if truncated and isinstance(data, dict):
                data["_truncated"] = True
            err = None
            if error is not None:
                if isinstance(error, BaseException):
                    from airlab_agent.redact import redact_text
                    err = {"error_type": type(error).__name__, "message": redact_text(str(error))[:500]}
                elif isinstance(error, dict):
                    err = safe_copy(error)[0]
                else:
                    err = {"message": safe_copy(str(error))[0]}
            eid = uuid.uuid4()
            self.q.put(("event", {
                "id": eid, "ts": _now(), "tenant_id": c.tenant_id, "agent_id": c.agent_id, "event_type": event_type,
                "run_id": c.run_id, "thread_id": c.thread_id, "trace_id": c.trace_id, "node": node or c.node,
                "memory_id": (str(memory_id)[:200] if memory_id is not None else None), "memory_type": memory_type,
                "namespace": namespace[:200] if namespace else None, "status": status,
                "duration_ms": round(duration_ms, 3) if duration_ms is not None else None, "error": err,
                "correlation_id": c.correlation_id, "retrieval_id": retrieval_id, "actor": actor,
                "payload": data if isinstance(data, dict) else {"value": data}}))
            self.published += 1
            return eid
        except Exception as e:  # noqa: BLE001
            self.lost += 1
            log.warning("memory event üretilemedi tür=%s hata=%s", event_type, type(e).__name__)
            return None

    def retrieval(self, row: dict, *, force: bool = False) -> None:
        if (SUPPRESS.get() and not force) or not (self.enabled or force):
            return
        try:
            c = current()
            for k in ("query", "filters", "selected", "excluded", "stages", "injection"):
                if row.get(k) is not None:
                    row[k] = safe_copy(row[k])[0]
            row.setdefault("ts", _now())
            row.setdefault("tenant_id", c.tenant_id)
            row.setdefault("agent_id", c.agent_id)
            for k in ("run_id", "thread_id", "trace_id", "node"):
                row.setdefault(k, getattr(c, k))
            if c.user_id and not row.get("user_id"):
                row["user_id"] = uuid.UUID(c.user_id)
            self.q.put(("retrieval", row))
        except Exception as e:  # noqa: BLE001
            self.lost += 1
            log.warning("retrieval kaydı üretilemedi hata=%s", type(e).__name__)

    # ------------------------------------------------------------ yazma
    async def flush(self) -> int:
        n = 0
        while True:
            ev, rv, up = [], [], []
            while len(ev) + len(rv) + len(up) < BATCH:
                try:
                    kind, row = self.q.get_nowait()
                except queue.Empty:
                    break
                {"event": ev, "retrieval": rv, "retrieval_update": up}[kind].append(row)
            if not ev and not rv and not up:
                return n
            try:
                async with transaction(self.sm) as s:
                    if rv:
                        await s.execute(insert(MemoryRetrieval), rv)
                    if ev:
                        await s.execute(insert(MemoryEvent), ev)
                    for u in up:                      # context injection sonucu (insert'ten sonra; FIFO)
                        await s.execute(update(MemoryRetrieval).where(MemoryRetrieval.id == u["id"])
                                        .values(injection=u["injection"]))
                n += len(ev) + len(rv) + len(up)
            except asyncio.CancelledError:
                self.lost += len(ev) + len(rv) + len(up)
                raise
            except Exception as e:  # noqa: BLE001
                self.lost += len(ev) + len(rv) + len(up)
                log.warning("memory events: %d kayıt yazılamadı hata=%s", len(ev) + len(rv) + len(up),
                            type(e).__name__)

    async def _pump(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), FLUSH_S)
            except asyncio.TimeoutError:
                pass
            await self.refresh_cfg()
            await self.flush()

    async def start(self) -> None:
        await self.refresh_cfg(force=True)
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._pump(), name="memory-events")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass
        await self.flush()


_PUB: MemoryEventPublisher | None = None


def install(pub: MemoryEventPublisher | None) -> None:
    global _PUB
    _PUB = pub


def publisher() -> MemoryEventPublisher | None:
    return _PUB


def publish(event_type: str, **kw) -> uuid.UUID | None:
    p = _PUB
    return p.publish(event_type, **kw) if p is not None else None


def cfg() -> dict:
    p = _PUB
    return p.cfg if p is not None else {}


class timer:
    """`with timer() as t: ...; t.ms`"""

    def __enter__(self):
        self._t = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self._t) * 1000
        return False
