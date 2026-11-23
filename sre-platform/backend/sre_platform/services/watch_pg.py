"""Watch: mevcut airlab_agent.watch durum makinesi AYNEN kullanılır (pencere değerlendirme, onay, hysteresis,
cooldown, veri boşluğu). Yalnız iki şey değişir:
  1. Kalıcı durum SQLite yerine PostgreSQL'de (sre.kv / watch_conditions / watch_windows / watch_events).
  2. Olay açılış/kapanışı platform kayıtlarına yazılır: incident (dedup_key='watch', koşullar listesi), bağlı
     araştırma (trigger=watch, ilişki trigger/recovery), zaman çizelgesi notları.
Watcher.tick() eşzamanlıdır ve ayrı thread'de çalışır; platformun async depoları ana döngüye köprülenir.
Otomatik geçiş yalnız 'Hafifletildi'ye kadardır — 'Çözüldü' insan kararıdır.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from airlab_agent.graph import new_run_id
from airlab_agent.signals import Window, iso
from airlab_agent.watch import Watcher

from ..agent.investigation import InvestigationRunner
from ..db.models import IncidentSource, IncidentStatus, Severity, Trigger
from ..db.session import transaction
from ..repositories import incidents, investigations
from ..repositories.base import Conflict

log = logging.getLogger("sre.watch")
WATCH_DEDUP = "watch"
KIND_TR = {"latency": "latency artışı", "errors": "error rate artışı", "pool": "Connection Pool saturation",
           "lock": "Lock Wait'teki session'lar", "db_conn": "PostgreSQL connection pressure", "cpu": "host CPU pressure"}


def describe(key: str) -> str:
    kind, _, target = key.partition(":")
    return f"{target} {KIND_TR.get(kind, kind)}"


def app_of(key: str) -> str | None:
    target = key.partition(":")[2]
    return target if target.endswith("-service") else None


async def _noop(*_a, **_k):
    return None


class PgStateStore:
    """airlab_agent.watch.StateStore arayüzü, PostgreSQL üzerinde (eşzamanlı psycopg; yalnız watch thread'i kullanır)."""

    def __init__(self, libpq_url: str):
        self.db = psycopg.connect(libpq_url, autocommit=False, application_name="sre-worker-watch")

    def close(self):
        self.db.close()

    def get(self, k, default=None):
        r = self.db.execute("SELECT value FROM sre.kv WHERE key=%s", (k,)).fetchone()
        return r[0] if r else default

    def put(self, k, v):
        self.db.execute("INSERT INTO sre.kv(key, value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (k, Jsonb(v)))
        self.db.commit()

    def conds(self) -> dict[str, dict]:
        return {k: v for k, v in self.db.execute("SELECT key, state FROM sre.watch_conditions")}

    def put_cond(self, k, v):
        self.db.execute("INSERT INTO sre.watch_conditions(key, state, updated_at) VALUES(%s,%s,now()) "
                        "ON CONFLICT (key) DO UPDATE SET state=EXCLUDED.state, updated_at=now()", (k, Jsonb(v)))

    def history(self, n: int) -> list[dict]:
        rows = self.db.execute("SELECT metrics FROM sre.watch_windows ORDER BY window_end DESC LIMIT %s", (n,)).fetchall()
        return [r[0] for r in reversed(rows)]

    def add_hist(self, end: str, m: dict, keep: int):
        self.db.execute("INSERT INTO sre.watch_windows(window_end, metrics) VALUES(%s,%s) "
                        "ON CONFLICT (window_end) DO UPDATE SET metrics=EXCLUDED.metrics", (end, Jsonb(m)))
        self.db.execute("DELETE FROM sre.watch_windows WHERE window_end NOT IN "
                        "(SELECT window_end FROM sre.watch_windows ORDER BY window_end DESC LIMIT %s)", (keep,))

    def event(self, kind, key, detail):
        self.db.execute("INSERT INTO sre.watch_events(kind, key, detail) VALUES(%s,%s,%s)",
                        (kind, key, Jsonb(json.loads(json.dumps(detail, default=str)))))
        self.db.commit()
        log.info("watch olayı", extra={"kind": kind, "key": key})

    def commit(self):
        self.db.commit()

    # Watcher.status() / temel _handle bunları kullanır; platformda incident'lar PG'de tutulur
    def open_incident(self):
        return None

    def save_incident(self, inc):  # pragma: no cover — PlatformWatcher._handle kullanmaz
        raise NotImplementedError


class PlatformWatcher(Watcher):
    def __init__(self, rt, loop: asyncio.AbstractEventLoop, store: PgStateStore, clock=None, gateway_factory=None):
        # watch turu sorguları için ayrı fabrika verilebilir (test: dakikalık zaman çizelgesi); varsayılan gerçek MCP
        super().__init__(rt.agent, gateway_factory or rt.gateway_factory, analyze_fn=None, store=store, clock=clock)
        self.rt, self.loop = rt, loop
        self.timeout = rt.ps.investigation_timeout_s + 60

    def _await(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(self.timeout)

    def _handle(self, t: dict, w: Window):
        key, tr = t["key"], t["transition"]
        if tr == "gap":
            self.store.event("telemetry_gap", key, t)
            return
        try:
            if tr == "open":
                r = self._await(self.on_open(t, w))
                self.store.event("incident_key_added" if r.get("deduped") else "incident_open", key, {**t, **r})
            elif tr == "recover":
                still_open = {k for k, c in self.store.conds().items() if c.get("state") == "open"}
                r = self._await(self.on_recover(t, w, still_open))
                self.store.event(r.pop("event"), key, {**t, **r})
        except Exception as e:  # noqa: BLE001 — kayıt hatası watch döngüsünü durdurmaz
            log.exception("watch olay işleme hatası")
            self.store.event("analysis_failed", key, {"error": f"{type(e).__name__}: {e}"[:500], **t})

    # ------------------------------------------------------------ async (ana döngüde)
    async def _investigate(self, inc_id: uuid.UUID, start: datetime, end: datetime, *, relation: str, mode: str,
                           title: str, notes: list[str], use_llm: bool, app: str | None):
        async with transaction(self.rt.sm) as s:
            inv = await investigations.create(s, run_id=new_run_id("inc" if relation == "trigger" else "rec"),
                                              trigger=Trigger.WATCH, title=title, window_start=start, window_end=end,
                                              application_id=app, description="; ".join(notes))
            await incidents.link_investigation(s, inc_id, inv.id, relation=relation)
            inv_id = inv.id
        llm = self.rt.llm.llm if (use_llm and self.rt.llm is not None and hasattr(self.rt.llm, "llm")) else None
        async with self.rt.sem:
            done, report = await InvestigationRunner(self.rt).run(inv_id, emit=_noop, mode=mode, core_llm=llm, notes=notes)
        if report:
            await self.rt.memory.remember_investigation(done, user_id=None, conversation_id=None)
        return done, report

    async def on_open(self, t: dict, w: Window) -> dict[str, Any]:
        key = t["key"]
        app = app_of(key)
        value = f"{t['value']:.3g}" if isinstance(t.get("value"), (int, float)) else "—"
        msg = f"Watch koşulu açıldı: {describe(key)} (değer {value}" + \
              (f", baseline {t['baseline']:.3g})" if isinstance(t.get("baseline"), (int, float)) else ")")
        async with transaction(self.rt.sm) as s:
            inc = await incidents.open_by_dedup(s, WATCH_DEDUP)
            if inc is None:
                try:
                    inc = await incidents.create(s, title=f"Watch: {describe(key)}", source=IncidentSource.WATCH,
                                                 severity=Severity.UNKNOWN, description=msg, application_id=app,
                                                 dedup_key=WATCH_DEDUP, conditions=[key], detected_at=w.end)
                    created = True
                except Conflict:                      # başka worker aynı anda açtı
                    inc, created = await incidents.open_by_dedup(s, WATCH_DEDUP), False
            else:
                created = False
            if not created:
                if key not in inc.conditions:
                    inc.conditions = [*inc.conditions, key]
                await incidents.add_note(s, inc.id, msg, kind="watch")
                if inc.status != IncidentStatus.MITIGATED:
                    return {"incident_id": str(inc.id), "deduped": True}
                # hafifletilmiş olay yeniden kötüleşti → incelemeye geri al ve yeni analiz çalıştır
                await incidents.transition(s, inc, IncidentStatus.INVESTIGATING, at=w.end,
                                           note=f"Watch: koşul yeniden açıldı ({describe(key)}).")
                reopened, inc_id = True, inc.id
            else:
                reopened, inc_id = False, inc.id
        start = w.end - timedelta(minutes=self.cfg["watch"]["analysis_minutes"])
        done, report = await self._investigate(
            inc_id, start, w.end, relation="related" if reopened else "trigger", mode="watch-incident", app=app,
            title=f"Watch olayı: {describe(key)}", notes=[msg], use_llm=self.cfg["watch"].get("llm_on_incident", True))
        async with transaction(self.rt.sm) as s:
            inc = await incidents.get(s, inc_id)
            await incidents.update_fields(
                s, inc, severity=done.severity if done.severity != Severity.UNKNOWN else None,
                root_cause=done.root_cause_hypothesis,
                recommended_actions=list((report or {}).get("human_review") or [])[:10] or None)
        return {"incident_id": str(inc_id), "investigation_id": str(done.id), "status": done.status.value,
                "deduped": reopened, "reopened": reopened}

    async def on_recover(self, t: dict, w: Window, still_open: set[str]) -> dict[str, Any]:
        key = t["key"]
        async with transaction(self.rt.sm) as s:
            inc = await incidents.open_by_dedup(s, WATCH_DEDUP)
            if inc is None or inc.status not in (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING):
                return {"event": "key_recovered", **({"incident_id": str(inc.id)} if inc else {})}
            remaining = [k for k in inc.conditions if k in still_open]
            if remaining:
                await incidents.add_note(s, inc.id, f"Koşul normale döndü: {describe(key)}; açık kalan: "
                                         + ", ".join(remaining), kind="watch")
                return {"event": "key_recovered", "incident_id": str(inc.id)}
            inc_id, opened = inc.id, inc.detected_at
        start = opened - timedelta(minutes=self.cfg["watch"]["analysis_minutes"])
        done, _ = await self._investigate(inc_id, start, w.end, relation="recovery", mode="watch-recovery", app=None,
                                          title="Watch recovery analizi", use_llm=False,
                                          notes=[f"tüm watch koşulları normale döndü ({iso(w.end)})"])
        async with transaction(self.rt.sm) as s:
            inc = await incidents.get(s, inc_id)
            if inc.status in (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING):
                await incidents.transition(s, inc, IncidentStatus.MITIGATED, at=w.end,
                                           note="Watch: tüm koşullar art arda temiz pencerelerle normale döndü "
                                                "(otomatik). 'Çözüldü' olarak kapatmak insan kararıdır.")
        return {"event": "incident_recovered", "incident_id": str(inc_id), "investigation_id": str(done.id)}

    def status(self) -> dict[str, Any]:
        return {"last_end": self.store.get("last_end"), "mcp_down": self.store.get("mcp_down", False),
                "open_conditions": [k for k, v in self.store.conds().items() if v.get("state") == "open"]}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
