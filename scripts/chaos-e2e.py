#!/usr/bin/env python3
"""Chaos v2 uçtan uca doğrulama: senaryoları GERÇEK sistemde sırayla çalıştırır, terminal duruma kadar izler,
success criteria / expected vs observed / recovery sonuçlarını ve markdown raporlarını kanıt olarak kaydeder.

APP_VM'de çalıştırın (kimlik bilgileri app-vm/.env'den okunur, ekrana basılmaz):
  python3 scripts/chaos-e2e.py --out /opt/airlab/deploy/evidence/chaos-v2 [--only S01,S02] [--quick]
  python3 scripts/chaos-e2e.py --acceptance        # birleşik DB yükü acceptance testi (S17, HIGH)

Çıkış kodu: tüm deneyler terminal duruma ulaştı, cleanup OK ve zorunlu ölçütler PASS ise 0.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

# senaryo → (intensity, initiator, targetMode, targets). Tüm senaryolar paylaşılan PostgreSQL'de yük oluşturur;
# MULTI / ALL / AUTO satırlarında yükü birden fazla uygulama başlatır.
PLAN = {
    "S01": ("MEDIUM", "checkin", "MULTI", ["reservation"]),
    "S02": ("MEDIUM", "reservation", "MULTI", ["reservation", "checkin"]),
    "S03": ("MEDIUM", "flightops", "AUTO", []),
    "S04": ("MEDIUM", "baggage", "SINGLE", []),
    "S05": ("MEDIUM", "baggage", "MULTI", ["flightops"]),
    "S06": ("MEDIUM", "reservation", "SINGLE", []),
    "S07": ("MEDIUM", "checkin", "SINGLE", []),
    "S08": ("MEDIUM", "reservation", "MULTI", ["checkin", "flightops"]),
    "S09": ("MEDIUM", "checkin", "MULTI", ["baggage"]),
    "S10": ("MEDIUM", "flightops", "MULTI", ["baggage"]),
    "S11": ("MEDIUM", "baggage", "MULTI", ["reservation"]),
    "S12": ("MEDIUM", "reservation", "SINGLE", []),
    "S13": ("MEDIUM", "checkin", "MULTI", ["flightops"]),
    "S14": ("MEDIUM", "flightops", "MULTI", ["checkin"]),
    "S15": ("MEDIUM", "reservation", "MULTI", ["baggage"]),
    "S16": ("MEDIUM", "checkin", "MULTI", ["flightops"]),
    "S17": ("HIGH", "baggage", "AUTO", []),
    "S18": ("MEDIUM", "reservation", "MULTI", ["checkin"]),
    "S18H": ("HIGH", "reservation", "ALL", []),
    "S19": ("MEDIUM", "baggage", "AUTO", []),
    "S20": ("HIGH", "checkin", "ALL", []),
}


def env(path: str) -> dict[str, str]:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"')
    return out


class Api:
    def __init__(self, base: str, user: str, pw: str):
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def req(self, method: str, path: str, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                b = resp.read().decode()
                return resp.status, (b if raw else json.loads(b) if b else None)
        except urllib.error.HTTPError as e:
            b = e.read().decode()
            try:
                return e.code, json.loads(b)
            except ValueError:
                return e.code, b


def wait_idle(api: Api, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, s = api.req("GET", "/api/v2/status")
        _, legacy = api.req("GET", "/api/status")
        if not s.get("busy") and legacy.get("state") not in ("RUNNING", "STOPPING"):
            return
        time.sleep(5)
    raise SystemExit("sistem boşa çıkmadı")


def run_one(api: Api, sid: str, intensity: str, initiator: str, mode: str, targets: list[str], durations: dict,
            out_dir: str, label: str) -> dict:
    wait_idle(api)
    body = {"scenarioId": sid, "initiator": initiator, "targetMode": mode, "targets": targets,
            "intensity": intensity, "durations": durations, "seed": 20261123, "label": label}
    code, res = api.req("POST", "/api/v2/experiments", body)
    if code not in (200, 202):
        return {"scenario": sid, "error": res}
    eid = res["id"]
    print(f"[{sid}] {eid} başladı ({intensity}, initiator={initiator}, targets={res.get('targets')})", flush=True)
    last = None
    while True:
        time.sleep(5)
        _, e = api.req("GET", f"/api/v2/experiments/{eid}?samples=false")
        if e["state"] != last:
            print(f"[{sid}]   → {e['state']}: {e.get('stateReason')}", flush=True)
            last = e["state"]
        if e["state"] in ("COMPLETED", "FAILED", "ABORTED", "TIMED_OUT"):
            break
    _, full = api.req("GET", f"/api/v2/experiments/{eid}")
    _, md = api.req("GET", f"/api/v2/experiments/{eid}/report.md", raw=True)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{sid}-{eid}.json"), "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, f"{sid}-{eid}.md"), "w", encoding="utf-8") as f:
        f.write(md if isinstance(md, str) else json.dumps(md))
    rep = full.get("report") or {}
    crit = rep.get("criteria", [])
    summary = {
        "scenario": sid, "id": eid, "state": full["state"], "stateReason": full.get("stateReason"),
        "verdict": rep.get("verdict"), "cleanupOk": full.get("cleanupOk"),
        "recovered": (rep.get("recovery") or {}).get("recovered"),
        "recoveryTimeSec": (rep.get("recovery") or {}).get("recoveryTimeSec"),
        "observedAffected": (rep.get("expectedVsObserved") or {}).get("observedAffected"),
        "criteria": [{"d": c["description"], "req": c["required"], "obs": c.get("observed"),
                      "thr": c.get("threshold"), "res": c["result"]} for c in crit],
        "controller": rep.get("controller"),
    }
    for c in summary["criteria"]:
        print(f"[{sid}]     {'*' if c['req'] else ' '} {c['res']:<8} {c['d']} (observed={c['obs']}, threshold={c['thr']})")
    print(f"[{sid}]   verdict={summary['verdict']} cleanup={summary['cleanupOk']} recovered={summary['recovered']} "
          f"({summary['recoveryTimeSec']} s) affected={summary['observedAffected']}", flush=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="/opt/airlab/app-vm/.env")
    ap.add_argument("--base", default="http://172.20.229.26:8080")
    ap.add_argument("--out", default="/opt/airlab/deploy/evidence/chaos-v2")
    ap.add_argument("--only", default="")
    ap.add_argument("--quick", action="store_true", help="kısa süreler (baseline 20, ramp 15, run 40, recovery 60)")
    ap.add_argument("--acceptance", action="store_true")
    a = ap.parse_args()
    e = env(a.env)
    api = Api(a.base, e.get("CHAOS_USER", "chaos"), e["CHAOS_PASSWORD"])
    durations = ({"baselineSec": 20, "rampSec": 15, "durationSec": 40, "recoverySec": 60, "maxDurationSec": 0}
                 if a.quick else {"baselineSec": 30, "rampSec": 30, "durationSec": 90, "recoverySec": 90,
                                  "maxDurationSec": 0})
    results = []
    if a.acceptance:
        results.append(run_one(api, "S17", "HIGH", "baggage", "AUTO", [], durations, a.out, "cascading acceptance"))
    else:
        only = [s.strip() for s in a.only.split(",") if s.strip()]
        for sid, (inten, init, mode, tg) in PLAN.items():
            if only and sid not in only:
                continue
            # "S18H" gibi ekli anahtarlar aynı senaryonun farklı yoğunluğudur (varsayılan çalıştırmada atlanır)
            if len(sid) > 3 and sid not in only:
                continue
            results.append(run_one(api, sid[:3], inten, init, mode, tg, durations, a.out, "e2e " + sid))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(a.out, f"summary-{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    ok = all(r.get("state") == "COMPLETED" and r.get("cleanupOk")
             and all(c["res"] == "PASS" for c in r.get("criteria", []) if c["req"]) for r in results)
    print("\nÖZET")
    for r in results:
        print(f"  {r['scenario']}: state={r.get('state')} verdict={r.get('verdict')} cleanup={r.get('cleanupOk')} "
              f"recovered={r.get('recovered')} {r.get('error', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
