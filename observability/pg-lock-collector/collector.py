#!/usr/bin/env python3
"""
airlab pg-lock-collector — KÜÇÜK, SALT-OKUR PostgreSQL kanıt toplayıcısı.

Bu bir agent DEĞİLDİR: karar vermez, sorgu sonlandırmaz, yalnız ölçer ve gönderir.
Dynatrace PostgreSQL extension'ın sağlamadığı / düşük sıklıkta sağladığı kanıtları üretir:
  * pg_blocking_pids() ile gerçek blocking zinciri (waiter → blocker; rol, application_name,
    wait_event, transaction yaşı, query_id, SQL yorumundaki op/rid/exp)  → LOG olayı
  * rol × application_name × state × wait_event_type oturum sayıları         → METRİK
  * global connection kapasitesi (reserved slotlar ayrı payda)              → METRİK
  * pg_stat_statements rol bazlı delta (reset/restart tespitli)            → METRİK + top-N LOG
  * pg_stat_database delta'ları                                            → METRİK

Yüksek cardinality'li kimlikler (pid, rid, exp, query_id) YALNIZ log özniteliğidir; metrik boyutu yapılmaz.
Çıkış: DT_MODE=dryrun (JSONL dosyası/stdout) | dynatrace (Metrics API v2 + Logs API v2) | both.
Gerekli DB yetkisi: pg_monitor rolü (lab_monitor). Bağlantı default_transaction_read_only=on.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("pg-lock-collector")

SQL_TAG = re.compile(r"/\*\s*app=(?P<app>[A-Za-z0-9._:-]+)(?:\s+op=(?P<op>[A-Za-z0-9._:-]+))?"
                     r"(?:\s+rid=(?P<rid>[A-Za-z0-9._:-]+))?(?:\s+exp=(?P<exp>[A-Za-z0-9._:-]+))?\s*\*/")
LAB_TAG = re.compile(r"/\*\s*lab:(?P<kind>[A-Za-z0-9._-]+)\s*\*/")
DIM_VALUE_SAFE = re.compile(r"[^A-Za-z0-9._:/ -]")


# --------------------------------------------------------------------------- yardımcılar (saf, test edilir)

def parse_sql_tag(query: str | None) -> dict[str, str]:
    """Uygulamaların SQL başına eklediği /* app=.. op=.. rid=.. exp=.. */ yorumunu ayrıştırır."""
    out: dict[str, str] = {}
    if not query:
        return out
    m = SQL_TAG.search(query)
    if m:
        out.update({k: v for k, v in m.groupdict().items() if v})
    lab = LAB_TAG.search(query)
    if lab:
        out["lab"] = lab.group("kind")
    return out


def dim(v: Any) -> str:
    """Metric line protocol boyut değeri: güvenli karakterler, en fazla 250 karakter, tırnaklı."""
    s = DIM_VALUE_SAFE.sub("_", "" if v is None else str(v))[:250]
    return '"' + s + '"'


def metric_line(key: str, dims: dict[str, Any], value: float, kind: str = "gauge", ts_ms: int | None = None) -> str:
    d = ",".join(f"{k}={dim(v)}" for k, v in sorted(dims.items()))
    head = key + ("," + d if d else "")
    payload = f"gauge,{value:g}" if kind == "gauge" else f"count,delta={value:g}"
    return f"{head} {payload}" + (f" {ts_ms}" if ts_ms else "")


@dataclass
class DeltaTracker:
    """Kümülatif sayaçlardan delta üretir. Reset/restart veya geri gidiş görülürse o tur delta ÜRETMEZ."""
    prev: dict[tuple, float] = field(default_factory=dict)
    epoch: Any = None

    def update(self, epoch: Any, values: dict[tuple, float]) -> tuple[dict[tuple, float], str | None]:
        if self.epoch is not None and epoch != self.epoch:
            self.prev, self.epoch = dict(values), epoch
            return {}, "reset_or_restart"
        self.epoch = epoch
        if not self.prev:
            self.prev = dict(values)
            return {}, "first_sample"
        deltas: dict[tuple, float] = {}
        for k, v in values.items():
            p = self.prev.get(k)
            if p is None:
                deltas[k] = v          # yeni anahtar (ör. yeni rol): ilk değer delta kabul edilir
            elif v < p:
                self.prev = dict(values)
                return {}, "counter_decreased"
            else:
                deltas[k] = v - p
        self.prev = dict(values)
        return deltas, None


def connection_capacity(max_conn: int, su_reserved: int, reserved: int, used_total: int) -> dict[str, float]:
    """Normal rollerin kullanabileceği slot = max - superuser_reserved - reserved. Hikari pool doluluğu ayrı paydadır."""
    normal_slots = max(1, max_conn - su_reserved - reserved)
    return {"max_connections": max_conn, "normal_slots": normal_slots, "used": used_total,
            "used_ratio_normal": round(used_total / normal_slots, 4)}


# --------------------------------------------------------------------------- SQL

Q_SETTINGS = """SELECT name, setting::int FROM pg_settings
 WHERE name IN ('max_connections','superuser_reserved_connections','reserved_connections')"""

Q_SESSIONS = """SELECT usename, coalesce(application_name,''), coalesce(state,'unknown'),
       coalesce(wait_event_type,'none'), count(*)
  FROM pg_stat_activity
 WHERE backend_type = 'client backend' AND datname = current_database()
 GROUP BY 1,2,3,4"""

Q_TOTAL_CLIENTS = "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"

Q_BLOCKING = """SELECT w.pid, w.usename, w.application_name, w.wait_event_type, w.wait_event,
       extract(epoch FROM now() - coalesce(w.state_change, w.query_start)) AS wait_s,
       extract(epoch FROM now() - w.xact_start) AS w_xact_age_s, w.query_id, left(w.query, 400),
       b.pid, b.usename, b.application_name, b.state, b.wait_event_type,
       extract(epoch FROM now() - b.xact_start) AS b_xact_age_s, b.query_id, left(b.query, 400)
  FROM pg_stat_activity w
  CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS bp(pid)
  JOIN pg_stat_activity b ON b.pid = bp.pid
 WHERE w.datname = current_database()"""

Q_DB = """SELECT extract(epoch FROM coalesce(stats_reset, pg_postmaster_start_time()))::bigint,
       xact_commit, xact_rollback, blks_read, blks_hit, deadlocks, temp_bytes, conflicts,
       extract(epoch FROM pg_postmaster_start_time())::bigint
  FROM pg_stat_database WHERE datname = current_database()"""

Q_STMT_EPOCH = """SELECT extract(epoch FROM i.stats_reset)::bigint, extract(epoch FROM pg_postmaster_start_time())::bigint
  FROM pg_stat_statements_info i"""

Q_STMT_ROLE = """SELECT r.rolname, sum(s.calls), sum(s.total_exec_time), sum(s.shared_blks_read),
       sum(s.shared_blks_hit), sum(s.rows)
  FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
 WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
 GROUP BY r.rolname"""

Q_STMT_TOP = """SELECT r.rolname, s.queryid, s.calls, s.total_exec_time, s.shared_blks_read, left(s.query, 300)
  FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
 WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
   AND r.rolname LIKE 'svc\\_%'"""


# --------------------------------------------------------------------------- çıkış

class Sink:
    def __init__(self, mode: str, dt_url: str | None, token: str | None, dryrun_file: str | None):
        self.mode = mode
        self.dt_url = (dt_url or "").rstrip("/")
        self.token = token
        self.dryrun_file = dryrun_file
        if mode in ("dynatrace", "both") and not (self.dt_url and token):
            raise SystemExit("DT_MODE=dynatrace için DT_URL ve DT_API_TOKEN gerekli")

    def send(self, lines: list[str], events: list[dict]) -> None:
        if self.mode in ("dryrun", "both"):
            rec = {"ts": datetime.now(timezone.utc).isoformat(), "metrics": lines, "logs": events}
            if self.dryrun_file:
                with open(self.dryrun_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                print(json.dumps(rec, ensure_ascii=False), flush=True)
        if self.mode in ("dynatrace", "both"):
            for i in range(0, len(lines), 900):   # payload 1 MB sınırının çok altında kal
                self._post("/api/v2/metrics/ingest", "\n".join(lines[i:i + 900]).encode(), "text/plain; charset=utf-8")
            if events:
                self._post("/api/v2/logs/ingest", json.dumps(events).encode(), "application/json; charset=utf-8")

    def _post(self, path: str, body: bytes, ctype: str, attempts: int = 3) -> None:
        for a in range(attempts):
            req = urllib.request.Request(self.dt_url + path, data=body, method="POST",
                                         headers={"Authorization": "Api-Token " + self.token, "Content-Type": ctype})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    if r.status >= 300:
                        log.warning("dynatrace %s -> %s", path, r.status)
                    return
            except urllib.error.HTTPError as e:
                detail = e.read()[:300]
                if e.code in (429, 502, 503, 504) and a < attempts - 1:
                    time.sleep(2 ** a)
                    continue
                log.error("dynatrace %s -> HTTP %s %s", path, e.code, detail)
                return
            except OSError as e:
                if a < attempts - 1:
                    time.sleep(2 ** a)
                    continue
                log.error("dynatrace %s erişilemedi: %s", path, e)
                return


# --------------------------------------------------------------------------- toplayıcı

class Collector:
    def __init__(self, conninfo: str, sink: Sink, prefix: str, db_host: str, stmt_interval: int, top_n: int):
        self.conninfo = conninfo
        self.sink = sink
        self.p = prefix
        self.db_host = db_host
        self.stmt_interval = stmt_interval
        self.top_n = top_n
        self.conn = None
        self.db_delta = DeltaTracker()
        self.stmt_delta = DeltaTracker()
        self.top_prev: dict[tuple, tuple] = {}
        self.top_epoch = None
        self.last_stmt = 0.0

    def _connect(self):
        import psycopg
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.conninfo, autocommit=True, connect_timeout=5,
                                        application_name="pg-lock-collector")
            with self.conn.cursor() as c:
                c.execute("SET statement_timeout = '3s'")
        return self.conn

    def collect_once(self) -> tuple[list[str], list[dict]]:
        t0 = time.monotonic()
        ts = int(time.time() * 1000)
        base = {"db.host": self.db_host}
        lines: list[str] = []
        events: list[dict] = []
        try:
            conn = self._connect()
            with conn.cursor() as c:
                c.execute(Q_SETTINGS)
                s = dict(c.fetchall())
                c.execute(Q_TOTAL_CLIENTS)
                used = c.fetchone()[0]
                cap = connection_capacity(s["max_connections"], s.get("superuser_reserved_connections", 3),
                                          s.get("reserved_connections", 0), used)
                lines.append(metric_line(f"{self.p}.connections.used", base, cap["used"], ts_ms=ts))
                lines.append(metric_line(f"{self.p}.connections.normal_slots", base, cap["normal_slots"], ts_ms=ts))
                lines.append(metric_line(f"{self.p}.connections.used_ratio_normal", base, cap["used_ratio_normal"], ts_ms=ts))

                c.execute(Q_SESSIONS)
                for role, app, state, wtype, n in c.fetchall():
                    lines.append(metric_line(f"{self.p}.sessions", {**base, "db.role": role, "app": app,
                                                                   "state": state, "wait_type": wtype}, n, ts_ms=ts))

                c.execute(Q_BLOCKING)
                edges = c.fetchall()
                waiters: dict[tuple, list[float]] = {}
                for (wpid, wrole, wapp, wet, we, wait_s, wxa, wqid, wq,
                     bpid, brole, bapp, bstate, bwet, bxa, bqid, bq) in edges:
                    wt, bt = parse_sql_tag(wq), parse_sql_tag(bq)
                    waiters.setdefault((wrole, wapp, brole, bapp), []).append(float(wait_s or 0))
                    events.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "loglevel": "WARN",
                        "log.source": "airlab.pg-lock-collector",
                        "airlab.event": "airlab.pg.blocking_edge",
                        "db.host": self.db_host,
                        "content": f"{wapp}({wrole}) pid {wpid} waits {wet}/{we} for {bapp}({brole}) pid {bpid} "
                                   f"wait={float(wait_s or 0):.2f}s blocker_xact_age={float(bxa or 0):.2f}s",
                        "waiter.pid": wpid, "waiter.db_role": wrole, "waiter.app": wapp,
                        "waiter.wait_event_type": wet, "waiter.wait_event": we,
                        "waiter.wait_seconds": round(float(wait_s or 0), 3),
                        "waiter.xact_age_seconds": round(float(wxa or 0), 3), "waiter.query_id": str(wqid),
                        "waiter.op": wt.get("op"), "waiter.request_id": wt.get("rid"), "waiter.experiment_id": wt.get("exp"),
                        "blocker.pid": bpid, "blocker.db_role": brole, "blocker.app": bapp, "blocker.state": bstate,
                        "blocker.wait_event_type": bwet, "blocker.xact_age_seconds": round(float(bxa or 0), 3),
                        "blocker.query_id": str(bqid), "blocker.op": bt.get("op"), "blocker.request_id": bt.get("rid"),
                        "blocker.experiment_id": bt.get("exp"), "blocker.lab_hold": bt.get("lab"),
                    })
                total = 0
                for (wrole, wapp, brole, bapp), waits in waiters.items():
                    d = {**base, "waiter.db_role": wrole, "waiter.app": wapp, "blocker.db_role": brole, "blocker.app": bapp}
                    lines.append(metric_line(f"{self.p}.lock.waiters", d, len(waits), ts_ms=ts))
                    lines.append(metric_line(f"{self.p}.lock.wait_max_seconds", d, round(max(waits), 3), ts_ms=ts))
                    total += len(waits)
                # sorgu BAŞARILI olduğunda gerçek sıfır yazılır (örnek anında bekleyen yok); hata durumunda yazılmaz
                lines.append(metric_line(f"{self.p}.lock.waiters_total", base, total, ts_ms=ts))

                c.execute(Q_DB)
                row = c.fetchone()
                epoch = (row[0], row[8])
                vals = {("xact_commit",): row[1], ("xact_rollback",): row[2], ("blks_read",): row[3],
                        ("blks_hit",): row[4], ("deadlocks",): row[5], ("temp_bytes",): row[6], ("conflicts",): row[7]}
                deltas, why = self.db_delta.update(epoch, {k: float(v) for k, v in vals.items()})
                for (name,), v in deltas.items():
                    lines.append(metric_line(f"{self.p}.db.{name}", base, v, kind="count", ts_ms=ts))
                if why == "reset_or_restart":
                    events.append(self._event("airlab.pg.stats_reset", "pg_stat_database reset/restart tespit edildi; bu tur delta üretilmedi"))

                if time.monotonic() - self.last_stmt >= self.stmt_interval:
                    self.last_stmt = time.monotonic()
                    lines += self._statements(c, base, ts, events)
            ok = 1
        except Exception as e:  # noqa: BLE001 — collector hiçbir hatada çökmemeli
            ok = 0
            log.warning("toplama hatası: %s", e)
            events.append(self._event("airlab.pg.collector_error", f"{type(e).__name__}: {e}", level="ERROR"))
            try:
                if self.conn is not None:
                    self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None
        lines.append(metric_line(f"{self.p}.collector.up", base, ok, ts_ms=ts))
        lines.append(metric_line(f"{self.p}.collector.scrape_ms", base, round((time.monotonic() - t0) * 1000, 1), ts_ms=ts))
        return lines, events

    def _statements(self, c, base, ts, events) -> list[str]:
        lines: list[str] = []
        c.execute(Q_STMT_EPOCH)
        epoch = tuple(c.fetchone())
        c.execute(Q_STMT_ROLE)
        vals = {}
        for role, calls, exec_ms, blks_read, blks_hit, rows in c.fetchall():
            vals[(role, "calls")] = float(calls)
            vals[(role, "exec_time_ms")] = float(exec_ms)
            vals[(role, "shared_blks_read")] = float(blks_read)
            vals[(role, "shared_blks_hit")] = float(blks_hit)
            vals[(role, "rows")] = float(rows)
        deltas, why = self.stmt_delta.update(epoch, vals)
        for (role, name), v in deltas.items():
            lines.append(metric_line(f"{self.p}.statements.{name}", {**base, "db.role": role}, round(v, 3), kind="count", ts_ms=ts))
        if why == "reset_or_restart":
            events.append(self._event("airlab.pg.stats_reset", "pg_stat_statements reset/restart; bu pencerede delta yok"))
        # top-N (rol × queryid) pencere deltası → log
        c.execute(Q_STMT_TOP)
        cur = {(r[0], r[1]): (float(r[2]), float(r[3]), float(r[4]), r[5]) for r in c.fetchall()}
        if self.top_epoch == epoch and self.top_prev:
            rows = []
            for k, (calls, ms, br, q) in cur.items():
                p = self.top_prev.get(k)
                dc, dm, db = (calls, ms, br) if p is None else (calls - p[0], ms - p[1], br - p[2])
                if dc > 0 and dm >= 0:
                    rows.append((dm, k, dc, db, q))
            for dm, (role, qid), dc, db, q in sorted(rows, reverse=True)[: self.top_n]:
                events.append({**self._event("airlab.pg.top_statement",
                                             f"{role} queryid={qid} calls={dc:g} exec_ms={dm:.1f} mean_ms={dm / dc:.2f}"),
                               "db.role": role, "query_id": str(qid), "calls_delta": dc, "exec_ms_delta": round(dm, 3),
                               "mean_ms": round(dm / dc, 3), "shared_blks_read_delta": db,
                               "window_seconds": self.stmt_interval, "query_text": q})
        self.top_prev, self.top_epoch = cur, epoch
        return lines

    def _event(self, etype: str, content: str, level: str = "INFO") -> dict:
        return {"timestamp": datetime.now(timezone.utc).isoformat(), "loglevel": level,
                "log.source": "airlab.pg-lock-collector", "airlab.event": etype, "db.host": self.db_host,
                "content": content}


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pw = os.getenv("PG_PASSWORD", "")
    if not pw:
        log.error("PG_PASSWORD tanımlı değil")
        return 2
    conninfo = (f"host={os.getenv('PG_HOST', 'localhost')} port={os.getenv('PG_PORT', '5432')} "
                f"dbname={os.getenv('PG_DB', 'airlab')} user={os.getenv('PG_USER', 'lab_monitor')} password={pw} "
                f"options='-c default_transaction_read_only=on'")
    sink = Sink(os.getenv("DT_MODE", "dryrun"), os.getenv("DT_URL"), os.getenv("DT_API_TOKEN"),
                os.getenv("DRYRUN_FILE") or None)
    interval = float(os.getenv("INTERVAL_S", "5"))
    col = Collector(conninfo, sink, os.getenv("METRIC_PREFIX", "airlab.pg"), os.getenv("DB_HOST_LABEL", os.getenv("PG_HOST", "db")),
                    int(os.getenv("STATEMENTS_INTERVAL_S", "60")), int(os.getenv("TOP_N", "10")))
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    log.info("başladı: interval=%ss mode=%s", interval, sink.mode)
    once = os.getenv("RUN_ONCE", "false").lower() == "true"
    while not stop["flag"]:
        t = time.monotonic()
        lines, events = col.collect_once()
        sink.send(lines, events)
        if once:
            break
        time.sleep(max(0.0, interval - (time.monotonic() - t)))
    log.info("durdu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
