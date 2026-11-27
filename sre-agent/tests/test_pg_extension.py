"""Dynatrace PostgreSQL extension (postgres.*) sinyalleri: özellik çıkarımı ve DB CPU nedeni ayrımı."""
from airlab_agent.attribution import alternatives, detect
from airlab_agent.features import assess_quality, extract
from airlab_agent.signals import CATALOG, REQUIRED, WATCH_SIGNALS, build_queries
from synth import NORMAL, build, cfg, deep, windows

HOT = {"hosts": {"db-vm": 92.0}}


def run(base, win, **kw):
    c, ws = cfg(), windows()
    res = build(base, win, ws, **kw)
    F = extract(res, ws, c)
    q = assess_quality(res, ws[1], c)
    a = detect(F["window"], F["baseline"], c, {k: r.evidence_id for k, r in res.items() if r.status == "OK"})
    return F, q, a


def cause(a):
    return next(m for m in a["mechanisms"] if m["id"] == "db_cpu")["cause"]


def test_signals_are_supporting_only_and_queries_use_extension_metrics():
    assert {"pg_server", "pg_io"} <= set(CATALOG)
    assert not {"pg_server", "pg_io"} & (set(REQUIRED) | set(WATCH_SIGNALS))     # extension yoksa analiz yine çalışır
    q = dict(build_queries(windows(), cfg(), ["pg_server", "pg_io"]))
    assert "postgres.active_time.count" in q["pg_server"] and "postgres.checkpoints_req" not in q["pg_server"]
    assert q["pg_server"].count("=sum(") <= 10                                    # DQL: timeseries başına en fazla 10
    assert "by:{backend_type}" in q["pg_io"] and "interval:1m" in q["pg_io"]


def test_features_are_per_minute_and_grouped_by_backend_type():
    F, q, _ = run(NORMAL, deep(NORMAL, pg={"active_ms": 9000.0, "autovac_blocks": 40.0}))
    b, w = F["baseline"]["db"], F["window"]["db"]
    assert (b["pg_points"], w["pg_points"]) == (15, 5)
    assert b["pg_active_ms_per_min"] == 2000.0 and w["pg_active_ms_per_min"] == 9000.0
    assert w["pg_tup_write_per_min"] == 100.0 and w["pg_autovacuum_blocks_per_min"] == 40.0
    assert w["pg_client_blocks_per_min"] == 5.0 and w["pg_checkpoint_blocks_per_min"] == 80.0
    assert q["pg_server"]["status"] == "OK" and q["pg_io"]["status"] == "OK"


def test_db_cpu_with_rising_query_time_is_query_load():
    _, _, a = run(NORMAL, deep(NORMAL, pg={"active_ms": 30000.0}, **HOT))
    c = cause(a)
    assert c["kind"] == "query_load" and c["query_active_ms_per_min"] == [2000.0, 30000.0]
    assert not [x for x in alternatives(a, {"spans": {"status": "OK"}}) if x.startswith("DB CPU")]


def test_db_cpu_with_autovacuum_and_flat_query_time_is_maintenance():
    _, _, a = run(deep(NORMAL, pg={"autovac_blocks": 700.0}), deep(NORMAL, pg={"autovac_blocks": 90000.0}, **HOT))
    c = cause(a)
    assert c["kind"] == "maintenance" and c["maintenance_signals"] == ["autovacuum buffer activity"]
    alt = alternatives(a, {"spans": {"status": "OK"}})
    assert any("maintenance" in x and "autovacuum buffer activity" in x for x in alt)


def test_heavy_checkpoint_writes_count_as_maintenance_and_both_is_mixed():
    _, _, a = run(NORMAL, deep(NORMAL, pg={"ckpt_blocks": 6000.0, "active_ms": 30000.0}, **HOT))
    c = cause(a)
    assert c["kind"] == "mixed" and c["maintenance_signals"] == ["checkpoint write I/O"]
    assert c["checkpoint_blocks_per_min"] == [80.0, 6000.0]


def test_routine_checkpoint_and_autovacuum_are_not_maintenance():
    # boşta lab'de ölçülen büyüklükler: checkpoint ~400 blok, rutin autovacuum ~1200 buffer hit/dk
    _, _, a = run(NORMAL, deep(NORMAL, pg={"ckpt_blocks": 400.0, "autovac_blocks": 1200.0}, **HOT))
    assert cause(a)["kind"] == "undetermined"


def test_no_rise_in_either_is_undetermined_not_guessed():
    _, _, a = run(NORMAL, deep(NORMAL, **HOT))
    assert cause(a)["kind"] == "undetermined"
    assert any("açıklanamadı" in x for x in alternatives(a, {"spans": {"status": "OK"}}))


def test_missing_extension_data_is_unknown_and_never_zero():
    for base, win, where in ((NORMAL, deep(NORMAL, pg=None, **HOT), "incelenen aralıkta"),
                             (deep(NORMAL, pg=None), deep(NORMAL, **HOT), "önceki dönemde")):
        F, _, a = run(base, win)
        c = cause(a)
        assert c["kind"] == "unknown" and where in c["summary"]
        assert any("ayrım yapılamadı" in x for x in alternatives(a, {"spans": {"status": "OK"}}))
    F, q, a = run(deep(NORMAL, pg=None), deep(NORMAL, pg=None, **HOT))
    assert "pg_active_ms_per_min" not in F["window"]["db"] and "pg_points" not in F["window"]["db"]
    assert q["pg_server"]["status"] == "EMPTY" and cause(a)["kind"] == "unknown"


def test_lock_wait_caveat_when_row_lock_is_also_active():
    from airlab_agent.attribution import db_cpu_cause
    m = cfg()["mechanisms"]
    base, cur = {"pg_points": 15, "pg_active_ms_per_min": 2000.0}, {"pg_points": 5, "pg_active_ms_per_min": 60000.0}
    assert "caveat" in db_cpu_cause(cur, base, m, lock_active=True)
    assert "caveat" not in db_cpu_cause(cur, base, m, lock_active=False)
