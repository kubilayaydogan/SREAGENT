"""Dynatrace Remote MCP erişimi (langchain-mcp-adapters 0.3.2 + mcp 1.30.0, streamable HTTP, Bearer platform token).

Agent'ın TEK canlı veri kaynağıdır: DB'ye ya da başka bir API'ye bağlanmaz, MCP kesilirse başka kaynağa geçmez.
Doğrulanan davranış (2026-11-21, `dynatrace-mcp 1.0`): `execute-dql` sonucu `structuredContent.records` ve
`structuredContent.metadata.grail` içinde döner; hata durumunda `isError=True` ve metin içinde DQL hata JSON'u.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from .redact import redact_text

SERVER = "dynatrace"
DQL_TOOL = "execute-dql"
PERMISSION_RE = re.compile(r"missing oauth scope|missing required scope|missing_bucket_permissions|access denied|"
                           r"forbidden|not authorized|unauthori[sz]ed|\b401\b|\b403\b", re.I)


class McpUnavailable(RuntimeError):
    """MCP'ye ulaşılamıyor (ağ, auth, sunucu). Analiz kısmi raporla devam eder; başka kaynağa geçilmez."""


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class QueryResult:
    key: str
    dql: str
    status: str                      # OK | EMPTY | ERROR | PERMISSION | UNAVAILABLE | SKIPPED_BUDGET
    records: list[dict] = field(default_factory=list)
    query_id: str | None = None
    error: str | None = None
    elapsed_ms: int = 0
    notifications: list[str] = field(default_factory=list)

    @property
    def evidence_id(self) -> str:
        return "ev-" + hashlib.sha1(self.dql.encode()).hexdigest()[:10]

    def evidence(self) -> dict:
        return {"id": self.evidence_id, "signal": self.key, "status": self.status, "records": len(self.records),
                "dynatrace_query_id": self.query_id, "dql": self.dql, "error": self.error,
                "notifications": self.notifications}


def _classify(res: Any) -> tuple[str, list[dict], str | None, str | None, list[str]]:
    text = "\n".join(getattr(c, "text", "") for c in (getattr(res, "content", None) or []))
    sc = getattr(res, "structuredContent", None) or {}
    meta = (sc.get("metadata") or {}).get("grail") or {}
    notes = [str(n.get("message") or n.get("notificationType") or n)[:200] for n in (meta.get("notifications") or [])]
    if getattr(res, "isError", False):
        status = "PERMISSION" if PERMISSION_RE.search(text) else "ERROR"
        return status, [], None, redact_text(text[:600]), notes
    records = sc.get("records")
    if records is None:                       # yapılandırılmış içerik yoksa metinden oku (eski sunucu biçimi)
        import json
        marker = "Query result records:"
        records = json.loads(text.split(marker, 1)[1]) if marker in text else []
    return ("OK" if records else "EMPTY"), records, meta.get("queryId"), None, notes


class McpGateway:
    def __init__(self, url: str, token: str, *, timeout_s: float = 60, retries: int = 2, max_queries: int = 40,
                 concurrency: int = 4, on_event=None):
        if not url or not token:
            raise McpUnavailable("DT_MCP_URL/DT_ENV ve DT_PLATFORM_TOKEN tanımlı değil")
        self._conn = {SERVER: {"transport": "streamable_http", "url": url, "timeout": timeout_s,
                               "headers": {"Authorization": f"Bearer {token}"}}}
        self.client = MultiServerMCPClient(self._conn)
        self.timeout_s, self.retries, self.max_queries, self.concurrency = timeout_s, retries, max_queries, concurrency
        self.queries_used = 0
        self.log: list[QueryResult] = []
        # İsteğe bağlı gözlemci: {"phase": "start"|"end", "key", "dql", ...}. Hata atarsa sorgu etkilenmez.
        self.on_event = on_event

    def _emit(self, ev: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001
                pass

    # ---- keşif
    async def _discover(self) -> dict:
        async with self.client.session(SERVER) as s:
            init = getattr(s, "_server_info", None)
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            schema = next((t.inputSchema for t in tools.tools if t.name == DQL_TOOL), None)
            return {"server": str(init) if init else None, "tools": names, "dql_tool_schema": schema}

    def discover(self) -> dict:
        try:
            return asyncio.run(asyncio.wait_for(self._discover(), self.timeout_s * 2))
        except Exception as e:  # noqa: BLE001 — her hata "MCP yok" demektir
            raise McpUnavailable(redact_text(f"{type(e).__name__}: {e}")) from None

    # ---- sorgu
    async def _one(self, session, key: str, dql: str) -> QueryResult:
        if self.queries_used >= self.max_queries:
            r = QueryResult(key, dql, "SKIPPED_BUDGET", error="sorgu bütçesi doldu")
            self._emit({"phase": "end", "key": key, "dql": dql, "status": r.status, "records": 0, "query_id": None,
                        "elapsed_ms": 0, "error": r.error, "evidence_id": r.evidence_id})
            return r
        self.queries_used += 1
        self._emit({"phase": "start", "key": key, "dql": dql, "tool": DQL_TOOL})
        last = None
        t_all = time.monotonic()
        for attempt in range(self.retries + 1):
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(
                    session.call_tool(DQL_TOOL, {"dqlQueryString": dql, "includeTypes": False}), self.timeout_s)
                status, recs, qid, err, notes = _classify(res)
                r = QueryResult(key, dql, status, recs, qid, err, int((time.monotonic() - t0) * 1000), notes)
                self._emit({"phase": "end", "key": key, "dql": dql, "status": status, "records": len(recs),
                            "query_id": qid, "elapsed_ms": int((time.monotonic() - t_all) * 1000), "error": err,
                            "attempts": attempt + 1, "evidence_id": r.evidence_id})
                return r
            except Exception as e:  # noqa: BLE001
                last = e
                await asyncio.sleep(min(2 ** attempt, 8))
        err = redact_text(f"{type(last).__name__}: {last}")
        self._emit({"phase": "end", "key": key, "dql": dql, "status": "UNAVAILABLE", "records": 0, "query_id": None,
                    "elapsed_ms": int((time.monotonic() - t_all) * 1000), "error": err, "attempts": self.retries + 1})
        raise McpUnavailable(err)

    async def _many(self, items: list[tuple[str, str]]) -> list[QueryResult]:
        async with self.client.session(SERVER) as session:
            sem = asyncio.Semaphore(self.concurrency)

            async def run(k, d):
                async with sem:
                    return await self._one(session, k, d)
            return list(await asyncio.gather(*(run(k, d) for k, d in items)))

    def run_many(self, items: list[tuple[str, str]]) -> dict[str, QueryResult]:
        """Sorguları tek MCP oturumunda (sınırlı eşzamanlılıkla) çalıştırır. MCP yoksa McpUnavailable."""
        if not items:
            return {}
        try:
            results = asyncio.run(asyncio.wait_for(self._many(items), self.timeout_s * (2 + len(items) / 2)))
        except McpUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise McpUnavailable(redact_text(f"{type(e).__name__}: {e}")) from None
        self.log.extend(results)
        return {r.key: r for r in results}
