"""Sürekli izleme. Normal döngü tamamen deterministiktir (Claude çağrısı yok).

- Değerlendirme birimi: ingest gecikmesi düşülmüş, TAMAMLANMIŞ 1 dakikalık pencereler.
- Her pencere bir kez değerlendirilir (son değerlendirilen pencere SQLite'ta) → yeniden başlatmada aynı pencere ikinci
  kez onay sayılmaz; en fazla max_backfill_windows geriye gidilir.
- Olay açma: aynı anahtar art arda confirm_windows YENİ pencerede `open` eşiğini aşmalı.
- Hysteresis: kapanma `clear` eşiğinin altında art arda clear_windows pencere ister; arada kalan değer sayaçları sıfırlar.
- Cooldown: kapanan anahtar cooldown süresince yeniden olay açmaz. Dedup: açık olay varken yeni anahtarlar ona eklenir.
- Veri yoksa (gap) sayaçlar değişmez; uzun boşluk ayrı "telemetri eksik" olayıdır — sıfır gibi değerlendirilmez.
- Olay açıldığında tam analiz (LangGraph) çalışır; Claude yalnız burada ve yapılandırmayla açıksa çağrılır,
  erişilemezse rapor deterministik açıklamayla yazılır.
"""
from __future__ import annotations

import json
import logging
import signal
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from .features import extract
from .mcp_gateway import McpUnavailable
from .signals import WATCH_SIGNALS, Window, build_queries, iso, parse_ts

log = logging.getLogger("airlab_agent.watch")
STOP = threading.Event()


# ---------------------------------------------------------------- saf fonksiyonlar (test edilir)
def window_metrics(fw: dict, cfg: dict) -> dict[str, float | None]:
    m: dict[str, float | None] = {}
    minreq = cfg["watch"].get("min_requests", 5)
    for app in cfg["apps"]:
        f = fw["apps"].get(app, {})
        enough = (f.get("req") or 0) >= minreq
        m[f"latency:{app}"] = f.get("http_mean_ms") if enough else None
        m[f"errors:{app}"] = f.get("err_rate") if enough else None
        m[f"pool:{app}"] = f.get("pool_ratio_avg", f.get("pool_ratio"))
    db = fw["db"]
    m["lock:db"] = db.get("lock_waiters_avg") if (db.get("collector_points") or 0) > 0 else None
    m["db_conn:db"] = db.get("conn_ratio")
    for role, host in cfg["hosts"].items():
        m[f"cpu:{host}"] = (fw["hosts"].get(host) or {}).get("cpu")
    return m


THRESH_KEY = {"latency": "latency_ratio", "errors": "error_rate", "pool": "pool_ratio", "lock": "lock_waiters",
              "db_conn": "db_conn_ratio", "cpu": "host_cpu_pct"}


