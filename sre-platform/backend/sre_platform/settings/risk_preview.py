"""Risk scoring önizlemesi: SABİT test fixture'ları, mevcut deterministik puanlayıcı (airlab_agent.scoring) ile
hem geçerli hem taslak yapılandırmada puanlanır. Geçmiş raporlar yeniden hesaplanmaz; bu yalnız bir önizlemedir.

Fixture değerleri gerçek bir ortamdan gelmez; okunabilir, sabit senaryolardır (aynı girdi → aynı sonuç).
"""
from __future__ import annotations

import copy
from typing import Any

from airlab_agent.scoring import app_components, combine

BASE = {"http_mean_ms": 20.0, "span_n": 400, "span_p95_ms": 60.0, "req": 3000, "err_rate": 0.001}

FIXTURES: list[dict[str, Any]] = [
    {"id": "healthy", "title": "Sağlıklı servis",
     "note": "Baseline'a yakın latency, düşük error rate, boşta Connection Pool",
     "cur": {**BASE, "span_p95_ms": 66.0, "http_mean_ms": 21.0, "pool_ratio_avg": 0.2, "lock_waiters_avg": 0.0},
     "db": {"conn_ratio": 0.15}, "cpu": 35.0},
    {"id": "lock_contention", "title": "Lock contention",
     "note": "Row lock wait: p95 ×4.5, bekleyen session ortalaması 2.4, havuz dolmaya yakın",
     "cur": {**BASE, "span_p95_ms": 270.0, "http_mean_ms": 140.0, "pool_ratio_avg": 0.9, "lock_waiters_avg": 2.4,
             "err_rate": 0.004},
     "db": {"conn_ratio": 0.55}, "cpu": 48.0},
    {"id": "pool_saturation", "title": "Connection Pool saturation",
     "note": "Havuz dolu ve timeout var; 5xx %3",
     "cur": {**BASE, "span_p95_ms": 150.0, "http_mean_ms": 75.0, "pool_ratio_avg": 1.0, "pool_timeouts": 12,
             "err_rate": 0.03, "lock_waiters_avg": 0.1},
     "db": {"conn_ratio": 0.7}, "cpu": 62.0},
    {"id": "missing_data", "title": "Eksik veri",
     "note": "Trace/HTTP ve host metric yok: risk score 0 değil, 'hesaplanamadı' olmalı",
     "cur": {"req": None, "pool_ratio_avg": 0.4, "lock_waiters_avg": None},
     "db": {}, "cpu": None},
]


def _cfg(base_cfg: dict, risk: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    cfg["score"].update(weights=dict(risk["weights"]), bands=dict(risk["bands"]),
                        **{k: list(v) for k, v in risk["ramps"].items()})
    cfg["quality"]["min_score_coverage"] = risk["min_score_coverage"]
    return cfg


def score(base_cfg: dict, risk: dict) -> list[dict]:
    cfg = _cfg(base_cfg, risk)
    host = cfg["hosts"]["app"]
    out = []
    for f in FIXTURES:
        base = BASE if f["id"] != "missing_data" else {}
        comp = app_components(f["cur"], base, f["db"], {host: {"cpu": f["cpu"]}} if f["cpu"] is not None else {}, cfg)
        res = combine(comp, cfg)
        out.append({"id": f["id"], "title": f["title"], "note": f["note"], "score": res["score"], "band": res["band"],
                    "coverage": res["coverage"], "missing": res["missing"],
                    "components": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in comp.items()
                                   if not k.startswith("_")}})
    return out


def preview(base_cfg: dict, current: dict, draft: dict) -> dict:
    cur, new = score(base_cfg, current), score(base_cfg, draft)
    rows = []
    for a, b in zip(cur, new):
        delta = None if a["score"] is None or b["score"] is None else round(b["score"] - a["score"], 1)
        rows.append({**{k: a[k] for k in ("id", "title", "note")}, "current": a, "draft": b, "delta": delta})
    return {"fixtures": rows, "note": "Sabit fixture'lar; geçmiş investigation skorları yeniden hesaplanmaz."}
