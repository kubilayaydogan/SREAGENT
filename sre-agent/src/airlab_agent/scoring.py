"""Deterministik risk puanı (0–100). LLM puana dokunmaz.

Bileşenler (her biri 0..1, parçalı doğrusal):
  latency    p95 (span, yeterli örnek varsa) ya da Micrometer ortalama süresinin baseline'a oranı
  errors     pencere içi 5xx oranı (mutlak)
  saturation max(Hikari aktif/max, DB normal bağlantı oranı); havuz zaman aşımı → 1.0, bekleyen istek → ≥0.8
  lock       uygulamanın bekleyen oturum ortalaması (collector başarılıysa 0 gerçek sıfırdır)
  resource   APP_VM CPU (uygulamalar aynı host'u paylaşır)
Ölçülemeyen bileşen None'dır ve paydaya girmez. Ölçülen ağırlık oranı < min_score_coverage → puan None.
Genel puan = uygulama puanlarının en yükseği (null olanlar hariç; hepsi null ise null).
"""
from __future__ import annotations

from typing import Any


def ramp(x: float | None, lo_hi: list[float]) -> float | None:
    if x is None:
        return None
    lo, hi = lo_hi
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def latency_ratio(cur: dict, base: dict, cfg: dict) -> tuple[float | None, str | None, float | None]:
    """(oran, kaynak, fark_ms). Span p95 her iki pencerede yeterli örnekle varsa onu, yoksa HTTP ortalamasını kullanır.

    Tail impact: p95 değişmese de ortalama tail_mean_ratio kat VE tail_min_delta_ms artmışsa ortalama kullanılır
    (isteklerin küçük bir kısmı çok yavaş; impact p95'in ötesinde kalıyor — ör. hot row Lock Wait)."""
    m = cfg["quality"]["min_span_samples"]
    mean = None
    if cur.get("http_mean_ms") is not None and base.get("http_mean_ms"):
        mc, mb = cur["http_mean_ms"], base["http_mean_ms"]
        mean = (mc / max(mb, 1e-6), "http_mean", mc - mb)
    if (cur.get("span_n") or 0) >= m and (base.get("span_n") or 0) >= m and cur.get("span_p95_ms") is not None \
            and base.get("span_p95_ms"):
        c, b = cur["span_p95_ms"], base["span_p95_ms"]
        p95 = (c / max(b, 1e-6), "span_p95", c - b)
        im = cfg["impact"]
        if (mean and mean[0] >= im.get("tail_mean_ratio", float("inf")) and mean[2] >= im.get("tail_min_delta_ms", 0)
                and mean[0] > p95[0]):
            return mean[0], "http_mean (tail)", mean[2]
        return p95
    return mean or (None, None, None)


def app_components(cur: dict, base: dict, db: dict, hosts: dict, cfg: dict) -> dict[str, Any]:
    s = cfg["score"]
    ratio, src, delta = latency_ratio(cur, base, cfg)
    lat = ramp(ratio, s["latency_ratio"])
    if lat is not None and delta is not None and delta < cfg["impact"]["latency_min_delta_ms"]:
        lat = 0.0

    err = ramp(cur.get("err_rate"), s["error_rate"]) if cur.get("req") else None

    sat_parts = [ramp(cur.get("pool_ratio_avg", cur.get("pool_ratio")), s["pool_ratio"]),
                 ramp(db.get("conn_ratio"), s["pool_ratio"])]
    sat_parts = [x for x in sat_parts if x is not None]
    sat = max(sat_parts) if sat_parts else None
    if sat is not None:
        if (cur.get("pool_timeouts") or 0) > 0:
            sat = 1.0
        elif (cur.get("pool_pending_max") or 0) > 0:
            sat = max(sat, 0.8)

    lock = ramp(cur.get("lock_waiters_avg"), s["lock_waiters"])
    app_host = hosts.get(cfg["hosts"]["app"]) or {}
    res = ramp(app_host.get("cpu"), s["cpu_pct"])
    return {"latency": lat, "errors": err, "saturation": sat, "lock": lock, "resource": res,
            "_latency_ratio": ratio, "_latency_source": src, "_latency_delta_ms": delta}


def combine(components: dict[str, Any], cfg: dict) -> dict[str, Any]:
    w = cfg["score"]["weights"]
    total = sum(w.values())
    avail = {k: v for k, v in components.items() if k in w and v is not None}
    cov = sum(w[k] for k in avail) / total if total else 0.0
    if cov < cfg["quality"]["min_score_coverage"] or not avail:
        return {"score": None, "coverage": round(cov, 3), "band": None,
                "missing": sorted(k for k in w if components.get(k) is None)}
    score = 100 * sum(w[k] * v for k, v in avail.items()) / sum(w[k] for k in avail)
    return {"score": round(score, 1), "coverage": round(cov, 3), "band": band(score, cfg),
            "missing": sorted(k for k in w if components.get(k) is None)}


def band(score: float | None, cfg: dict) -> str | None:
    if score is None:
        return None
    b = cfg["score"]["bands"]
    if score < b["low"]:
        return "düşük"
    if score < b["medium"]:
        return "orta"
    if score < b["high"]:
        return "yüksek"
    return "kritik"


def score_all(cur_fw: dict, base_fw: dict, cfg: dict) -> dict[str, Any]:
    apps = {}
    for app in cfg["apps"]:
        comp = app_components(cur_fw["apps"].get(app, {}), base_fw["apps"].get(app, {}), cur_fw["db"],
                              cur_fw["hosts"], cfg)
        apps[app] = {"components": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in comp.items()},
                     **combine(comp, cfg)}
    scored = [a["score"] for a in apps.values() if a["score"] is not None]
    overall = max(scored) if scored else None
    cov = sum(a["coverage"] for a in apps.values()) / len(apps) if apps else 0.0
    return {"apps": apps, "overall": {"score": overall, "band": band(overall, cfg), "coverage": round(cov, 3),
                                      "scored_apps": len(scored), "total_apps": len(apps)}}
