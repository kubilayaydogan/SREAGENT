"""Dashboard telemetrisi — Dynatrace MCP üzerinden, sabit DQL şablonlarıyla (kullanıcı girdisi DQL'e yalnız beyaz
listeli uygulama adı ve sabit zaman aralığı olarak girer). Veri yoksa değer `None` + durum `no_data`; asla sahte veri
üretilmez. Sonuçlar kısa süre (TTL) önbelleklenir; aynı anahtar için eşzamanlı istekler tek sorguda birleşir.

Sağlık durumu deterministiktir:
  unknown  — pencerede telemetri yok (veri yok ≠ kapalı)
  down     — istek var ve hata oranı ≥ %50, ya da uygulama container'ı raporlamıyor (host raporlarken)
  degraded — hata oranı ≥ watch eşiği, p95 ≥ 2× önceki dönem, havuz ≥ %90 ya da açık watch koşulu/incident
  healthy  — veri var ve yukarıdakiler yok
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from airlab_agent.mcp_gateway import McpUnavailable
from airlab_agent.signals import iso, num, parse_ts

RANGES = {"15m": (15, 1), "1h": (60, 5), "6h": (360, 15), "24h": (1440, 60)}   # dakika, aralık (dk)
CACHE_TTL_S = 30


@dataclass(frozen=True)
class Span:
    key: str
    start: datetime
    end: datetime
    interval_min: int

    @property
    def rng(self) -> str:
        return f'from:"{iso(self.start)}", to:"{iso(self.end)}"'


def make_span(range_key: str, lag_s: int, now: datetime | None = None) -> Span:
    if range_key not in RANGES:
        raise ValueError(f"geçersiz aralık: {range_key} (izinli: {', '.join(RANGES)})")
    minutes, iv = RANGES[range_key]
    now = now or datetime.now(timezone.utc)
    end = (now - timedelta(seconds=lag_s)).replace(second=0, microsecond=0)
    end = end - timedelta(minutes=end.minute % iv) if iv > 1 else end
    return Span(range_key, end - timedelta(minutes=minutes), end, iv)


def _uri(cfg) -> str:
    return " and ".join(f'not startsWith(uri, "{p}")' for p in cfg["latency_exclude_path_prefixes"])


def _path(cfg) -> str:
    return "".join(f' and not startsWith(url.path, "{p}")' for p in cfg["latency_exclude_path_prefixes"])


def queries(sp: Span, cfg: dict) -> list[tuple[str, str]]:
    iv = f"{sp.interval_min}m"
    span_f = f'filter span.kind == "server" and isNotNull(spring.application.name){_path(cfg)}'
    prev = Span(sp.key, sp.start - (sp.end - sp.start), sp.start, sp.interval_min)
    return [
        ("http", f"timeseries n=sum(airlab.app.http.server.requests, rollup: count), "
                 f"t=sum(airlab.app.http.server.requests, rollup: sum), {sp.rng}, interval:{iv}, "
                 f"by:{{service, outcome}}, filter:{{{_uri(cfg)}}}"),
        ("spans_total", f"fetch spans, {sp.rng} | {span_f} | summarize n=count(), p50=percentile(duration, 50)/1000000, "
                        f"p95=percentile(duration, 95)/1000000, p99=percentile(duration, 99)/1000000, "
                        f"by:{{svc=spring.application.name}}"),
        ("spans_prev", f"fetch spans, {prev.rng} | {span_f} | summarize n=count(), p95=percentile(duration, 95)/1000000, "
                       f"by:{{svc=spring.application.name}}"),
        ("spans_series", f"fetch spans, {sp.rng} | {span_f} | summarize n=count(), p95=percentile(duration, 95)/1000000, "
                         f"p99=percentile(duration, 99)/1000000, by:{{svc=spring.application.name, t=bin(start_time, {iv})}}"),
        ("hikari", f"timeseries a=max(airlab.app.hikaricp.connections.active), mx=max(airlab.app.hikaricp.connections.max), "
                   f"pe=max(airlab.app.hikaricp.connections.pending), {sp.rng}, interval:{iv}, by:{{service}}"),
        ("db_conn", f"timeseries r=max(airlab.pg.connections.used_ratio_normal), used=max(airlab.pg.connections.used), "
                    f"slots=max(airlab.pg.connections.normal_slots), {sp.rng}, interval:{iv}"),
        ("containers", f"timeseries u=sum(dt.containers.cpu.usage_user_time), {sp.rng}, interval:{iv}, "
                       f"by:{{dt.smartscape.container}} | fieldsAdd name=getNodeName(dt.smartscape.container)"),
    ]


def _bins(rec: dict) -> list[datetime]:
    tf, iv = rec.get("timeframe") or {}, num(rec.get("interval"))
    if not tf or not iv:
        return []
    start, step = parse_ts(tf["start"]), timedelta(microseconds=iv / 1000)
    n = max((len(v) for v in rec.values() if isinstance(v, list)), default=0)
    return [start + i * step for i in range(n)]


def _r(v: float | None, n: int = 1) -> float | None:
    return round(v, n) if v is not None else None


def compute(results: dict[str, Any], sp: Span, cfg: dict, *, open_keys: set[str] | None = None) -> dict[str, Any]:
    """MCP sonuçlarından uygulama metrikleri, zaman serileri ve sağlık durumu (saf fonksiyon; test edilebilir)."""
    apps = list(cfg["apps"])
    image_to_app = cfg.get("container_image_to_app", {})
    open_keys = open_keys or set()
    st = {k: (r.status if hasattr(r, "status") else r["status"]) for k, r in results.items()}
    rec = {k: (r.records if hasattr(r, "records") else r["records"]) for k, r in results.items()}
    ivm = sp.interval_min
    # ---- http
    per = {a: {"n": 0.0, "t": 0.0, "err": 0.0} for a in apps}
    series: dict[str, dict[str, dict]] = {a: {} for a in apps}
    for r in rec.get("http", []):
        a = r.get("service")
        if a not in per:
            continue
        bins = _bins(r)
        ns, ts = r.get("n") or [], r.get("t") or []
        for i, t in enumerate(bins):
            n = num(ns[i]) if i < len(ns) else None
            if n is None:
                continue
            tt = num(ts[i]) if i < len(ts) else 0.0
            p = series[a].setdefault(iso(t), {"n": 0.0, "t": 0.0, "err": 0.0})
            p["n"] += n
            p["t"] += tt or 0.0
            per[a]["n"] += n
            per[a]["t"] += tt or 0.0
            if r.get("outcome") == "SERVER_ERROR":
                p["err"] += n
                per[a]["err"] += n
    # ---- spans
    tot = {r.get("svc"): r for r in rec.get("spans_total", [])}
    prev = {r.get("svc"): r for r in rec.get("spans_prev", [])}
    sser: dict[str, dict[str, dict]] = {a: {} for a in apps}
    for r in rec.get("spans_series", []):
        a = r.get("svc")
        if a in sser and r.get("t"):
            sser[a][iso(parse_ts(r["t"]))] = {"p95": num(r.get("p95")), "p99": num(r.get("p99")), "n": num(r.get("n"))}
    # ---- hikari
    pool: dict[str, dict] = {}
    pool_series: dict[str, dict[str, float]] = {a: {} for a in apps}
    for r in rec.get("hikari", []):
        a = r.get("service")
        if a not in pool_series:
            continue
        aa, mx, pe = r.get("a") or [], r.get("mx") or [], r.get("pe") or []
        for i, t in enumerate(_bins(r)):
            av, mv = (num(aa[i]) if i < len(aa) else None), (num(mx[i]) if i < len(mx) else None)
            if av is not None and mv:
                pool_series[a][iso(t)] = min(1.0, av / mv)
        vals = list(pool_series[a].values())
        pend = [num(x) for x in pe if num(x) is not None]
        pool[a] = {"max_ratio": max(vals) if vals else None, "last_ratio": vals[-1] if vals else None,
                   "pending_max": max(pend) if pend else None}
    # ---- db
    db_series, db = {}, {"status": "no_data", "last_ratio": None, "max_ratio": None, "used": None, "slots": None}
    for r in rec.get("db_conn", []):
        rr, used, slots = r.get("r") or [], r.get("used") or [], r.get("slots") or []
        for i, t in enumerate(_bins(r)):
            v = num(rr[i]) if i < len(rr) else None
            if v is not None:
                db_series[iso(t)] = v
        vals = list(db_series.values())
        if vals:
            uv = [num(x) for x in used if num(x) is not None]
            sv = [num(x) for x in slots if num(x) is not None]
            db = {"status": "ok", "last_ratio": vals[-1], "max_ratio": max(vals),
                  "used": uv[-1] if uv else None, "slots": sv[-1] if sv else None}
    # ---- container raporlayanlar
    reporting = set()
    for r in rec.get("containers", []):
        name = str(r.get("name") or "")
        for img, a in image_to_app.items():
            if name.startswith(img + ":") or name == img:
                if any(num(x) is not None for x in (r.get("u") or [])[-2:]):
                    reporting.add(a)
    containers_ok = st.get("containers") == "OK"
    thr = cfg["watch"]["thresholds"]
    min_req = cfg["watch"].get("min_requests", 5)
    out_apps = {}
    for a in apps:
        n, t, e = per[a]["n"], per[a]["t"], per[a]["err"]
        sp_t, sp_p = tot.get(a) or {}, prev.get(a) or {}
        p95, p95_prev = num(sp_t.get("p95")), num(sp_p.get("p95"))
        reasons, has = [], n > 0 or bool(sp_t)
        err_rate = e / n if n else None
        status = "healthy" if has else "unknown"
        if containers_ok and a not in reporting:
            status, reasons = "down", ["container metric'i gelmiyor"]
        elif n >= min_req and err_rate is not None and err_rate >= 0.5:
            status, reasons = "down", [f"error rate %{err_rate * 100:.0f}"]
        elif has:
            if n >= min_req and err_rate is not None and err_rate >= thr["error_rate"]["open"]:
                reasons.append(f"error rate %{err_rate * 100:.1f}")
            if p95 and p95_prev and num(sp_t.get("n") or 0) >= 30 and p95 >= thr["latency_ratio"]["open"] * p95_prev:
                reasons.append(f"p95 {p95:.0f} ms (önceki dönem {p95_prev:.0f} ms)")
            pr = (pool.get(a) or {}).get("last_ratio")
            if pr is not None and pr >= thr["pool_ratio"]["open"]:
                reasons.append(f"Connection Pool %{pr * 100:.0f}")
            if {k for k in open_keys if k.endswith(":" + a)}:
                reasons.append("açık watch koşulu: " + ", ".join(sorted(k for k in open_keys if k.endswith(":" + a))))
            if reasons:
                status = "degraded"
        pts = []
        for ts_ in sorted(set(series[a]) | set(sser[a]) | set(pool_series[a])):
            h, s_, pl = series[a].get(ts_), sser[a].get(ts_), pool_series[a].get(ts_)
            pts.append({"t": ts_,
                        "rpm": _r(h["n"] / ivm) if h else None,
                        "error_rate": _r(h["err"] / h["n"], 4) if h and h["n"] else None,
                        "mean_ms": _r(h["t"] / h["n"]) if h and h["n"] else None,
                        "p95_ms": s_["p95"] if s_ else None, "p99_ms": s_["p99"] if s_ else None,
                        "pool_ratio": _r(pl, 3) if pl is not None else None})
        out_apps[a] = {
            "status": status, "reasons": reasons,
            "throughput_rpm": _r(n / max((sp.end - sp.start).total_seconds() / 60, 1)) if n else (0.0 if has else None),
            "requests": int(n) if has else None,
            "error_rate": _r(err_rate, 4) if err_rate is not None else None,
            "mean_ms": _r(t / n) if n else None,
            "p50_ms": num(sp_t.get("p50")), "p95_ms": p95, "p99_ms": num(sp_t.get("p99")),
            "span_samples": int(num(sp_t.get("n")) or 0) if sp_t else 0,
            "p95_prev_ms": p95_prev, "pool": pool.get(a) or {"max_ratio": None, "last_ratio": None, "pending_max": None},
            "series": pts,
        }
    # ---- genel
    N = sum(per[a]["n"] for a in apps)
    T = sum(per[a]["t"] for a in apps)
    E = sum(per[a]["err"] for a in apps)
    counts: dict[str, int] = {}
    for a in out_apps.values():
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    p95s = [(x["p95_ms"], x["span_samples"]) for x in out_apps.values() if x["p95_ms"] is not None]
    p99s = [(x["p99_ms"], x["span_samples"]) for x in out_apps.values() if x["p99_ms"] is not None]
    overall = {
        "throughput_rpm": _r(N / max((sp.end - sp.start).total_seconds() / 60, 1)) if N else None,
        "error_rate": _r(E / N, 4) if N else None,
        "mean_ms": _r(T / N) if N else None,
        # uygulama p95/p99'larının en kötüsü (persentiller toplanamaz; ortalamaları yanıltıcı olur)
        "p95_ms_worst": max(p for p, _ in p95s) if p95s else None,
        "p99_ms_worst": max(p for p, _ in p99s) if p99s else None,
        "db_connection_ratio": db["last_ratio"], "db_connection_ratio_max": db["max_ratio"],
    }
    return {"range": sp.key, "start": iso(sp.start), "end": iso(sp.end), "interval_minutes": ivm,
            "sources": st, "apps": out_apps, "status_counts": counts, "overall": overall, "db": db,
            "db_series": [{"t": k, "ratio": _r(v, 4)} for k, v in sorted(db_series.items())]}


class TelemetryService:
    def __init__(self, rt):
        self.rt = rt
        self._cache: dict[str, tuple[float, dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def snapshot(self, range_key: str = "1h", *, open_keys: set[str] | None = None) -> dict[str, Any]:
        cfg = self.rt.agent.cfg
        sp = make_span(range_key, cfg["windows"]["ingest_lag_seconds"])
        key = f"{range_key}:{iso(sp.end)}"
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return self._with_keys(hit[1], open_keys)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
                return self._with_keys(hit[1], open_keys)
            try:
                gw = self.rt.gateway_factory()
                res = await asyncio.to_thread(gw.run_many, queries(sp, cfg))
                data = {"available": True, "raw": res}
            except McpUnavailable as e:
                data = {"available": False, "error": str(e)}
            self._cache = {k: v for k, v in self._cache.items() if time.monotonic() - v[0] < CACHE_TTL_S * 4}
            self._cache[key] = (time.monotonic(), {"span": sp, **data})
        return self._with_keys(self._cache[key][1], open_keys)

    def _with_keys(self, data: dict, open_keys: set[str] | None) -> dict:
        sp = data["span"]
        if not data["available"]:
            return {"available": False, "error": data["error"], "range": sp.key, "start": iso(sp.start),
                    "end": iso(sp.end), "apps": {}, "status_counts": {}, "overall": {}, "db": {"status": "no_data"},
                    "db_series": []}
        return {"available": True, **compute(data["raw"], sp, self.rt.agent.cfg, open_keys=open_keys)}
