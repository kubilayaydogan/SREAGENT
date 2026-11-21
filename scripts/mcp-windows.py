#!/usr/bin/env python3
"""
Deney fazlarını (run-experiment.py --json-out çıktıları) Dynatrace'te Remote MCP `execute-dql` ile sorgular.
Her faz için: servis başına p95 (OneAgent span'ları, /actuator ve /lab hariç), collector kilit kenarı logları,
PostgreSQL "still waiting for" logları, collector waiters_total maksimumu.

  export DT_PLATFORM_TOKEN=...   # MCP token (komut satırına yazmayın)
  python3 scripts/mcp-windows.py --env <environment-id> deploy/evidence/vm/experiments/E2.json ... \
      --json-out deploy/evidence/mcp-experiment-windows.json

Not: span sayıları (n) OneAgent Adaptive Traffic Management örneklemesine tabidir; p95 karşılaştırma içindir.
"""
import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mcp_probe", HERE / "mcp-probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

SPANS = '''fetch spans, from:"{f}", to:"{t}"
| filter span.kind == "server" and isNotNull(spring.application.name)
    and not startsWith(url.path, "/actuator") and not startsWith(url.path, "/lab")
| summarize n=count(), p95_ms=percentile(duration, 95)/1000000, by:{{svc=spring.application.name}}'''
EDGES = 'fetch logs, from:"{f}", to:"{t}" | filter airlab.event == "airlab.pg.blocking_edge" | summarize kenar=count()'
PGLOG = ('fetch logs, from:"{f}", to:"{t}" | filter host.name == "db-vm" and contains(content, "still waiting for") '
         '| summarize lock_wait_log=count()')
WAIT = ('timeseries w=max(airlab.pg.lock.waiters_total), from:"{f}", to:"{t}" '
        '| fieldsAdd maks=arrayMax(w) | fields maks')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help="Dynatrace environment id (ör. abc12345)")
    ap.add_argument("--phases", default="BASELINE,LOAD,RECOVERY")
    ap.add_argument("--json-out")
    ap.add_argument("experiments", nargs="+", help="run-experiment.py --json-out dosyaları")
    a = ap.parse_args()
    token = os.environ.get("DT_PLATFORM_TOKEN") or sys.exit("DT_PLATFORM_TOKEN ortam değişkeni gerekli")
    url = f"https://{a.env}.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp"
    client = probe.McpClient(url, token, 180)
    client.initialize()

    def q(dql):
        s = probe.summarize_result(client.call("execute-dql", {"dqlQueryString": dql}))
        if s.get("isError"):
            return {"error": s.get("text_head", "")[:300]}
        try:
            return json.loads(s.get("records_head") or "[]")
        except json.JSONDecodeError:
            return {"raw": (s.get("records_head") or "")[:500]}

    out = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "tool": "execute-dql", "results": {}}
    wanted = a.phases.split(",")
    for path in a.experiments:
        rec = json.load(open(path, encoding="utf-8"))
        name = pathlib.Path(path).stem
        for ph, (f, t) in rec["phases"].items():
            if ph not in wanted or not t:
                continue
            r = {"from": f, "to": t,
                 "spans": q(SPANS.format(f=f, t=t)), "blocking_edges": q(EDGES.format(f=f, t=t)),
                 "pg_lock_wait_logs": q(PGLOG.format(f=f, t=t)), "collector_waiters_max": q(WAIT.format(f=f, t=t))}
            out["results"][f"{name}/{ph}"] = r
            sp = r["spans"]
            if isinstance(sp, list):
                sp = {x.get("svc"): f'{float(x.get("p95_ms", 0)):.0f}ms(n={x.get("n")})' for x in sp}
            print(f"{name:6} {ph:8} {sp} | kenar={r['blocking_edges']} pglog={r['pg_lock_wait_logs']} "
                  f"waiters={r['collector_waiters_max']}", flush=True)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(probe.redact(out), fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
