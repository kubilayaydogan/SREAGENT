"""Sentetik Dynatrace MCP kayıtları (yalnız test). Biçim, 2026-11-21'de gerçek `execute-dql` çıktısından alındı:
timeseries kayıtları `timeframe{start,end}`, `interval` (ns, string) ve alan başına dizi içerir; fetch/summarize
kayıtları düz alanlardır. Senaryo etiketleri BU dosyada değil, eval/labels.yaml'dadır.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from airlab_agent.config import load_yaml_config
from airlab_agent.mcp_gateway import McpUnavailable, QueryResult
from airlab_agent.signals import Window, iso

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
BASE_MIN, WIN_MIN = 15, 5
APPS = ["reservation-service", "checkin-service", "flightops-service", "baggage-service"]

NORMAL_APP = {"rpm": 60.0, "mean_ms": 20.0, "err": 0.0, "p95": 30.0, "span_n": 200, "pool_a": 1.0, "pool_pe": 0.0,
              "pool_to": 0.0, "sessions": 2.0, "exec_ms": 100.0, "cores": 0.2, "idle_tx": 0.0}
NORMAL = {"apps": {a: dict(NORMAL_APP) for a in APPS},
          "hosts": {"app-vm": 30.0, "db-vm": 10.0}, "conn_ratio": 0.1, "lock_total": 0.0,
          "lock_pairs": {}, "edges": [], "pg_lock_logs": 0, "collector": True, "tooling_cores": {"chaos-controller": 0.1,
                                                                                                 "sre-agent": 0.05},
          # Dynatrace PostgreSQL extension (postgres.*), dakika başına; None → o dönemde extension verisi yok
          "pg": {"active_ms": 2000.0, "writes": 100.0, "autovac_blocks": 0.0, "ckpt_blocks": 80.0},
          # İsteğe bağlı anahtarlar (yoksa boşta değerler): "dbstats" {dl, rb, cm, tmp, rd} dakika başına;
          # "host_mem" {host: %}; "pg_errors" {db role: {dl, sr}} dönem toplamı; pg içinde ins / ret / wal.
          }


def cfg():
    return load_yaml_config(None)


def windows(start: datetime = T0) -> list[Window]:
    s = start + timedelta(minutes=BASE_MIN)
    return [Window("baseline", start, s), Window("window", s, s + timedelta(minutes=WIN_MIN))]


def deep(d, **over):
    out = copy.deepcopy(d)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(out[k].get(kk), dict):
                    out[k][kk].update(vv)
                else:
                    out[k][kk] = vv
        else:
            out[k] = v
    return out


def _ts(ws, per_window: dict[str, dict[str, float | None]], dims: dict, n_bins_override=None) -> dict:
    start, end = ws[0].start, ws[-1].end
    n = int((end - start).total_seconds() // 60)
    rec = {**dims, "timeframe": {"start": iso(start).replace("Z", ".000000000Z"), "end": iso(end)},
           "interval": "60000000000"}
    fields = next(iter(per_window.values())).keys()
    for f in fields:
        arr = []
        for i in range(n):
            t = start + timedelta(minutes=i)
            w = next(x for x in ws if x.start <= t < x.end)
            arr.append(per_window[w.name][f])
        rec[f] = arr
    return rec


def build(spec_base: dict, spec_win: dict, ws=None, drop: tuple = (), stale: tuple = ()) -> dict[str, QueryResult]:
    """Baseline ve pencere senaryosundan tüm katalog sinyallerinin MCP sonuçlarını üretir."""
    return build_multi({"baseline": spec_base, "window": spec_win}, ws or windows(), drop, stale)


def build_multi(S: dict[str, dict], ws: list[Window], drop: tuple = (), stale: tuple = ()) -> dict[str, QueryResult]:
    """Pencere adı → senaryo eşlemesinden sonuç üretir (watch testleri dakikalık pencere kullanır)."""
    R: dict[str, list] = {k: [] for k in ("http", "spans", "hikari", "jvm_gc", "jvm_heap", "hosts", "containers",
                                          "db_conn", "db_sessions", "lock_waiters", "collector", "edges",
                                          "pg_lock_logs", "db_stats", "statements", "pg_server", "pg_io",
                                          "pg_error_logs")}
    for app in APPS:
        for outcome in ("SUCCESS", "SERVER_ERROR"):
            pw = {}
            for wn, sp in S.items():
                a = sp["apps"][app]
                n = a["rpm"] * ((1 - a["err"]) if outcome == "SUCCESS" else a["err"])
                pw[wn] = {"n": n, "t": n * a["mean_ms"], "mx": a["mean_ms"] * 3}
            R["http"].append(_ts(ws, pw, {"service": app, "outcome": outcome}))
        for wn, sp in S.items():
            a = sp["apps"][app]
            R["spans"].append({"w": wn, "svc": app, "n": str(int(a["span_n"])), "p95": str(a["p95"]),
                               "p50": str(a["p95"] / 2)})
        R["hikari"].append(_ts(ws, {wn: {"a": sp["apps"][app]["pool_a"], "pe": sp["apps"][app]["pool_pe"], "mx": 8.0,
                                         "to": sp["apps"][app]["pool_to"]} for wn, sp in S.items()}, {"service": app}))
        R["jvm_gc"].append(_ts(ws, {wn: {"gc": 1.0, "gcmax": 10.0, "ov": 0.001} for wn in S}, {"service": app}))
        R["jvm_heap"].append(_ts(ws, {wn: {"used": 5e7} for wn in S}, {"service": app, "area": "heap"}))
        R["db_sessions"].append(_ts(ws, {wn: {"s": sp["apps"][app]["sessions"]} for wn, sp in S.items()},
                                    {"app": app, "state": "idle", "wait_type": "Client"}))
        R["db_sessions"].append(_ts(ws, {wn: {"s": sp["apps"][app].get("idle_tx", 0.0)} for wn, sp in S.items()},
                                    {"app": app, "state": "idle in transaction", "wait_type": "Client"}))
        role = "svc_" + app.split("-")[0]
        R["statements"].append(_ts(ws, {wn: {"et": sp["apps"][app]["exec_ms"], "calls": 10.0} for wn, sp in S.items()},
                                   {"db.role": role}))
        img = "airlab/" + app.split("-")[0] + ":1.0.0"
        R["containers"].append(_ts(ws, {wn: {"u": sp["apps"][app]["cores"] * 60e9, "s": 0.0, "rss": 4e8}
                                        for wn, sp in S.items()}, {"name": img}))
    for tool in ("chaos-controller", "sre-agent"):
        R["containers"].append(_ts(ws, {wn: {"u": sp["tooling_cores"].get(tool, 0.0) * 60e9, "s": 0.0, "rss": 1e8}
                                        for wn, sp in S.items()}, {"name": f"airlab/{tool}:1.0.0"}))
    for host in ("app-vm", "db-vm"):
        R["hosts"].append(_ts(ws, {wn: {"cpu": sp["hosts"][host], "io": 0.5, "mem": sp.get("host_mem", {}).get(host, 30.0)}
                                   for wn, sp in S.items()},
                              {"host": host, "dt.smartscape.host": f"HOST-{host}"}))
    R["db_conn"].append(_ts(ws, {wn: {"r": sp["conn_ratio"], "used": sp["conn_ratio"] * 97, "slots": 97.0}
                                 for wn, sp in S.items()}, {}))
    R["collector"].append(_ts(ws, {wn: ({"up": 1.0, "tot": sp["lock_total"], "tavg": sp["lock_total"]} if sp["collector"]
                                        else {"up": None, "tot": None, "tavg": None}) for wn, sp in S.items()}, {}))
    pairs = {(wa, ba) for sp in S.values() for (wa, ba) in sp["lock_pairs"]}
    for (wa, ba) in pairs:
        R["lock_waiters"].append(_ts(ws, {wn: ({"wt": sp["lock_pairs"].get((wa, ba)), "wmax": 2.0}
                                               if sp["lock_pairs"].get((wa, ba)) else {"wt": None, "wmax": None})
                                          for wn, sp in S.items()}, {"waiter.app": wa, "blocker.app": ba}))
    for wn, sp in S.items():
        for e in sp["edges"]:
            R["edges"].append({"w": wn, **e})
        if sp["pg_lock_logs"]:
            R["pg_lock_logs"].append({"w": wn, "n": str(sp["pg_lock_logs"])})
    R["db_stats"].append(_ts(ws, {wn: {"dl": 0.0, "rb": 0.0, "cm": 100.0, "tmp": 0.0, "rd": 0.0,
                                       **sp.get("dbstats", {})} for wn, sp in S.items()}, {}))
    for wn, sp in S.items():
        for role, d in sp.get("pg_errors", {}).items():
            R["pg_error_logs"].append({"w": wn, "role": role, "dl": str(d.get("dl", 0)), "sr": str(d.get("sr", 0))})
    if any(sp.get("pg") for sp in S.values()):
        nul = dict.fromkeys(("act", "ret", "ins", "upd", "dlt", "cw", "cs", "wal", "bc"))
        R["pg_server"].append(_ts(ws, {wn: ({"act": sp["pg"]["active_ms"], "ret": sp["pg"].get("ret", 5000.0),
                                              "ins": sp["pg"].get("ins", 0.0),
                                              "upd": sp["pg"]["writes"], "dlt": 0.0,
                                              "cw": 100.0, "cs": 1.0, "bc": sp["pg"]["ckpt_blocks"],
                                              "wal": sp["pg"].get("wal", 1e5)} if sp.get("pg") else nul) for wn, sp in S.items()}, {}))
        nio = dict.fromkeys(("rd", "wr", "ext", "hit", "rt", "wt"))
        for bt in ("autovacuum worker", "client backend"):
            R["pg_io"].append(_ts(ws, {wn: ({"rd": 0.0 if bt == "autovacuum worker" else 5.0, "wr": 0.0, "ext": 0.0,
                                              "hit": sp["pg"]["autovac_blocks"] if bt == "autovacuum worker" else 0.0,
                                              "rt": 0.0, "wt": 0.0} if sp.get("pg") else nio)
                                       for wn, sp in S.items()}, {"backend_type": bt}))

    out = {}
    for k, recs in R.items():
        if k in drop:
            out[k] = QueryResult(k, f"-- synthetic {k}", "EMPTY", [])
            continue
        if k in stale:   # son 10 dakika boş
            for r in recs:
                for f, v in r.items():
                    if isinstance(v, list):
                        r[f] = v[:-10] + [None] * 10
        out[k] = QueryResult(k, f"-- synthetic {k}", "OK" if recs else "EMPTY", recs, query_id=f"q-{k}")
    return out


class FakeGateway:
    """McpGateway yerine: önceden üretilmiş sonuçları döndürür ya da MCP kesintisini taklit eder."""
    def __init__(self, results: dict[str, QueryResult] | None = None, down: bool = False, extra: dict | None = None):
        self.results, self.down, self.extra = results or {}, down, extra or {}
        self.queries_used = 0
        self.calls: list[list[str]] = []

    def run_many(self, items):
        self.calls.append([k for k, _ in items])
        if self.down:
            raise McpUnavailable("ConnectError: simulated outage")
        out = {}
        for k, d in items:
            self.queries_used += 1
            r = self.results.get(k) or self.extra.get(k) or QueryResult(k, d, "EMPTY", [])
            out[k] = QueryResult(k, d, r.status, r.records, r.query_id, r.error)
        return out


class TimelineGateway:
    """Watch testleri için: DQL'deki from/to aralığını 1 dk pencerelere böler, her dakika için timeline(t) senaryosunu
    kullanır. down=True iken MCP kesintisi taklit edilir."""
    def __init__(self, timeline):
        self.timeline, self.down, self.queries_used, self.calls = timeline, False, 0, 0

    def run_many(self, items):
        import re
        from airlab_agent.signals import parse_ts
        self.calls += 1
        if self.down:
            raise McpUnavailable("ConnectError: simulated outage")
        m = re.search(r'from:"([^"]+)", to:"([^"]+)"', items[0][1])
        start, end = parse_ts(m.group(1)), parse_ts(m.group(2))
        n = int((end - start).total_seconds() // 60)
        ws = [Window(f"m{i}", start + timedelta(minutes=i), start + timedelta(minutes=i + 1)) for i in range(n)]
        res = build_multi({w.name: self.timeline(w.start) for w in ws}, ws)
        keys = [k for k, _ in items]
        self.queries_used += len(keys)
        return {k: res[k] for k in keys}
