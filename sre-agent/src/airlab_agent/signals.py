"""Sinyal kataloğu: DQL üretimi ve MCP sonuçlarının pencere/varlık bazında normalizasyonu.

Tüm sorgu biçimleri 2026-11-21'de tenant `mfi74785` üzerinde MCP `execute-dql` ile doğrulandı
(docs/lab/telemetry-contract.md §J). Tenant Smartscape on Grail: `dt.entity.*` yok → `dt.smartscape.*` + getNodeName().
`dt.service.request.*` controller bazında bölündüğü için servis bazında Micrometer (`service` boyutu) ve span'lar
(`spring.application.name`) kullanılır.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class Window:
    name: str                 # baseline | window | after | w<idx>
    start: datetime
    end: datetime

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def iso(self) -> dict:
        return {"name": self.name, "start": iso(self.start), "end": iso(self.end)}


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if "." in s:                                   # nanosaniye → mikrosaniye
        head, frac = s.split(".", 1)
        tz = "+" + frac.split("+", 1)[1] if "+" in frac else ""
        digits = "".join(ch for ch in frac.split("+")[0] if ch.isdigit())[:6]
        s = f"{head}.{digits}{tz}"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rng(ws: list[Window]) -> str:
    return f'from:"{iso(min(w.start for w in ws))}", to:"{iso(max(w.end for w in ws))}"'


def _bucket(ws: list[Window], field: str) -> str:
    """fetch sorguları için: kaydı zaman alanına göre pencere adına eşleyen iç içe if()."""
    ordered = sorted(ws, key=lambda w: w.start)
    expr = '"_out"'
    for w in reversed(ordered):
        expr = f'if({field} < toTimestamp("{iso(w.end)}"), "{w.name}", else:{expr})'
    return f'fieldsAdd w = if({field} < toTimestamp("{iso(ordered[0].start)}"), "_out", else:{expr})'


def _excl_paths(prefixes: list[str]) -> str:
    return "".join(f' and not startsWith(url.path, "{p}")' for p in prefixes)


# ---------------------------------------------------------------- sorgu üreticileri (pencereler → DQL)
def q_http(ws, cfg):
    return (f"timeseries n=sum(airlab.app.http.server.requests, rollup: count), "
            f"t=sum(airlab.app.http.server.requests, rollup: sum), mx=max(airlab.app.http.server.requests, rollup: max), "
            f"{_rng(ws)}, interval:1m, by:{{service, outcome}}{_uri_filter(cfg)}")


def _uri_filter(cfg) -> str:
    """Micrometer `uri` boyutu üzerinden bakım/kaos uçlarını (/actuator, /lab) dışlar — span filtresiyle aynı kural."""
    conds = " and ".join(f'not startsWith(uri, "{p}")' for p in cfg["latency_exclude_path_prefixes"])
    return f", filter:{{{conds}}}" if conds else ""


def q_spans(ws, cfg):
    return (f'fetch spans, {_rng(ws)} | filter span.kind == "server" and isNotNull(spring.application.name)'
            f'{_excl_paths(cfg["latency_exclude_path_prefixes"])} | {_bucket(ws, "start_time")} '
            f'| summarize n=count(), p50=percentile(duration, 50)/1000000, p95=percentile(duration, 95)/1000000, '
            f'by:{{w, svc=spring.application.name}}')


def q_hikari(ws, cfg):
    return (f"timeseries a=max(airlab.app.hikaricp.connections.active), pe=max(airlab.app.hikaricp.connections.pending), "
            f"mx=max(airlab.app.hikaricp.connections.max), to=sum(airlab.app.hikaricp.connections.timeout), "
            f"{_rng(ws)}, interval:1m, by:{{service}}")


def q_jvm_gc(ws, cfg):
    return (f"timeseries gc=sum(airlab.app.jvm.gc.pause, rollup: count), gcmax=max(airlab.app.jvm.gc.pause, rollup: max), "
            f"ov=max(airlab.app.jvm.gc.overhead), {_rng(ws)}, interval:1m, by:{{service}}")


def q_jvm_heap(ws, cfg):
    return (f'timeseries used=max(airlab.app.jvm.memory.used), {_rng(ws)}, interval:1m, by:{{service, area}} '
            f'| filter area == "heap"')


def q_hosts(ws, cfg):
    return (f"timeseries cpu=avg(dt.host.cpu.usage), io=avg(dt.host.cpu.iowait), mem=avg(dt.host.memory.usage), "
            f"{_rng(ws)}, interval:1m, by:{{dt.smartscape.host}} | fieldsAdd host=getNodeName(dt.smartscape.host)")


def q_containers(ws, cfg):
    return (f"timeseries u=sum(dt.containers.cpu.usage_user_time), s=sum(dt.containers.cpu.usage_system_time), "
            f"rss=max(dt.containers.memory.resident_set_bytes), {_rng(ws)}, interval:1m, "
            f"by:{{dt.smartscape.container}} | fieldsAdd name=getNodeName(dt.smartscape.container)")


def q_db_conn(ws, cfg):
    return (f"timeseries r=max(airlab.pg.connections.used_ratio_normal), used=max(airlab.pg.connections.used), "
            f"slots=max(airlab.pg.connections.normal_slots), {_rng(ws)}, interval:1m")


def q_db_sessions(ws, cfg):
    # db.role: application_name'i bilinen servis adı olmayan oturumlar (havuz dışı bağlantılar) role üzerinden sahiplenilir
    return f"timeseries s=avg(airlab.pg.sessions), {_rng(ws)}, interval:1m, by:{{app, db.role, state, wait_type}}"


def q_lock_waiters(ws, cfg):
    return (f"timeseries wt=max(airlab.pg.lock.waiters), wmax=max(airlab.pg.lock.wait_max_seconds), {_rng(ws)}, "
            f"interval:1m, by:{{waiter.app, blocker.app}}")


def q_collector(ws, cfg):
    """Collector sağlığı + kilit toplamı. waiters_total yalnız sorgu BAŞARILI olunca (gerçek 0 dahil) yazılır → kapsam ölçüsü."""
    return (f"timeseries up=min(airlab.pg.collector.up), tot=max(airlab.pg.lock.waiters_total), "
            f"tavg=avg(airlab.pg.lock.waiters_total), {_rng(ws)}, interval:1m")


def q_edges(ws, cfg):
    return (f'fetch logs, {_rng(ws)} | filter airlab.event == "airlab.pg.blocking_edge" | {_bucket(ws, "timestamp")} '
            f'| summarize n=count(), wait_max=max(toDouble(waiter.wait_seconds)), '
            f'by:{{w, waiter_app=waiter.app, blocker_app=blocker.app, wait_type=waiter.wait_event_type, '
            f'wait_event=waiter.wait_event, '
            f'waiter_op=waiter.op, blocker_op=blocker.op, blocker_state=blocker.state, '
            f'blocker_wait=blocker.wait_event_type}}')


def q_pg_lock_logs(ws, cfg):
    return (f'fetch logs, {_rng(ws)} | filter host.name == "{cfg["hosts"]["db"]}" and contains(content, "still waiting for") '
            f'| {_bucket(ws, "timestamp")} | summarize n=count(), by:{{w}}')


def q_pg_error_logs(ws, cfg):
    """PostgreSQL logundaki deadlock ve serialization failure satırları. Rol, log_line_prefix'teki `user=` alanından
    ayrıştırılır (2026-11-27 gerçek log: `... user=svc_checkin db=airlab app=... ERROR:  could not serialize access`)."""
    return (f'fetch logs, {_rng(ws)} | filter host.name == "{cfg["hosts"]["db"]}" '
            f'and (contains(content, "deadlock detected") or contains(content, "could not serialize access")) '
            f'| parse content, "LD \'user=\' LD:role \' db=\'" | {_bucket(ws, "timestamp")} '
            f'| summarize dl=countIf(contains(content, "deadlock detected")), '
            f'sr=countIf(contains(content, "could not serialize access")), by:{{w, role}}')


def q_db_stats(ws, cfg):
    return (f"timeseries dl=sum(airlab.pg.db.deadlocks), rb=sum(airlab.pg.db.xact_rollback), "
            f"cm=sum(airlab.pg.db.xact_commit), tmp=sum(airlab.pg.db.temp_bytes), rd=sum(airlab.pg.db.blks_read), "
            f"{_rng(ws)}, interval:1m")


def q_statements(ws, cfg):
    return (f"timeseries et=sum(airlab.pg.statements.exec_time_ms), calls=sum(airlab.pg.statements.calls), "
            f"{_rng(ws)}, interval:1m, by:{{db.role}}")


def q_pg_server(ws, cfg):
    """Dynatrace PostgreSQL extension (postgres.*), sunucu geneli: query active time, tuple trafiği, checkpoint ve WAL.
    Tek timeseries komutu en fazla 10 aggregation kabul ediyor (11.'de DQL-SYNTAX-ERROR; tenant'ta 2026-11-27'de görüldü).
    `postgres.checkpoints_{req,timed}.count` SORGULANMAZ: PG17'de extension bu sayaçları hep 0 yazıyor (elle CHECKPOINT
    ile doğrulandı, 2026-11-27); checkpoint aktivitesi `postgres.buffers_checkpoint.count` (yazılan blok) ile okunur.
    `.count` metric'leri extension'ın her örneklemede yazdığı delta'dır; 1 dk kovada toplanır."""
    return (f"timeseries act=sum(postgres.active_time.count), ret=sum(postgres.tup_returned.count), "
            f"ins=sum(postgres.tup_inserted.count), upd=sum(postgres.tup_updated.count), "
            f"dlt=sum(postgres.tup_deleted.count), "
            f"cw=sum(postgres.checkpoint_write_time.count), cs=sum(postgres.checkpoint_sync_time.count), "
            f"wal=sum(postgres.wal_bytes.count), bc=sum(postgres.buffers_checkpoint.count), {_rng(ws)}, interval:1m")


