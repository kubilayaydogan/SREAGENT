"""MCP sonuçlarından pencere bazında özellik çıkarımı + veri kalitesi.

Kalite durumları: OK | EMPTY | PERMISSION | ERROR | STALE | UNAVAILABLE | SKIPPED_BUDGET.
"Gerçek sıfır" ile "veri yok" ayrımı: bir değer yalnızca kaynağı veri ÜRETTİĞİ kanıtlanmışsa 0 kabul edilir
(ör. kilit beklemesi 0 → collector o dakikada başarılı ve waiters_total=0 yazmış). Aksi halde None (bilinmiyor).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from .redact import strip_fields
from .signals import Window, _agg, last_point, num, split_series

TS_FIELDS = {
    "http": ["n", "t", "mx"], "hikari": ["a", "pe", "mx", "to"], "jvm_gc": ["gc", "gcmax", "ov"], "jvm_heap": ["used"],
    "hosts": ["cpu", "io", "mem"], "containers": ["u", "s", "rss"], "db_conn": ["r", "used", "slots"],
    "db_sessions": ["s"], "lock_waiters": ["wt", "wmax"], "collector": ["up", "tot", "tavg"],
    "db_stats": ["dl", "rb", "cm", "tmp", "rd"], "statements": ["et", "calls"],
    "pg_server": ["act", "ret", "ins", "upd", "dlt", "cw", "cs", "wal", "bc"],
    "pg_io": ["rd", "wr", "ext", "hit", "rt", "wt"],
}
# pg_stat_io backend tipleri: maintenance ile client yükünü ayırmak için
PG_IO_GROUPS = {"autovacuum worker": "autovacuum", "client backend": "client"}


def _empty_window() -> dict:
    return {"apps": {}, "hosts": {}, "db": {}, "containers": {}}


def _app(fw: dict, app: str) -> dict:
    return fw["apps"].setdefault(app, {})


def _qr(results: dict, key: str) -> dict:
    r = results.get(key)
    if r is None:
        return {"status": "MISSING", "records": []}
    return r if isinstance(r, dict) else r.__dict__


def extract(results: dict[str, Any], ws: list[Window], cfg: dict) -> dict[str, dict]:
    """results: signal → QueryResult (veya dict). Dönüş: pencere adı → özellikler."""
    F = {w.name: _empty_window() for w in ws}
    apps = set(cfg["apps"])

    def ts(key):
        r = _qr(results, key)
        return r["records"] if r["status"] == "OK" else []

    # HTTP (Micrometer): istek sayısı, 5xx oranı, ortalama süre
    acc: dict[tuple, dict] = {}
    for rec in ts("http"):
        svc, outcome = rec.get("service"), rec.get("outcome")
        for wn, vals in split_series(rec, ws, TS_FIELDS["http"]).items():
            a = acc.setdefault((wn, svc), {"n": 0.0, "t": 0.0, "err": 0.0, "mx": [], "pts": 0})
            a["n"] += sum(vals["n"]); a["t"] += sum(vals["t"]); a["mx"] += vals["mx"]; a["pts"] = max(a["pts"], len(vals["n"]))
            if outcome == "SERVER_ERROR":
                a["err"] += sum(vals["n"])
    for (wn, svc), a in acc.items():
        if svc not in apps and svc not in cfg["tooling_apps"]:
            continue
        w = next(x for x in ws if x.name == wn)
        f = _app(F[wn], svc)
        f.update(req=a["n"], rpm=a["n"] / max(w.minutes, 1e-9), http_points=a["pts"],
                 err_rate=(a["err"] / a["n"]) if a["n"] else None,
                 http_mean_ms=(a["t"] / a["n"]) if a["n"] else None, http_max_ms=max(a["mx"]) if a["mx"] else None)

    # Span'lar (OneAgent): p95 yalnız yeterli örnek varsa
    for rec in ts("spans"):
        wn, svc = rec.get("w"), rec.get("svc")
        if wn in F and svc in apps:
            n = num(rec.get("n")) or 0
            f = _app(F[wn], svc)
            f.update(span_n=n, span_p95_ms=num(rec.get("p95")), span_p50_ms=num(rec.get("p50")))

    # Hikari havuzu
    for rec in ts("hikari"):
        svc = rec.get("service")
        if svc not in apps:
            continue
        for wn, v in split_series(rec, ws, TS_FIELDS["hikari"]).items():
            if not v["a"]:
                continue
            mx = _agg(v["mx"], "max")
            f = _app(F[wn], svc)
            f.update(pool_active_max=_agg(v["a"], "max"), pool_max=mx, pool_pending_max=_agg(v["pe"], "max"),
                     pool_timeouts=_agg(v["to"], "sum") or 0.0,
                     pool_ratio=(_agg(v["a"], "max") / mx) if mx else None,
                     pool_ratio_avg=(_agg(v["a"], "avg") / mx) if mx else None)

    for rec in ts("jvm_gc"):
        svc = rec.get("service")
        if svc in apps:
            for wn, v in split_series(rec, ws, TS_FIELDS["jvm_gc"]).items():
                if v["ov"] or v["gc"]:
                    _app(F[wn], svc).update(gc_overhead=_agg(v["ov"], "max"), gc_pause_max_ms=_agg(v["gcmax"], "max"))

    for rec in ts("hosts"):
        host = rec.get("host")
        for wn, v in split_series(rec, ws, TS_FIELDS["hosts"]).items():
            if v["cpu"]:
                F[wn]["hosts"][host] = {"cpu": _agg(v["cpu"], "avg"), "cpu_max": _agg(v["cpu"], "max"),
                                        "iowait": _agg(v["io"], "avg"), "mem": _agg(v["mem"], "avg"),
                                        "points": len(v["cpu"])}

    img_map = cfg["container_image_to_app"]
    for rec in ts("containers"):
        name = str(rec.get("name") or "")
        owner = img_map.get(name.split(":")[0], name)
        for wn, v in split_series(rec, ws, TS_FIELDS["containers"]).items():
            if v["u"]:
                # usage_*_time: 1 dk kovada CPU-nanosaniye → ortalama çekirdek
                cores = (sum(v["u"]) + sum(v["s"])) / 1e9 / 60 / len(v["u"])
                F[wn]["containers"][owner] = {"cpu_cores": round(cores, 4), "rss_max": _agg(v["rss"], "max")}

    for rec in ts("db_conn"):
        for wn, v in split_series(rec, ws, TS_FIELDS["db_conn"]).items():
            if v["r"]:
                F[wn]["db"].update(conn_ratio=_agg(v["r"], "max"), conn_used_max=_agg(v["used"], "max"),
                                   conn_slots=_agg(v["slots"], "max"))

    for rec in ts("db_sessions"):
        app, state, wt = rec.get("app"), rec.get("state"), rec.get("wait_type")
        if app not in apps:            # havuz dışı bağlantı (farklı application_name): DB role → uygulama; ham ad tutulmaz
            app = cfg["db_role_to_app"].get(rec.get("db.role"))
        for wn, v in split_series(rec, ws, TS_FIELDS["db_sessions"]).items():
            if v["s"] and app in apps:
                f = _app(F[wn], app)
                avg = _agg(v["s"], "avg")
                f["db_sessions"] = f.get("db_sessions", 0.0) + avg
                if state == "active" and wt == "Lock":
                    f["db_sessions_lockwait"] = f.get("db_sessions_lockwait", 0.0) + avg
                if str(state or "").startswith("idle in transaction"):   # DB, transaction içinde client'ı bekliyor
                    f["db_sessions_idle_tx"] = f.get("db_sessions_idle_tx", 0.0) + avg

    # Collector sağlığı: hangi dakikalarda başarılı ölçüm var → kilit değerleri o dakikalar için "gerçek"
    for rec in ts("collector"):
        for wn, v in split_series(rec, ws, TS_FIELDS["collector"]).items():
            F[wn]["db"].update(collector_points=len(v["tot"]), collector_up_min=_agg(v["up"], "min"),
                               lock_waiters_max=_agg(v["tot"], "max"), lock_waiters_avg=_agg(v["tavg"], "avg"))

    lw_seen: dict[str, set] = {}
    for rec in ts("lock_waiters"):
        wapp, bapp = rec.get("waiter.app"), rec.get("blocker.app")
        for wn, v in split_series(rec, ws, TS_FIELDS["lock_waiters"]).items():
            if v["wt"]:
                f = _app(F[wn], wapp) if wapp in apps else None
                if f is not None:
                    # metrik yalnız bekleme varken yazılır; ortalama = toplam / pencere dakikası
                    w = next(x for x in ws if x.name == wn)
                    f["lock_waiters_avg"] = f.get("lock_waiters_avg", 0.0) + sum(v["wt"]) / max(w.minutes, 1)
                    f["lock_wait_max_s"] = max(f.get("lock_wait_max_s", 0.0), _agg(v["wmax"], "max") or 0.0)
                    lw_seen.setdefault(wn, set()).add(wapp)

    # "Gerçek 0" yalnız query'nin kendisi yanıt verdiyse geçerlidir. Query başarısızsa (ERROR, TIMEOUT, PERMISSION…)
    # kayıt yokluğu ölçüm değildir: değer None kalır, skor kapsamından düşer.
    lw_answered = _qr(results, "lock_waiters")["status"] in ("OK", "EMPTY")
    for wn, fw in F.items():
        ok = (fw["db"].get("collector_points") or 0) > 0 and lw_answered
        for app in apps:                       # collector çalışıyorsa bekleme serisi olmayan uygulama = gerçek 0
            f = _app(fw, app)
            if "lock_waiters_avg" not in f:
                f["lock_waiters_avg"] = 0.0 if ok else None

    edges = strip_fields(ts("edges"), cfg["strip_fields"])
    for rec in edges:
        wn = rec.get("w")
        if wn in F:
            F[wn]["db"].setdefault("edges", []).append(
                {k: rec.get(k) for k in ("waiter_app", "blocker_app", "wait_type", "waiter_op", "blocker_op",
                                         "blocker_state", "blocker_wait")} | {"n": num(rec.get("n")) or 0,
                                                              "wait_max_s": num(rec.get("wait_max"))})
    edges_answered = _qr(results, "edges")["status"] in ("OK", "EMPTY")
    for wn, fw in F.items():
        ok = (fw["db"].get("collector_points") or 0) > 0 and edges_answered
        e = fw["db"].get("edges", [])
        fw["db"]["edges_total"] = sum(x["n"] for x in e) if (e or ok) else None

    for rec in ts("pg_lock_logs"):
        if rec.get("w") in F:
            F[rec["w"]]["db"]["pg_lock_wait_logs"] = num(rec.get("n")) or 0
    if _qr(results, "pg_lock_logs")["status"] in ("OK", "EMPTY"):
        for fw in F.values():
            fw["db"].setdefault("pg_lock_wait_logs", 0)

    for rec in ts("db_stats"):
        for wn, v in split_series(rec, ws, TS_FIELDS["db_stats"]).items():
            if v["cm"]:
                F[wn]["db"].update(deadlocks=_agg(v["dl"], "sum"), rollbacks=_agg(v["rb"], "sum"),
                                   commits=_agg(v["cm"], "sum"), temp_bytes=_agg(v["tmp"], "sum"))

    # Dynatrace PostgreSQL extension (postgres.*). Değerler dakika başına: toplam / dolu nokta sayısı (boşluklar sayılmaz).
    # Kayıt yoksa alanlar hiç yazılmaz → "veri yok" (sıfır değil).
    for rec in ts("pg_server"):
        for wn, v in split_series(rec, ws, TS_FIELDS["pg_server"]).items():
            n = len(v["act"])
            if not n:
                continue
            per_min = lambda *fs: sum(sum(v[f]) for f in fs) / n   # noqa: E731
            d = F[wn]["db"]
            d.update(pg_points=n, pg_active_ms_per_min=per_min("act"), pg_tup_read_per_min=per_min("ret"),
                     pg_tup_write_per_min=per_min("ins", "upd", "dlt"))
            if v["cw"] or v["cs"]:
                d["pg_checkpoint_ms_per_min"] = (sum(v["cw"]) + sum(v["cs"])) / max(len(v["cw"]), len(v["cs"]))
            if v["wal"]:
                d["pg_wal_bytes_per_min"] = sum(v["wal"]) / len(v["wal"])
            if v["bc"]:
                d["pg_checkpoint_blocks_per_min"] = sum(v["bc"]) / len(v["bc"])

    io_acc: dict[tuple, dict] = {}
    for rec in ts("pg_io"):
        grp = PG_IO_GROUPS.get(str(rec.get("backend_type") or ""))
        if not grp:
            continue
        for wn, v in split_series(rec, ws, TS_FIELDS["pg_io"]).items():
            n = max(len(v["rd"]), len(v["wr"]), len(v["ext"]), len(v["hit"]))
            if n:
                a = io_acc.setdefault((wn, grp), {"blocks": 0.0, "ms": 0.0, "n": 0})
                # hit dahil: veri shared_buffers'tayken vacuum/query işi disk I/O'su olmadan (yalnız CPU) geçer
                a["blocks"] += sum(v["rd"]) + sum(v["wr"]) + sum(v["ext"]) + sum(v["hit"])
                a["ms"] += sum(v["rt"]) + sum(v["wt"])
                a["n"] = max(a["n"], n)
    for (wn, grp), a in io_acc.items():
        F[wn]["db"][f"pg_{grp}_blocks_per_min"] = a["blocks"] / a["n"]
        F[wn]["db"][f"pg_{grp}_io_ms_per_min"] = a["ms"] / a["n"]

    role_map = cfg["db_role_to_app"]
    for rec in ts("statements"):
        app = role_map.get(rec.get("db.role"))
        for wn, v in split_series(rec, ws, TS_FIELDS["statements"]).items():
            if app and v["calls"]:
                w = next(x for x in ws if x.name == wn)
                f = _app(F[wn], app)
                f["db_exec_ms_per_min"] = (_agg(v["et"], "sum") or 0) / max(w.minutes, 1e-9)
                f["db_calls_per_min"] = (_agg(v["calls"], "sum") or 0) / max(w.minutes, 1e-9)
    return F


# ---------------------------------------------------------------- veri kalitesi
def assess_quality(results: dict[str, Any], target: Window, cfg: dict) -> dict[str, dict]:
    q = cfg["quality"]
    out: dict[str, dict] = {}
    for key, r in results.items():
        r = _qr(results, key)
        st, detail = r["status"], r.get("error")
        if st == "OK" and key in TS_FIELDS:
            last = None
            for rec in r["records"]:
                lp = last_point(rec, TS_FIELDS[key])
                last = max(last, lp) if (last and lp) else (lp or last)
            if last is None:
                st, detail = "EMPTY", "zaman serisi kaydı var ama tüm noktalar boş"
            elif last < target.start:
                st, detail = "STALE", "hedef pencerede veri noktası yok (yalnız baseline'da var)"
            else:
                age = (target.end - (last + timedelta(minutes=1))).total_seconds()
                if age > q["stale_after_seconds"]:
                    st, detail = "STALE", f"son veri noktası pencere sonundan {int(age)} sn önce"
        out[key] = {"status": st, "detail": detail, "evidence_id": r.get("evidence_id") or _evid(r)}
    return out


def _evid(r: dict) -> str | None:
    import hashlib
    return ("ev-" + hashlib.sha1(r["dql"].encode()).hexdigest()[:10]) if r.get("dql") else None


def app_coverage(fw: dict, app: str, cfg: dict) -> dict:
    """Uygulama bazında hangi ölçümlerin güvenilir olduğu."""
    f = fw["apps"].get(app, {})
    return {
        "http": f.get("req") is not None,
        "latency_spans": (f.get("span_n") or 0) >= cfg["quality"]["min_span_samples"],
        "pool": f.get("pool_ratio") is not None,
        "lock": f.get("lock_waiters_avg") is not None,
    }


def rolling_median(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return median(vs) if vs else None


def window_list(start: datetime, end: datetime, baseline_minutes: int) -> list[Window]:
    return [Window("baseline", start - timedelta(minutes=baseline_minutes), start), Window("window", start, end)]
