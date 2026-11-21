"""Değerlendirme koşucusu.

  python eval/run_eval.py              # sentetik senaryolar (çevrimdışı)
  python eval/run_eval.py --real       # gerçek deney pencereleri, Dynatrace MCP üzerinden (Claude kapalı)

Etiketler (labels.yaml / real_labels.yaml) yalnız sonuç karşılaştırmasında okunur; agent'a girdi olarak verilmez.
Sonuç: eval/results/<tür>-<zaman>.json + ekrana tablo.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent / "tests"), str(HERE.parent / "src")]


def check(label: dict, r: dict) -> list[str]:
    """Rapor (JSON) ile etiketi karşılaştırır; başarısız kontrollerin listesini döndürür."""
    fails = []
    mechs = [m["id"] for m in r["mechanisms"]]
    c = r["causality"]
    if "primary" in label and c["primary_mechanism"] != label["primary"]:
        fails.append(f"primary {c['primary_mechanism']} != {label['primary']}")
    if label.get("sources") and c["source_apps"][: len(label["sources"])] != label["sources"]:
        fails.append(f"sources {c['source_apps']} != {label['sources']}")
    if label.get("affected") and not set(label["affected"]) <= set(c["affected_apps"]):
        fails.append(f"affected {c['affected_apps']} ⊉ {label['affected']}")
    for m in label.get("not_mechanisms", []):
        if m in mechs:
            fails.append(f"beklenmeyen mekanizma {m}")
    for m in label.get("mechanisms", []):
        if m not in mechs:
            fails.append(f"eksik mekanizma {m}")
    for a in label.get("not_impacted", []):
        if a in r["impacted"]:
            fails.append(f"{a} etkilenmiş görünmemeliydi: {r['impacted'][a]['reasons']}")
    s = r["risk"]["overall"]["score"]
    if "max_score" in label and (s is None or s >= label["max_score"]):
        fails.append(f"puan {s} ≥ {label['max_score']}")
    if "min_score" in label and (s is None or s < label["min_score"]):
        fails.append(f"puan {s} < {label['min_score']}")
    return fails


def run_synthetic(tmp: Path) -> list[dict]:
    from airlab_agent.config import Settings
    from airlab_agent.graph import run_analysis
    from scenarios import SCENARIOS
    from synth import FakeGateway, build, cfg, windows

    labels = yaml.safe_load((HERE / "labels.yaml").read_text(encoding="utf-8"))
    s = Settings(data_dir=tmp, cfg=cfg())
    out = []
    for name in SCENARIOS:
        ws = windows()
        base, win, kw = SCENARIOS[name]()
        gw = FakeGateway(build(base, win, ws, **kw))
        st = run_analysis(s, lambda: gw, ws[1].start, ws[1].end, use_llm=False, fixture=True, run_id=f"eval-{name}")
        r = json.loads(Path(st["report_paths"][0]).read_text(encoding="utf-8"))
        out.append({"case": name, "report": r["id"], "fails": check(labels[name], r), **_brief(r)})
    return out


def run_real(tmp: Path | None) -> list[dict]:
    from functools import partial

    from airlab_agent.config import load_settings
    from airlab_agent.graph import run_analysis
    from airlab_agent.mcp_gateway import McpGateway
    from airlab_agent.redact import register_secrets

    s = load_settings()
    register_secrets(s.secrets())
    if tmp:
        s.data_dir = tmp
    b = s.cfg["budgets"]
    gwf = partial(McpGateway, s.mcp_url, s.dt_token, timeout_s=b["query_timeout_seconds"], retries=b["query_retries"],
                  max_queries=b["max_queries_per_run"])
    wins = yaml.safe_load((HERE / "real_windows.yaml").read_text(encoding="utf-8"))
    labels = yaml.safe_load((HERE / "real_labels.yaml").read_text(encoding="utf-8"))
    out = []
    for name, w in wins.items():
        start = datetime.fromisoformat(w["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(w["to"].replace("Z", "+00:00"))
        bl = None
        if w.get("baseline_from"):
            bl = (datetime.fromisoformat(w["baseline_from"].replace("Z", "+00:00")),
                  datetime.fromisoformat(w["baseline_to"].replace("Z", "+00:00")))
        st = run_analysis(s, gwf, start, end, use_llm=False, mode="eval-real", baseline=bl)
        r = json.loads(Path(st["report_paths"][0]).read_text(encoding="utf-8"))
        out.append({"case": name, "report": r["id"], "fails": check(labels[name], r), **_brief(r)})
    return out


def _brief(r: dict) -> dict:
    return {"score": r["risk"]["overall"]["score"], "primary": r["causality"]["primary_mechanism"],
            "confidence": r["causality"]["confidence"], "sources": r["causality"]["source_apps"],
            "affected": r["causality"]["affected_apps"], "impacted": sorted(r["impacted"]),
            "coverage": f"{r['data_coverage']['signals_ok']}/{r['data_coverage']['signals_total']}"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--real", action="store_true")
    p.add_argument("--data-dir", help="raporların yazılacağı dizin (varsayılan: geçici)")
    a = p.parse_args()
    import tempfile
    tmp = Path(a.data_dir) if a.data_dir else Path(tempfile.mkdtemp(prefix="airlab-eval-"))
    rows = run_real(tmp) if a.real else run_synthetic(tmp)
    for r in rows:
        mark = "PASS" if not r["fails"] else "FAIL"
        print(f"{mark}  {r['case']:18} puan={r['score']!s:6} birincil={r['primary']!s:28} güven={r['confidence']!s:6} "
              f"kaynak={','.join(r['sources']) or '-':24} etkilenen={','.join(r['affected']) or '-':20} "
              f"kapsam={r['coverage']}")
        for f in r["fails"]:
            print(f"        - {f}")
    passed = sum(1 for r in rows if not r["fails"])
    print(f"\n{passed}/{len(rows)} senaryo geçti")
    res_dir = (tmp / "eval-results") if a.data_dir else HERE / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    kind = "real" if a.real else "synthetic"
    path = res_dir / f"{kind}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"kind": kind, "fixture": not a.real, "rows": rows}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"sonuç: {path}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
