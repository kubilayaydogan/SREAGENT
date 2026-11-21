"""Risk formülü: deterministik, bileşen bazlı, eksik veride null."""
import pytest

from airlab_agent.scoring import band, combine, ramp, score_all
from synth import cfg


def test_ramp_edges():
    assert ramp(None, [1, 2]) is None
    assert ramp(0.5, [1, 2]) == 0.0
    assert ramp(1.5, [1, 2]) == pytest.approx(0.5)
    assert ramp(9, [1, 2]) == 1.0


def test_combine_weighted_and_renormalised():
    c = cfg()
    comp = {"latency": 1.0, "errors": 0.0, "saturation": 0.0, "lock": 0.0, "resource": 0.0}
    assert combine(comp, c)["score"] == pytest.approx(30.0)
    comp["resource"] = None                                     # ölçülemeyen bileşen paydaya girmez
    r = combine(comp, c)
    assert r["score"] == pytest.approx(100 * 0.30 / 0.90, abs=0.1)
    assert r["coverage"] == pytest.approx(0.9) and r["missing"] == ["resource"]


def test_score_null_when_coverage_low():
    c = cfg()
    r = combine({"latency": None, "errors": None, "saturation": 0.5, "lock": None, "resource": 0.2}, c)
    assert r["score"] is None and r["band"] is None and r["coverage"] == pytest.approx(0.3)


def test_true_zero_is_not_missing():
    c = cfg()
    r = combine({"latency": 0.0, "errors": 0.0, "saturation": 0.0, "lock": 0.0, "resource": 0.0}, c)
    assert r["score"] == 0.0 and r["coverage"] == 1.0


def test_bands():
    c = cfg()
    assert [band(x, c) for x in (0, 24.9, 25, 50, 75, 100)] == ["düşük", "düşük", "orta", "yüksek", "kritik", "kritik"]
    assert band(None, c) is None


def test_weights_configurable():
    c = cfg()
    c["score"]["weights"] = {"latency": 1.0, "errors": 0.0, "saturation": 0.0, "lock": 0.0, "resource": 0.0}
    assert combine({"latency": 0.5, "errors": 1, "saturation": 1, "lock": 1, "resource": 1}, c)["score"] == 50.0


def test_latency_below_min_delta_is_zero():
    c = cfg()
    cur = {"http_mean_ms": 4.0, "req": 10, "err_rate": 0.0}
    base = {"http_mean_ms": 1.0}
    s = score_all({"apps": {"checkin-service": cur}, "db": {}, "hosts": {}},
                  {"apps": {"checkin-service": base}, "db": {}, "hosts": {}}, c)
    comp = s["apps"]["checkin-service"]["components"]
    assert comp["_latency_ratio"] == 4.0 and comp["latency"] == 0.0     # ×4 ama yalnız +3 ms


def test_overall_is_max_of_scored_apps_and_null_if_none():
    c = cfg()
    empty = {"apps": {}, "db": {}, "hosts": {}}
    s = score_all(empty, empty, c)
    assert s["overall"]["score"] is None and s["overall"]["scored_apps"] == 0
