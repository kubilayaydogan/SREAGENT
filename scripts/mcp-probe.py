#!/usr/bin/env python3
"""Dynatrace Remote MCP veri yolu kanıtı (Aşama 1, kabul kriteri 13).

Agent DEĞİLDİR: yalnız MCP uç noktasına bağlanır, araçları listeler ve verilen DQL sorgularını
MCP aracı üzerinden çalıştırır. Çıktıyı (token'sız) JSON kanıt dosyasına yazar.

Yalnız Python 3.9+ standart kütüphanesi. Token komut satırına DEĞİL ortam değişkenine verilir:

  export DT_PLATFORM_TOKEN=dt0s16....            # platform token (mcp-gateway:servers:invoke/read + storage:*:read)
  python3 scripts/mcp-probe.py --env <environment-id> list
  python3 scripts/mcp-probe.py --env <environment-id> dql --query 'fetch logs | limit 1'
  python3 scripts/mcp-probe.py --env <environment-id> gate --since 30m      # kabul kapısı sorgularının tamamı
  python3 scripts/mcp-probe.py --env <environment-id> call --tool <ad> --args '{"k":"v"}'

Uç nokta: https://<env>.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp
Taşıma: MCP Streamable HTTP (JSON-RPC 2.0 POST; yanıt application/json veya text/event-stream).
Araç adları ve girdi şemaları tools/list'ten okunur; burada sabitlenmez.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
MCP_PATH = "/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp"

# Kabul kapısı (docs/lab/acceptance-gate.md "Telemetri / MCP kapısı") satırları → DQL.
# {since} yerine --since değeri konur. Sorgular salt-okur ve limitlidir.
GATE_QUERIES: list[tuple[str, str]] = [
    # Tenant "Smartscape on Grail" kullanıyor: dt.entity.* ve entityName() YOK (2026-11-21 mfi74785'te doğrulandı).
    ("4 servis (OneAgent) — servis listesi",
     'smartscapeNodes "SERVICE" | filter contains(name, "reservation") or contains(name, "checkin") '
     'or contains(name, "flightops") or contains(name, "baggage") or contains(name, "chaos") '
     '| fields id, name | limit 50'),
    ("İki host (APP_VM, DB_VM)",
     'smartscapeNodes "HOST" | fields id, name | limit 20'),
    ("4 servis — yanıt süresi/istek sayısı",
     'timeseries {{ rt = avg(dt.service.request.response_time), n = sum(dt.service.request.count) }}, '
     'by: {{ dt.smartscape.service }}, from: now()-{since} | fieldsAdd svc = getNodeName(dt.smartscape.service) | limit 20'),
    ("APP_VM ve DB_VM host CPU/RAM",
     'timeseries {{ cpu = avg(dt.host.cpu.usage), mem = avg(dt.host.memory.usage) }}, by: {{ dt.smartscape.host }}, '
     'from: now()-{since} | fieldsAdd host = getNodeName(dt.smartscape.host)'),
    # Servis başına p95: OneAgent histogramından (Micrometer Dynatrace v2 exporter yüzdelikleri GÖNDERMİYOR).
    # Birim mikrosaniye. Servislerin p95'leri ortalanmaz.
    ("4 servis — p95 (OneAgent)",
     'timeseries p95 = percentile(dt.service.request.response_time, 95), by: {{ dt.smartscape.service }}, '
     'from: now()-{since}, interval: 1m | fieldsAdd svc = getNodeName(dt.smartscape.service) | limit 20'),
    # dt.containers.cpu.usage_percent bu tenant'ta YOK; mevcut anahtarlar 2026-11-21'de listelendi.
    ("Container CPU/RAM",
     'timeseries {{ user = sum(dt.containers.cpu.usage_user_time), sys = sum(dt.containers.cpu.usage_system_time), '
     'thr = avg(dt.containers.cpu.throttling_ratio), rss = avg(dt.containers.memory.resident_set_bytes) }}, '
     'by: {{ dt.smartscape.container }}, from: now()-{since} | fieldsAdd c = getNodeName(dt.smartscape.container) | limit 20'),
    ("Hikari (Micrometer)",
     'timeseries {{ active = avg(airlab.app.hikaricp.connections.active), pending = avg(airlab.app.hikaricp.connections.pending) }}, '
     'by: {{ service, pool }}, from: now()-{since}'),
    # Micrometer Timer → tek anahtar (özet gauge); istek sayısı rollup: count ile.
    ("HTTP istekleri (Micrometer)",
     'timeseries n = sum(airlab.app.http.server.requests, rollup: count), by: {{ service }}, from: now()-{since}'),
    ("PostgreSQL extension",
     'timeseries {{ backends = avg(postgres.numbackends), waiting = avg(postgres.activity.active_waiting), '
     'locks = avg(postgres.locks) }}, from: now()-{since}'),
    ("airlab.pg.* (collector)",
     'timeseries {{ waiters = max(airlab.pg.lock.waiters_total), used = max(airlab.pg.connections.used_ratio_normal) }}, '
     'from: now()-{since}'),
    ("airlab.pg.blocking_edge logları",
     'fetch logs, from: now()-{since} | filter airlab.event == "airlab.pg.blocking_edge" '
     '| sort timestamp desc | limit 5'),
    # Container stdout JSON'u ayrıştırılmadan gelir (log.source = "Container Output") → sorguda parse.
    ("Uygulama ECS logları (request_id / experiment_id)",
     'fetch logs, from: now()-{since} | filter log.source == "Container Output" and startsWith(container.name, "airlab-") '
     '| parse content, "JSON:j" | fieldsAdd svc = j[service][name], request_id = j[request_id], '
     'experiment_id = j[experiment_id], status = j[http][status] | filter isNotNull(request_id) '
     '| fields timestamp, svc, request_id, experiment_id, status, dt.trace_id | limit 5'),
    ("PostgreSQL sunucu logları (lock wait)",
     'fetch logs, from: now()-{since} | filter host.name == "db-vm" and contains(log.source, "postgresql") '
     '| filter contains(content, "still waiting for") or contains(content, "acquired") | limit 5'),
    ("PostgreSQL extension top queries logları",
     'fetch logs, from: now()-{since} | filter dt.extension.name == "com.dynatrace.extension.postgres" '
     '| summarize n = count(), by: {{ event.group }}'),
    ("Tazelik (container başına son log yaşı)",
     'fetch logs, from: now()-{since} | filter log.source == "Container Output" and startsWith(container.name, "airlab-") '
     '| summarize last = max(timestamp), by: {{ container.name }} | fieldsAdd age = now() - last'),
]

SECRET_RE = re.compile(r"dt0[a-z]\d{2}\.[A-Za-z0-9._-]+")


def redact(obj):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return json.loads(SECRET_RE.sub("dt0***REDACTED***", s))


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(self, url: str, token: str, timeout: float = 60.0):
        self.url, self.token, self.timeout = url, token, timeout
        self.session_id: str | None = None
        self.protocol: str | None = None
        self._id = 0

    def _post(self, payload: dict, expect_response: bool = True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "airlab-mcp-probe/1.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            hint = {401: "token geçersiz/süresi dolmuş", 403: "kapsam eksik (mcp-gateway:servers:invoke/read?) veya kullanıcı izni yok",
                    404: "environment id / URL yanlış"}.get(e.code, "")
            raise McpError(f"HTTP {e.code} {hint}: {SECRET_RE.sub('***', detail)}") from None
        except urllib.error.URLError as e:
            raise McpError(f"bağlantı hatası: {e.reason}") from None
        if not expect_response:
            return None
        msgs = self._parse(body, ctype)
        want = payload.get("id")
        for m in msgs:
            if m.get("id") == want:
                if "error" in m:
                    raise McpError(f"JSON-RPC hata: {m['error']}")
                return m.get("result")
        raise McpError(f"yanıtta id={want} bulunamadı (content-type={ctype}, ilk 300: {body[:300]!r})")

    @staticmethod
    def _parse(body: str, ctype: str) -> list[dict]:
        if "text/event-stream" in ctype:
            out, data = [], []
            for line in body.splitlines() + [""]:
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif line == "" and data:
                    try:
                        out.append(json.loads("\n".join(data)))
                    except json.JSONDecodeError:
                        pass
                    data = []
            return out
        if not body.strip():
            return []
        parsed = json.loads(body)
        return parsed if isinstance(parsed, list) else [parsed]

    def request(self, method: str, params: dict | None = None):
        self._id += 1
        p = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            p["params"] = params
        return self._post(p)

    def notify(self, method: str, params: dict | None = None):
        p = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            p["params"] = params
        self._post(p, expect_response=False)

    def initialize(self) -> dict:
        res = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "airlab-mcp-probe", "version": "1.0"},
        })
        self.protocol = res.get("protocolVersion", PROTOCOL_VERSION)
        self.notify("notifications/initialized")
        return res

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call(self, name: str, args: dict):
        return self.request("tools/call", {"name": name, "arguments": args})


def pick_dql_tool(tools: list[dict]) -> tuple[str, str]:
    """DQL çalıştıran aracı ve sorgu parametresinin adını şemadan bulur."""
    cands = [t for t in tools if "dql" in t["name"].lower()
             and not re.search(r"verify|generate|explain|create|nl2|natural", t["name"], re.I)]
    cands.sort(key=lambda t: (0 if re.search(r"exec|run|query", t["name"], re.I) else 1, t["name"]))
    if not cands:
        raise McpError("DQL çalıştıran araç bulunamadı; 'list' çıktısından adı seçip 'call' kullanın. Araçlar: "
                       + ", ".join(t["name"] for t in tools))
    t = cands[0]
    props = (t.get("inputSchema") or {}).get("properties", {})
    req = (t.get("inputSchema") or {}).get("required", [])
    for key in list(req) + list(props):
        if props.get(key, {}).get("type") == "string" and re.search(r"dql|query|statement", key, re.I):
            return t["name"], key
    strings = [k for k in req if props.get(k, {}).get("type") == "string"]
    if strings:
        return t["name"], strings[0]
    raise McpError(f"{t['name']} aracının sorgu parametresi şemadan çıkarılamadı: {json.dumps(props)[:400]}")


def summarize_result(res) -> dict:
    """tools/call sonucunu kısaltır: metin içerikleri, satır sayısı ipucu, isError."""
    if not isinstance(res, dict):
        return {"raw": res}
    texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts)
    out = {"isError": bool(res.get("isError")), "text_chars": len(joined), "text_head": joined[:1500]}
    # execute-dql metni: "Query metadata: {...}" (bildirimler dahil) + "Query result records:\n[ ... ]"
    marker = "Query result records:"
    if marker in joined:
        meta, recs = joined.split(marker, 1)
        out["metadata_head"] = meta[:3000]
        recs = recs.strip()
        out["records_head"] = recs[:2500]
        try:
            out["record_count"] = len(json.loads(recs))
        except json.JSONDecodeError:
            out["record_count"] = 0 if re.fullmatch(r"\[\s*\]", recs) else None
    if res.get("structuredContent") is not None:
        out["structured_head"] = json.dumps(res["structuredContent"], ensure_ascii=False)[:1500]
    return out


PERMISSION_RE = re.compile(r"missing oauth scope|missing required scope|access denied|missing_bucket_permissions"
                           r"|no bucket permissions|forbidden|not authorized|unauthori")


def classify(summary: dict) -> str:
    """Boş sonuç / hata / izin / veri var ayrımı (gerçek sıfır ile 'kayıt yok' aynı şey değildir).
    İzin uyarıları yalnız metadata/hata metninde aranır; kayıt içeriği (ör. log metni) sınıflandırmayı etkilemez."""
    meta = summary.get("metadata_head", summary.get("text_head", "")).lower()
    if PERMISSION_RE.search(meta):
        return "IZIN_HATASI"
    if summary.get("isError"):
        return "HATA"
    if "record_count" in summary:
        n = summary["record_count"]
        return "BOS" if n == 0 else ("VERI_VAR" if n else "BELIRSIZ")
    return "BELIRSIZ"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="Dynatrace environment id (ör. abc12345). Ya da DT_ENVIRONMENT")
    ap.add_argument("--url", help="tam MCP URL'si (env yerine)")
    ap.add_argument("--out", default="deploy/evidence", help="kanıt JSON dizini (git dışı)")
    ap.add_argument("--timeout", type=float, default=90.0)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="initialize + tools/list")
    d = sub.add_parser("dql", help="tek DQL sorgusu")
    d.add_argument("--query", required=True)
    g = sub.add_parser("gate", help="kabul kapısı sorgularının hepsi")
    g.add_argument("--since", default="30m", help="DQL süre, ör. 30m, 2h")
    c = sub.add_parser("call", help="herhangi bir aracı çağır")
    c.add_argument("--tool", required=True)
    c.add_argument("--args", default="{}")
    a = ap.parse_args()

    token = os.environ.get("DT_PLATFORM_TOKEN", "")
    if not token:
        print("DT_PLATFORM_TOKEN ortam değişkeni boş (token'ı argüman olarak vermeyin).", file=sys.stderr)
        return 2
    env = a.env or os.environ.get("DT_ENVIRONMENT", "")
    url = a.url or (f"https://{env}.apps.dynatrace.com{MCP_PATH}" if env else "")
    if not url:
        print("--env veya --url gerekli", file=sys.stderr)
        return 2
    if "since" in a and not re.fullmatch(r"\d+[smhd]", a.since):
        print("--since biçimi: 30m, 2h, 1d", file=sys.stderr)
        return 2

    ev = {"probe": "airlab-mcp-probe/1.0", "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "url": url, "command": a.cmd, "steps": []}
    rc = 0
    client = McpClient(url, token, a.timeout)
    try:
        t0 = time.monotonic()
        init = client.initialize()
        ev["server"] = {"info": init.get("serverInfo"), "protocolVersion": init.get("protocolVersion"),
                        "capabilities": list((init.get("capabilities") or {}).keys()),
                        "session": bool(client.session_id), "ms": round((time.monotonic() - t0) * 1000)}
        print(f"initialize OK  sunucu={init.get('serverInfo')}  protokol={init.get('protocolVersion')}")
        tools = client.list_tools()
        ev["tools"] = [{"name": t["name"], "required": (t.get("inputSchema") or {}).get("required", [])} for t in tools]
        print(f"tools/list OK  {len(tools)} araç")
        if a.cmd == "list":
            for t in tools:
                desc = (t.get("description") or "").strip().splitlines()[0][:90] if t.get("description") else ""
                print(f"  - {t['name']:<40} {desc}")
        elif a.cmd in ("dql", "gate"):
            tool, param = pick_dql_tool(tools)
            print(f"DQL aracı: {tool}({param}=...)")
            queries = [("tek sorgu", a.query)] if a.cmd == "dql" else \
                [(n, q.format(since=a.since)) for n, q in GATE_QUERIES]
            for name, q in queries:
                t1 = time.monotonic()
                try:
                    s = summarize_result(client.call(tool, {param: q}))
                    status = classify(s)
                except McpError as e:
                    s, status = {"error": str(e)}, "HATA"
                ms = round((time.monotonic() - t1) * 1000)
                ev["steps"].append({"signal": name, "tool": tool, "dql": q, "status": status, "ms": ms, "result": s})
                print(f"  [{status:<11}] {ms:>6} ms  {name}")
                if status in ("HATA", "IZIN_HATASI"):
                    rc = 1
        elif a.cmd == "call":
            res = client.call(a.tool, json.loads(a.args))
            s = summarize_result(res)
            ev["steps"].append({"tool": a.tool, "args": json.loads(a.args), "result": s})
            print(json.dumps(s, ensure_ascii=False, indent=2)[:4000])
    except McpError as e:
        ev["error"] = str(e)
        print(f"HATA: {e}", file=sys.stderr)
        rc = 1
    finally:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"mcp-{a.cmd}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        f.write_text(json.dumps(redact(ev), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"kanıt: {f}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