def q_pg_io(ws, cfg):
    """pg_stat_io (extension): backend tipine göre blok I/O — autovacuum worker / client backend ayrımı.
    Sonuç kümesini ilk metric (reads) belirliyor: okuma satırı olmayan backend tipleri (checkpointer, background
    writer) bu sorguda DÖNMEZ (tenant'ta 2026-11-27'de görüldü); checkpoint için pg_server.bc kullanılır."""
    return (f"timeseries rd=sum(postgres.io.reads.count), wr=sum(postgres.io.writes.count), "
            f"ext=sum(postgres.io.extends.count), hit=sum(postgres.io.hits.count), "
            f"rt=sum(postgres.io.read_time.count), "
            f"wt=sum(postgres.io.write_time.count), {_rng(ws)}, interval:1m, by:{{backend_type}}")


CATALOG: dict[str, Callable] = {
    "http": q_http, "spans": q_spans, "hikari": q_hikari, "jvm_gc": q_jvm_gc, "jvm_heap": q_jvm_heap,
    "hosts": q_hosts, "containers": q_containers, "db_conn": q_db_conn, "db_sessions": q_db_sessions,
    "lock_waiters": q_lock_waiters, "collector": q_collector, "edges": q_edges, "pg_lock_logs": q_pg_lock_logs,
    "db_stats": q_db_stats, "statements": q_statements, "pg_server": q_pg_server, "pg_io": q_pg_io,
    "pg_error_logs": q_pg_error_logs,
}
# Analizin zorunlu sinyalleri (doctor bunları kontrol eder). Diğerleri destekleyicidir.
REQUIRED = ["http", "spans", "hikari", "hosts", "db_conn", "db_sessions", "collector", "edges"]
WATCH_SIGNALS = ["http", "spans", "hikari", "hosts", "db_conn", "collector"]


