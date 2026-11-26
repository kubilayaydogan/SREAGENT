"""Run başına middleware bağlamı ve execution kaydı yazıcısı.

* `start` run başında yapılandırma SNAPSHOT'ını (`rc.middleware`) `airlab_agent.middleware.RUN` contextvar'ına bağlar;
  run sürerken yapılan yapılandırma değişiklikleri bu run'ı etkilemez. Her kayıt kullandığı gerçek sürümü taşır.
* before_agent burada (custom run wrapper), after_agent `finish`'te yalnız normal tamamlanmada çalışır; hata / iptal
  durumunda yalnız run kökü kaydı `status` ile yazılır (after_agent bir finally mechanism'i değildir).
* Kayıtlar hem analiz thread'inden hem sohbet görevinden gelir → thread-safe kuyruk; bir asyncio görevi periyodik
  boşaltır. Yazma hatası agent'ı durdurmaz (telemetry fail-open); kayıp sayısı loglanır.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import uuid
from datetime import datetime

from sqlalchemy import insert

from airlab_agent import middleware as mw

from ..db.models import MiddlewareExecution
from ..db.session import transaction

log = logging.getLogger("sre.middleware")
FLUSH_S = 0.5
BATCH = 300
_COLS = {c.key for c in MiddlewareExecution.__table__.columns}


def _row(ev: dict) -> dict:
    r = {k: v for k, v in ev.items() if k in _COLS}
    r["id"] = uuid.UUID(ev["execution_id"])
    r["parent_execution_id"] = uuid.UUID(ev["parent_execution_id"]) if ev.get("parent_execution_id") else None
    r["started_at"] = datetime.fromisoformat(ev["started_at"])
    r["ended_at"] = datetime.fromisoformat(ev["ended_at"]) if ev.get("ended_at") else None
    for k, n in (("target", 128), ("task", 128), ("tool_call_id", 64), ("policy", 160), ("error_code", 64),
                 ("run_id", 160), ("thread_id", 160), ("trace_id", 64), ("user_id", 64)):
        if isinstance(r.get(k), str):
            r[k] = r[k][:n]
    return r


class MwSession:
    def __init__(self, rt, run: mw.RunMw, graph: str, token):
        self.rt, self.run, self.graph, self._token = rt, run, graph, token
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.write_lost = 0
        run.sink = self.q.put
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._pump(), name=f"middleware-{run.run_id}")

    async def _pump(self) -> None:
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
                    rows.append(_row(self.q.get_nowait()))
                except queue.Empty:
                    break
                except Exception:  # noqa: BLE001 — bozuk kayıt: yalnız sayılır
                    self.write_lost += 1
            if not rows:
                return
            try:
                async with transaction(self.rt.sm) as s:
                    await s.execute(insert(MiddlewareExecution), rows)
            except asyncio.CancelledError:
                self.write_lost += len(rows)
                raise
            except Exception as e:  # noqa: BLE001 — telemetry kaybı; agent devam eder
                self.write_lost += len(rows)
                log.warning("middleware execution kaydı yazılamadı (%s kayıt): %s", len(rows), type(e).__name__)

    async def finish(self, status: str, error: str | None = None) -> None:
        try:
            mw.agent_end(self.run, graph=self.graph, status=status, error=error)
        except Exception:  # noqa: BLE001
            log.exception("middleware run kapanışı kaydedilemedi")
        self._stop.set()
        try:
            await self._task
        except Exception:  # noqa: BLE001
            pass
        await self.flush()
        if self.run.lost or self.write_lost:
            log.warning("middleware kayıt kaybı run=%s emit=%s write=%s", self.run.run_id, self.run.lost,
                        self.write_lost)
        if self._token is not None:
            try:
                mw.RUN.reset(self._token)
            except ValueError:                # farklı context'ten kapatıldı: çağıranın sarmalayıcısı geri alır
                pass
            self._token = None


async def start(rt, rc, *, run_id: str, kind: str, graph: str, thread_id: str | None = None,
                trace_id: str | None = None, user_id: str | None = None) -> MwSession:
    """Run'ı middleware bağlamına alır ve before_agent'ı çalıştırır. Hata olsa bile run varsayılan (kod) yapılandırma
    ile çalışmaya devam eder: zorunlu güvenlik middleware'leri her koşulda etkindir."""
    settings = dict(mw.DEFAULT_SETTINGS)
    settings.update(((getattr(rc, "effective", None) or {}).get("agent_middleware") or {}))
    cfg = getattr(rc, "middleware", None) or mw.default_config()
    parent = mw.RUN.get()
    run = mw.RunMw(run_id=run_id, thread_id=thread_id, trace_id=trace_id, kind=kind, user_id=user_id, config=cfg,
                   settings=settings,
                   parent_id=parent.root_id if parent is not None and parent.run_id else None)
    token = mw.RUN.set(run)
    sess = MwSession(rt, run, graph, token)
    mw.agent_start(run, graph=graph)
    return sess
