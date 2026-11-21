"""Senaryo değerlendirmesi: etiketler (eval/labels.yaml) yalnız burada okunur, agent girdisine girmez."""
from pathlib import Path

import pytest
import yaml

from airlab_agent.attribution import detect
from airlab_agent.features import assess_quality, extract
from airlab_agent.scoring import score_all
from scenarios import SCENARIOS
from synth import build, cfg, windows

LABELS = yaml.safe_load((Path(__file__).parent.parent / "eval" / "labels.yaml").read_text(encoding="utf-8"))


def run(name):
    c, ws = cfg(), windows()
    base, win, kw = SCENARIOS[name]()
    res = build(base, win, ws, **kw)
    F = extract(res, ws, c)
    q = assess_quality(res, ws[1], c)
    s = score_all(F["window"], F["baseline"], c)
    a = detect(F["window"], F["baseline"], c, {k: r.evidence_id for k, r in res.items() if r.status == "OK"})
    return F, q, s, a


def test_labels_and_scenarios_aligned():
    assert set(LABELS) == set(SCENARIOS)


@pytest.mark.parametrize("name", sorted(LABELS))
def test_scenario(name):
    F, q, s, a = run(name)
    L = LABELS[name]
    mechs = [m["id"] for m in a["mechanisms"]]
    primary = next((m for m in a["mechanisms"] if not m.get("secondary_to")), None)
    if "primary" in L:
        assert (primary["id"] if primary else None) == L["primary"], (mechs, a["impacted"])
    if L.get("sources"):
        assert primary["source_apps"][: len(L["sources"])] == L["sources"]
    if L.get("affected"):
        assert set(L["affected"]) <= set(primary["affected_apps"])
    for m in L.get("secondary", []):
        assert any(x["id"] == m and x.get("secondary_to") for x in a["mechanisms"])
    for m in L.get("not_mechanisms", []):
        assert m not in mechs
    for m in L.get("mechanisms", []):
        assert m in mechs
    if L.get("sources_any"):
        assert set(L["sources_any"]) <= {x for m in a["mechanisms"] for x in m["source_apps"]}
    for app in L.get("not_impacted", []):
        assert app not in a["impacted"]
    if "max_score" in L:
        assert s["overall"]["score"] is not None and s["overall"]["score"] < L["max_score"]
    if "min_score" in L:
        assert s["overall"]["score"] >= L["min_score"]
    for k in L.get("quality_not_ok", []):
        assert q[k]["status"] != "OK"
    for k in L.get("quality_stale", []):
        assert q[k]["status"] == "STALE"
    if L.get("lock_unknown"):
        # collector yokken kilit 0 DEĞİL, bilinmiyor
        assert F["window"]["db"]["edges_total"] is None
        assert all(F["window"]["apps"][x]["lock_waiters_avg"] is None for x in cfg()["apps"])
        assert all(v["components"]["lock"] is None for v in s["apps"].values())


def test_chaos_labels_never_reach_features():
    F, *_ = run("row_lock")
    text = repr(F)
    assert "exp-LEAK" not in text and "hold-inventory-lock" not in text


def test_true_zero_lock_when_collector_up():
    F, q, s, a = run("normal")
    assert F["window"]["db"]["edges_total"] == 0
    assert all(F["window"]["apps"][x]["lock_waiters_avg"] == 0.0 for x in cfg()["apps"])
    assert q["edges"]["status"] == "EMPTY"          # sorgu boş döndü ama collector çalıştığı için değer gerçek 0


def test_app_vm_cpu_separates_agent_itself():
    c, ws = cfg(), windows()
    base, win, kw = SCENARIOS["normal"]()
    import copy
    win = copy.deepcopy(win)
    win["hosts"]["app-vm"] = 95.0
    win["tooling_cores"]["sre-agent"] = 1.5
    res = build(base, win, ws)
    F = extract(res, ws, c)
    a = detect(F["window"], F["baseline"], c, {})
    m = next(x for x in a["mechanisms"] if x["id"] == "app_vm_cpu")
    assert m["source_apps"][0] == "sre-agent" and m["self_is_source"] is True
