"""Watch: 3 YENİ pencere onayı, ingest gecikmesi, yeniden başlatmada tekrar sayım yok, hysteresis, cooldown, recovery,
dedup, veri boşluğu, MCP kesintisi. Normal döngüde analiz/LLM çağrısı yok."""
from datetime import datetime, timedelta, timezone

import pytest

from airlab_agent.config import Settings
from airlab_agent.watch import StateStore, Watcher, step, verdict
from scenarios import SCENARIOS
from synth import TimelineGateway, cfg

T = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    s = Settings(data_dir=tmp_path, cfg=cfg())
    s.ensure_dirs()
    normal = SCENARIOS["normal"]()[1]
    lock = SCENARIOS["row_lock"]()[1]
    bad = {"from": None, "to": None}

    def timeline(t):
        if bad["from"] and bad["from"] <= t and (bad["to"] is None or t < bad["to"]):
            return lock
        return normal
    gw = TimelineGateway(timeline)
    now = {"t": T}
    calls = []

    def analyze(**kw):
        calls.append(kw)
        return {"run_id": kw["run_id"]}
    w = Watcher(s, lambda: gw, analyze, StateStore(s.state_db), clock=lambda: now["t"])
    return s, w, gw, now, bad, calls


def advance(w, now, minutes):
    out = []
    for _ in range(minutes):
        now["t"] += timedelta(minutes=1)
        out.append(w.tick())
    return out


def next_window_start(s, now):
    lag = s.cfg["windows"]["ingest_lag_seconds"]
    return (now["t"] - timedelta(seconds=lag)).replace(second=0, microsecond=0)


def event_kinds(w):
    return [row[0] for row in w.store.db.execute("SELECT kind FROM events")]


def test_verdict_and_hysteresis():
    c = cfg()
    assert verdict("pool:x", 0.95, [], c)[0] == "bad"
    assert verdict("pool:x", 0.8, [], c)[0] == "hold"              # clear < değer < open
    assert verdict("pool:x", 0.5, [], c)[0] == "clear"
    assert verdict("pool:x", None, [], c)[0] == "gap"
    assert verdict("latency:x", 100.0, [20.0] * 2, c)[0] == "gap"  # baseline yetersiz
    assert verdict("latency:x", 100.0, [20.0] * 10, c)[:2] == ("bad", 5.0)


def test_step_confirm_hysteresis_cooldown():
    c = cfg()
    st = {"state": "ok"}
    for _ in range(2):
        st, tr = step(st, "bad", T, c)
        assert tr is None
    st, tr = step(st, "bad", T, c)
    assert tr == "open"
    for v in ("clear", "hold", "clear", "clear"):                   # hold sayacı sıfırlar
        st, tr = step(st, v, T, c)
        assert tr is None
    st, tr = step(st, "clear", T, c)
    assert tr == "recover" and st["cooldown_until"]
    for _ in range(5):                                              # cooldown içinde yeniden açılmaz
        st, tr = step(st, "bad", T + timedelta(minutes=1), c)
        assert tr != "open"
    st, tr = step(st, "bad", T + timedelta(minutes=11), c)
    assert tr == "open"


def test_gap_does_not_count_as_zero():
    c = cfg()
    st, tr = step({"state": "ok", "bad": 2}, "gap", T, c)
    assert st["bad"] == 2 and tr is None
    for _ in range(c["watch"]["gap_windows_event"] - 1):
        st, tr = step(st, "gap", T, c)
    assert tr == "gap"


def test_watch_end_to_end(setup):
    s, w, gw, now, bad, calls = setup
    assert w.tick() == {"evaluated": 0, "history_filled": 15}       # ilk tur: yalnız baseline geçmişi
    advance(w, now, 3)
    assert not calls and w.store.open_incident() is None
    bad["from"] = next_window_start(s, now)
    advance(w, now, 2)
    assert w.store.open_incident() is None                          # 2 pencere: henüz onay yok
    advance(w, now, 1)
    inc = w.store.open_incident()
    assert inc is not None and len(calls) == 1                      # 3. YENİ pencerede olay + tek analiz
    assert calls[0]["mode"] == "watch-incident"
    advance(w, now, 3)
    assert len(calls) == 1                                          # dedup: aynı olay için yeni analiz yok
    assert w.store.open_incident()["id"] == inc["id"]

    bad["to"] = next_window_start(s, now)
    advance(w, now, 2)
    assert w.store.open_incident() is not None                      # 2 temiz pencere yetmez
    advance(w, now, 2)
    assert w.store.open_incident() is None                          # recovery
    assert calls[-1]["mode"] == "watch-recovery" and calls[-1]["use_llm"] is False
    kinds = event_kinds(w)
    assert "incident_open" in kinds and "incident_recovered" in kinds


def test_restart_does_not_recount_windows(setup):
    s, w, gw, now, bad, calls = setup
    w.tick()
    bad["from"] = next_window_start(s, now)
    advance(w, now, 2)                                              # 2 kötü pencere değerlendirildi
    w2 = Watcher(s, lambda: gw, w.analyze_fn, StateStore(s.state_db), clock=lambda: now["t"])
    assert w2.tick()["evaluated"] == 0                              # aynı dakika: yeni pencere yok
    assert w2.store.open_incident() is None
    advance(w2, now, 1)                                             # 3. yeni pencere → olay
    assert w2.store.open_incident() is not None


def test_mcp_outage_does_not_advance_windows(setup):
    s, w, gw, now, bad, calls = setup
    w.tick()
    last = w.store.get("last_end")
    gw.down = True
    r = advance(w, now, 3)
    assert all(x.get("mcp") == "down" for x in r) and w.store.get("last_end") == last
    gw.down = False
    r = advance(w, now, 1)
    assert r[0]["evaluated"] == 4                                   # kaçırılan pencereler sonra değerlendirildi
    kinds = event_kinds(w)
    assert kinds.count("mcp_unavailable") == 1 and "mcp_restored" in kinds


def test_normal_loop_never_calls_analysis(setup):
    s, w, gw, now, bad, calls = setup
    w.tick()
    advance(w, now, 10)
    assert calls == []