def verdict(key: str, value: float | None, history: list[float | None], cfg: dict) -> tuple[str, float | None, float | None]:
    """→ (bad|clear|hold|gap, karşılaştırılan_değer, baseline). Gecikme baseline'a oranla; diğerleri mutlak eşikle."""
    kind = key.split(":")[0]
    th = cfg["watch"]["thresholds"][THRESH_KEY[kind]]
    if value is None:
        return "gap", None, None
    base = None
    if kind == "latency":
        hist = [h for h in history if h is not None]
        if len(hist) < max(3, cfg["watch"]["baseline_windows"] // 3):
            return "gap", None, None                     # baseline yetersiz → karar verilmez
        base = median(hist)
        value = value / max(base, 1e-6)
    if value >= th["open"]:
        return "bad", value, base
    if value <= th["clear"]:
        return "clear", value, base
    return "hold", value, base


def step(c: dict, v: str, now: datetime, cfg: dict) -> tuple[dict, str | None]:
    """Durum makinesi. c: {state, bad, clear, gap, cooldown_until}. Dönüş: (yeni durum, geçiş|None)."""
    w = cfg["watch"]
    c = dict(c)
    if v == "gap":
        c["gap"] = c.get("gap", 0) + 1
        return c, ("gap" if c["gap"] == w["gap_windows_event"] else None)
    c["gap"] = 0
    cooldown = c.get("cooldown_until") and now < parse_ts(c["cooldown_until"])
    if c.get("state", "ok") == "ok":
        c["bad"] = c.get("bad", 0) + 1 if v == "bad" else 0
        if c["bad"] >= w["confirm_windows"] and not cooldown:
            c.update(state="open", clear=0)
            return c, "open"
        return c, None
    # open
    c["clear"] = c.get("clear", 0) + 1 if v == "clear" else 0
    if c["clear"] >= w["clear_windows"]:
        c.update(state="ok", bad=0, clear=0, cooldown_until=iso(now + timedelta(minutes=w["cooldown_minutes"])))
        return c, "recover"
    return c, None


# ---------------------------------------------------------------- kalıcı durum
class StateStore:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS cond(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS hist(end TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, opened_at TEXT, closed_at TEXT, status TEXT,
                                             keys TEXT, reports TEXT);
        CREATE TABLE IF NOT EXISTS events(ts TEXT, kind TEXT, key TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS chat(thread TEXT, ts TEXT, role TEXT, content TEXT);
        """)

    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))
        self.db.commit()

    def conds(self) -> dict[str, dict]:
        return {k: json.loads(v) for k, v in self.db.execute("SELECT k, v FROM cond")}

    def put_cond(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO cond VALUES(?,?)", (k, json.dumps(v)))

    def history(self, n: int) -> list[dict]:
        rows = self.db.execute("SELECT v FROM hist ORDER BY end DESC LIMIT ?", (n,)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def add_hist(self, end: str, m: dict, keep: int):
        self.db.execute("INSERT OR REPLACE INTO hist VALUES(?,?)", (end, json.dumps(m)))
        self.db.execute("DELETE FROM hist WHERE end NOT IN (SELECT end FROM hist ORDER BY end DESC LIMIT ?)", (keep,))

    def event(self, kind, key, detail):
        ts = iso(datetime.now(timezone.utc))
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (ts, kind, key, json.dumps(detail, default=str)))
        self.db.commit()
        log.info("event %s %s %s", kind, key, json.dumps(detail, default=str, ensure_ascii=False))

    def open_incident(self) -> dict | None:
        r = self.db.execute("SELECT id, opened_at, keys, reports FROM incidents WHERE status='open'").fetchone()
        return {"id": r[0], "opened_at": r[1], "status": "open", "keys": json.loads(r[2]),
                "reports": json.loads(r[3])} if r else None

    def save_incident(self, inc: dict):
        self.db.execute("INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?,?)",
                        (inc["id"], inc["opened_at"], inc.get("closed_at"), inc["status"], json.dumps(inc["keys"]),
                         json.dumps(inc["reports"])))
        self.db.commit()

    def commit(self):
        self.db.commit()


# ---------------------------------------------------------------- döngü
class Watcher:
    def __init__(self, settings, gateway_factory, analyze_fn, store: StateStore | None = None, clock=None):
        self.s, self.cfg = settings, settings.cfg
        self.gw_factory, self.analyze_fn = gateway_factory, analyze_fn
        self.store = store or StateStore(settings.state_db)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _new_windows(self) -> tuple[list[Window], list[Window]]:
        w = self.cfg["watch"]
        lag = self.cfg["windows"]["ingest_lag_seconds"]
        latest = (self.clock() - timedelta(seconds=lag)).replace(second=0, microsecond=0)
        last = self.store.get("last_end")
        hist_only: list[Window] = []
        if last is None:                       # ilk çalıştırma: baseline geçmişi doldur, değerlendirme yapma
            first = latest - timedelta(minutes=w["baseline_windows"])
            hist_only = [Window(f"h{i}", first + timedelta(minutes=i), first + timedelta(minutes=i + 1))
                         for i in range(w["baseline_windows"])]
            return hist_only, []
        start = max(parse_ts(last), latest - timedelta(minutes=w["max_backfill_windows"]))
        n = int((latest - start).total_seconds() // 60)
        return [], [Window(f"w{i}", start + timedelta(minutes=i), start + timedelta(minutes=i + 1)) for i in range(n)]

    def tick(self) -> dict:
        hist_ws, new_ws = self._new_windows()
        ws = hist_ws + new_ws
        if not ws:
            return {"evaluated": 0}
        try:
            gw = self.gw_factory()
            res = gw.run_many(build_queries(ws, self.cfg, WATCH_SIGNALS))
            results = {k: {"status": r.status, "records": r.records, "dql": r.dql} for k, r in res.items()}
        except McpUnavailable as e:
            # MCP yoksa pencereleri İLERLETME: veri geldiğinde değerlendirilsin. Olay olarak kaydet (dedup'lı).
            if not self.store.get("mcp_down"):
                self.store.event("mcp_unavailable", "mcp", {"error": str(e)})
                self.store.put("mcp_down", True)
            return {"evaluated": 0, "mcp": "down"}
        if self.store.get("mcp_down"):
            self.store.event("mcp_restored", "mcp", {})
            self.store.put("mcp_down", False)
        F = extract(results, ws, self.cfg)
        keep = self.cfg["watch"]["baseline_windows"]
        for w in hist_ws:
            self.store.add_hist(iso(w.end), window_metrics(F[w.name], self.cfg), keep)
        if hist_ws:
            self.store.put("last_end", iso(hist_ws[-1].end))
            return {"evaluated": 0, "history_filled": len(hist_ws)}
        transitions = []
        for w in new_ws:
            transitions += self._evaluate(w, window_metrics(F[w.name], self.cfg))
        return {"evaluated": len(new_ws), "transitions": transitions}

    def _evaluate(self, w: Window, m: dict) -> list[dict]:
        hist = self.store.history(self.cfg["watch"]["baseline_windows"])
        conds = self.store.conds()
        now = w.end
        out = []
        for key, value in m.items():
            v, cmp, base = verdict(key, value, [h.get(key) for h in hist], self.cfg)
            c, tr = step(conds.get(key, {"state": "ok"}), v, now, self.cfg)
            c.update(last=v, value=cmp, baseline=base, at=iso(now))
            self.store.put_cond(key, c)
            if tr:
                out.append({"key": key, "transition": tr, "value": cmp, "baseline": base, "window_end": iso(now)})
        # baseline geçmişine yalnız olay dışı pencereler girer (olay sırasında baseline kirlenmesin)
        any_open = any(c.get("state") == "open" for c in self.store.conds().values())
        if not any_open:
            self.store.add_hist(iso(w.end), m, self.cfg["watch"]["baseline_windows"])
        self.store.put("last_end", iso(w.end))          # bu pencere bir daha değerlendirilmez
        self.store.commit()
        for t in out:
            self._handle(t, w)
        return out

    def _handle(self, t: dict, w: Window):
        key, tr = t["key"], t["transition"]
        if tr == "gap":
            self.store.event("telemetry_gap", key, t)
            return
        inc = self.store.open_incident()
        if tr == "open":
            if inc:                                         # dedup: açık olaya ekle
                if key not in inc["keys"]:
                    inc["keys"].append(key)
                    self.store.save_incident(inc)
                self.store.event("incident_key_added", key, {"incident": inc["id"], **t})
                return
            start = w.end - timedelta(minutes=self.cfg["watch"]["analysis_minutes"])
            from .graph import new_run_id
            inc = {"id": new_run_id("inc"), "opened_at": iso(w.end), "status": "open", "keys": [key], "reports": []}
            self.store.save_incident(inc)
            self.store.event("incident_open", key, {"incident": inc["id"], **t})
            r = self._analyze(inc, start, w.end, "watch-incident", self.cfg["watch"].get("llm_on_incident", True))
            if r:
                inc["reports"].append(r)
                self.store.save_incident(inc)
        elif tr == "recover" and inc:
            conds = self.store.conds()
            if all(conds.get(k, {}).get("state") != "open" for k in inc["keys"]):
                start = parse_ts(inc["opened_at"]) - timedelta(minutes=self.cfg["watch"]["analysis_minutes"])
                r = self._analyze(inc, start, w.end, "watch-recovery", False)
                inc.update(status="closed", closed_at=iso(w.end))
                if r:
                    inc["reports"].append(r)
                self.store.save_incident(inc)
                self.store.event("incident_recovered", key, {"incident": inc["id"], **t})
            else:
                self.store.event("key_recovered", key, {"incident": inc["id"], **t})

    def _analyze(self, inc, start, end, mode, use_llm) -> str | None:
        try:
            st = self.analyze_fn(start=start, end=end, mode=mode, use_llm=use_llm,
                                 run_id=f"{inc['id']}-{'open' if mode == 'watch-incident' else 'recovery'}",
                                 notes=[f"watch olayı {inc['id']}: anahtarlar {', '.join(inc['keys'])}"])
            return st.get("run_id")
        except Exception as e:  # noqa: BLE001 — analiz hatası watch'u durdurmaz
            self.store.event("analysis_failed", inc["id"], {"error": f"{type(e).__name__}: {e}"})
            return None

    def run_forever(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: STOP.set())
            except (ValueError, OSError):
                pass
        poll = self.cfg["watch"]["poll_seconds"]
        log.info("watch başladı (poll=%ss, onay=%s pencere)", poll, self.cfg["watch"]["confirm_windows"])
        backoff = poll
        while not STOP.is_set():
            try:
                r = self.tick()
                log.info("tick %s", json.dumps(r, default=str, ensure_ascii=False))
                backoff = poll if r.get("mcp") != "down" else min(backoff * 2, 600)
            except Exception as e:  # noqa: BLE001
                log.exception("tick hatası: %s", type(e).__name__)
                backoff = min(backoff * 2, 600)
            STOP.wait(backoff)
        log.info("watch düzgün kapatıldı")

    def status(self) -> dict[str, Any]:
        return {"last_end": self.store.get("last_end"), "mcp_down": self.store.get("mcp_down", False),
                "open_incident": self.store.open_incident(),
                "conditions": {k: {x: v.get(x) for x in ("state", "bad", "clear", "gap", "last", "value", "baseline")}
                               for k, v in self.store.conds().items() if v.get("state") == "open" or v.get("bad")}}
