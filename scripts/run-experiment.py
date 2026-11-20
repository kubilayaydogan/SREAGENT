#!/usr/bin/env python3
"""
Hazır reçeteyi chaos-controller API'si ile başlatır, bitene kadar izler ve faz bazlı özet basar.
Yalnız stdlib kullanır.

  CHAOS_URL=http://127.0.0.1:8080 CHAOS_USER=chaos CHAOS_PASSWORD=... \
    python3 scripts/run-experiment.py E2-hot-row-lock --warmup 10 --baseline 40 --load 60 --recovery 40

--targets reservation,checkin   : reçetedeki hedeflerden yalnız bunları tut
--set app.key=value             : hedef parametresini değiştir (ör. --set reservation.holdMs=1500)
--stop-after N                  : N saniye sonra stop-all çağır (durdurma testi)
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

URL = os.getenv("CHAOS_URL", "http://127.0.0.1:8080").rstrip("/")
AUTH = "Basic " + base64.b64encode(f"{os.getenv('CHAOS_USER', 'chaos')}:{os.getenv('CHAOS_PASSWORD', '')}".encode()).decode()


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, headers={"Authorization": AUTH, "Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith("{") else raw)


def summarize(rec):
    print(f"\n== {rec['experimentId']} {rec['spec']['scenario']} durum={rec['state']} {rec.get('stopReason') or ''}")
    for p, (s, e) in rec["phases"].items():
        print(f"   {p:9s} {s} → {e}")
    print(f"   üretici: {rec['producer']}")
    print(f"   cleanup: {rec['cleanup']}")
    agg = {}
    for r in rec["results"]:
        k = (r["source"], r["app"], r["op"], r["phase"])
        agg[k] = r
    print(f"\n   {'kaynak':10s} {'uygulama':11s} {'operasyon':22s} {'faz':9s} {'hedef':>6s} {'gönd.':>6s} {'yanıt':>6s} "
          f"{'409':>4s} {'5xx':>4s} {'t/o':>4s} {'drop':>5s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'rps':>6s}")
    order = {"WARMUP": 0, "BASELINE": 1, "LOAD": 2, "RECOVERY": 3}
    for k in sorted(agg, key=lambda k: (k[0], k[1], k[2], order.get(k[3], 9))):
        r = agg[k]
        if r["phase"] == "WARMUP":
            continue
        print(f"   {r['source']:10s} {r['app']:11s} {r['op']:22s} {r['phase']:9s} {r['intended']:6d} {r['sent']:6d} "
              f"{r['completed']:6d} {r['conflict']:4d} {r['serverError']:4d} {r['timeout']:4d} {r['dropped']:5d} "
              f"{r['p50Ms']:8.1f} {r['p95Ms']:8.1f} {r['p99Ms']:8.1f} {r['achievedRps']:6.2f}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("--warmup", type=int)
    ap.add_argument("--baseline", type=int)
    ap.add_argument("--load", type=int)
    ap.add_argument("--recovery", type=int)
    ap.add_argument("--targets")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--stop-after", type=int)
    ap.add_argument("--json-out")
    a = ap.parse_args()

    st, spec = call("GET", f"/api/presets/{a.preset}")
    if st != 200:
        sys.exit(f"reçete yok: {a.preset} ({st})")
    for k, v in (("warmupSec", a.warmup), ("baselineSec", a.baseline), ("loadSec", a.load), ("recoverySec", a.recovery)):
        if v is not None:
            spec[k] = v
    if a.targets:
        keep = set(a.targets.split(","))
        spec["targets"] = [t for t in spec["targets"] if t["app"] in keep]
    for s in a.set:
        key, val = s.split("=", 1)
        app, field = key.split(".", 1)
        for t in spec["targets"]:
            if t["app"] == app:
                t[field] = json.loads(val) if val[:1] in "0123456789-[{tfn\"" else val
    for t in spec["targets"]:
        if t["mode"] == "RPS" or t.get("activePhases") == "LOAD":
            t["rampUpSec"] = min(t.get("rampUpSec", 0), spec["loadSec"])
    spec["clientRequestId"] = "cli-" + uuid.uuid4().hex
    st, rec = call("POST", "/api/experiments", spec)
    if st not in (200, 201):
        sys.exit(f"başlatılamadı {st}: {rec}")
    eid = rec["experimentId"]
    # idempotency kontrolü: aynı istek tekrar
    st2, rec2 = call("POST", "/api/experiments", spec)
    print(f"başladı {eid}; tekrar gönderim → HTTP {st2} {rec2.get('experimentId') if isinstance(rec2, dict) else rec2}")
    t0 = time.time()
    last = None
    while True:
        time.sleep(2)
        st, s = call("GET", "/api/status")
        if a.stop_after and time.time() - t0 >= a.stop_after:
            print("stop-all →", call("POST", "/api/experiments/stop-all")[1])
            a.stop_after = None
        if s.get("experimentId") == eid and s.get("phase") != last:
            last = s.get("phase")
            print(f"  t+{time.time() - t0:5.0f}s faz={last}")
        st, r = call("GET", f"/api/experiments/{eid}")
        if r["state"] in ("COMPLETED", "STOPPED", "FAILED"):
            break
    summarize(r)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