def build_queries(ws: list[Window], cfg: dict, keys: list[str] | None = None) -> list[tuple[str, str]]:
    return [(k, CATALOG[k](ws, cfg)) for k in (keys or list(CATALOG))]


# ---------------------------------------------------------------- normalizasyon
def _bins(rec: dict) -> list[datetime]:
    tf, iv = rec.get("timeframe") or {}, num(rec.get("interval"))
    if not tf or not iv:
        return []
    start, step = parse_ts(tf["start"]), timedelta(microseconds=iv / 1000)
    n = max((len(v) for v in rec.values() if isinstance(v, list)), default=0)
    return [start + i * step for i in range(n)]


def _window_of(t: datetime, ws: list[Window]) -> str | None:
    for w in ws:
        if w.start <= t < w.end:
            return w.name
    return None


def split_series(rec: dict, ws: list[Window], fields: list[str]) -> dict[str, dict[str, list[float]]]:
    """Zaman serisi kaydını pencere → alan → [dolu değerler] olarak böler. None değerler (boşluk) atılır."""
    out: dict[str, dict[str, list[float]]] = {w.name: {f: [] for f in fields} for w in ws}
    for i, t in enumerate(_bins(rec)):
        wn = _window_of(t, ws)
        if wn is None:
            continue
        for f in fields:
            arr = rec.get(f) or []
            v = num(arr[i]) if i < len(arr) else None
            if v is not None:
                out[wn][f].append(v)
    return out


def series_timeline(rec: dict, field: str) -> list[tuple[str, float | None]]:
    arr = rec.get(field) or []
    return [(iso(t), num(arr[i]) if i < len(arr) else None) for i, t in enumerate(_bins(rec))]


def last_point(rec: dict, fields: list[str]) -> datetime | None:
    bins, last = _bins(rec), None
    for f in fields:
        arr = rec.get(f) or []
        for i in range(min(len(arr), len(bins)) - 1, -1, -1):
            if arr[i] is not None:
                last = max(last, bins[i]) if last else bins[i]
                break
    return last


def _agg(vals: list[float], how: str) -> float | None:
    if not vals:
        return None
    return {"max": max(vals), "sum": sum(vals), "avg": sum(vals) / len(vals), "min": min(vals)}[how]


# ---------------------------------------------------------------- ek kanıt sorguları (mekanizmaya göre, bütçe dahilinde)
def q_edges_detail(ws, cfg):
    target = [w for w in ws if w.name == "window"] or ws
    return (f'fetch logs, {_rng(target)} | filter airlab.event == "airlab.pg.blocking_edge" '
            f'| fields timestamp, content, waiter.app, blocker.app, waiter.wait_event_type, waiter.wait_event, '
            f'waiter.wait_seconds, blocker.state, blocker.wait_event_type, blocker.xact_age_seconds, waiter.op, blocker.op '
            f'| sort toDouble(waiter.wait_seconds) desc | limit 15')


def q_top_statements(ws, cfg):
    target = [w for w in ws if w.name == "window"] or ws
    return (f'fetch logs, {_rng(target)} | filter airlab.event == "airlab.pg.top_statement" '
            f'| summarize exec_ms=sum(toDouble(exec_ms_delta)), calls=sum(toDouble(calls_delta)), '
            f'by:{{db.role, query_id}} | sort exec_ms desc | limit 10')


EXTRA: dict[str, Callable] = {"edges_detail": q_edges_detail, "top_statements": q_top_statements}
