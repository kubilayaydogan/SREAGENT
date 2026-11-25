"""MCP çağrı kaydı için güvenli özetler: istek önizlemesi (alan listesi), DQL şablonu, hata özeti, HTTP kodu ve
iptal edilen çağrının kapanış olayı."""
from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace as NS

from airlab_agent.mcp_gateway import (McpGateway, McpUnavailable, dql_template, http_status_of, request_preview,
                                      safe_error_summary)

SECRET = "PLAIN-SECRET-VALUE-42"
DQL = (f'fetch logs, from:"2026-11-21T10:00:00Z", to:"2026-11-21T10:15:00Z" | filter content == "{SECRET}" '
       '| summarize n = count() | limit 50')


def test_request_preview_uses_field_list_and_never_raw_dql():
    p = request_preview("dynatrace", "execute-dql", {"dqlQueryString": DQL, "includeTypes": False,
                                                     "authorization": "Bearer x"}, "logs")
    assert p["method"] == "tools/call" and p["query_key"] == "logs" and p["params"] == {"includeTypes": False}
    assert (p["query_start"], p["query_end"], p["limit"]) == ("2026-11-21T10:00:00Z", "2026-11-21T10:15:00Z", 50)
    assert p["omitted_params"] == ["authorization"] and p["dql_chars"] == len(DQL) and len(p["dql_sha256"]) == 16
    blob = repr(p)
    assert SECRET not in blob and DQL not in blob and "Bearer" not in blob
    assert p["dql_template"].startswith('fetch logs, from:"…", to:"…" | filter content == "…"')
    # bilinmeyen connector/tool: hiçbir parametre değeri saklanmaz
    q = request_preview("other", "tool", {"q": SECRET, "n": 3})
    assert q["params"] == {} and q["omitted_params"] == ["n", "q"] and SECRET not in repr(q)


def test_template_error_summary_http_status():
    assert dql_template("a  \n  b", 400) == "a b" and dql_template("x" * 500, 10).endswith(" …")
    s = safe_error_summary(f'DQL error near "{SECRET}"\nline2 {SECRET}')
    assert s == 'DQL error near "…"' and safe_error_summary(None) is None
    assert http_status_of(NS(response=NS(status_code=403), __cause__=None, __context__=None)) == 403
    inner = RuntimeError("HTTP 401 Unauthorized")
    outer = RuntimeError("wrapped")
    outer.__cause__ = inner
    assert http_status_of(outer) == 401 and http_status_of(TimeoutError()) is None


def test_cancelled_call_emits_closing_event():
    """Bir çağrı MCP'yi erişilemez bulunca (HTTP 503) paralel çağrılar iptal edilir; her biri kapanış olayı yayar."""
    class S:
        async def call_tool(self, name, args):
            if "fast" in args["dqlQueryString"]:
                err = RuntimeError("Server error '503 Service Unavailable' for url 'http://mcp.test'")
                raise err
            await asyncio.sleep(5)

    @contextlib.asynccontextmanager
    async def cm(_s):
        yield S()
    ev: list[dict] = []
    gw = McpGateway("http://mcp.test", "tok", timeout_s=0.5, retries=0, concurrency=4, on_event=ev.append)
    gw.client = NS(session=cm)
    try:
        gw.run_many([("a", 'fetch logs // fast'), ("b", 'fetch logs // slow')])
        raise AssertionError("McpUnavailable beklenirdi")
    except McpUnavailable:
        pass
    ends = {e["key"]: e for e in ev if e["phase"] == "end"}
    assert ends["a"]["outcome"] == "unavailable" and ends["a"]["http_status"] == 503
    assert ends["b"]["outcome"] == "cancelled" and ends["b"]["records"] is None
    starts = {e["key"] for e in ev if e["phase"] == "start"}
    assert starts == set(ends)
