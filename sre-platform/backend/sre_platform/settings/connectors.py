"""MCP connector'ları: tohumlama, bağlantı testi, tool keşfi, analiz gateway'i.

* Test (transport + kimlik doğrulama) yalnız MCP `initialize` el sıkışmasıdır; hiçbir tool ÇALIŞTIRILMAZ.
* Keşif `tools/list` çağırır; sonuç sınırlıdır (en fazla 200 tool, açıklama 300 karakter) ve güvenilmeyen veri olarak
  saklanır (yalnız ad/açıklama/annotation; talimat olarak yorumlanmaz).
* Tool kimlikleri connector adıyla namespace'lenir: `dynatrace/execute-dql`.
* Read-only politika: `destructiveHint=true` ya da `readOnlyHint=false` olan tool'lar onaylanamaz.
* Analiz grafiği tek bir DQL kaynağı bekler: `analysis_source=true` ve sağlayıcısı doğrulanmış adapter'a sahip
  (şu an yalnız Dynatrace / execute-dql) connector. Devre dışıysa ya da execute-dql onaylı değilse yeni koşular MCP
  verisi olmadan kısmi rapor üretir (başka kaynağa sessizce geçilmez).
* stdio transport desteklenmez (arayüzden komut çalıştırılmaz).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact_text

from ..db.models import Connector
from . import netguard, secrets

log = logging.getLogger("sre.settings.connectors")
DQL_TOOL = "execute-dql"
MAX_TOOLS = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def seed(s: AsyncSession, rt) -> None:
    """Ortamdaki Dynatrace bağlantısını bir kez connector olarak kaydeder (yönetici düzenlemeleri ezilmez)."""
    ag = rt.agent
    if not ag.mcp_url:
        return
    exists = (await s.execute(select(Connector.id).where(Connector.managed == "bootstrap"))).first()
    if exists:
        return
    b = ag.cfg["budgets"]
    has_source = (await s.execute(select(Connector.id).where(Connector.analysis_source))).first()
    s.add(Connector(name="dynatrace", provider="dynatrace", transport="streamable_http", endpoint=ag.mcp_url,
                    auth_type="bearer", secret_ref="env:DT_PLATFORM_TOKEN", timeout_s=int(b["query_timeout_seconds"]),
                    retries=int(b["query_retries"]), enabled=True, read_only=True, analysis_source=not has_source,
                    allowed_tools=[DQL_TOOL], managed="bootstrap", revision=1))
    await s.flush()


def out(c: Connector) -> dict[str, Any]:
    caps = c.capabilities or {}
    return {"id": str(c.id), "name": c.name, "provider": c.provider, "transport": c.transport, "endpoint": c.endpoint,
            "auth_type": c.auth_type, "secret": secrets.describe(c.secret_ref), "timeout_s": c.timeout_s,
            "retries": c.retries, "enabled": c.enabled, "read_only": c.read_only, "analysis_source": c.analysis_source,
            "allowed_tools": list(c.allowed_tools or []),
            "allowed_tool_ids": [f"{c.name}/{t}" for t in (c.allowed_tools or [])],
            "tools": caps.get("tools", []), "server": caps.get("server"), "discovered_at": caps.get("discovered_at"),
            "last_check": c.last_check or {}, "managed": c.managed, "revision": c.revision,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


def validate_tools(c: Connector, tools: list[str]) -> None:
    """Onaylanacak tool'lar keşfedilmiş olmalı; read-only politikada yıkıcı tool onaylanamaz."""
    known = {t["name"]: t for t in (c.capabilities or {}).get("tools", [])}
    unknown = [t for t in tools if t not in known and not (c.provider == "dynatrace" and t == DQL_TOOL)]
    if unknown:
        raise ValueError(f"keşfedilmemiş tool onaylanamaz: {', '.join(unknown)} (önce keşif çalıştırın)")
    if c.read_only:
        bad = [t for t in tools if t in known and (known[t].get("destructive") or known[t].get("read_only") is False)]
        if bad:
            raise ValueError(f"read-only politikada yazma/yıkıcı tool onaylanamaz: {', '.join(bad)}")


# ------------------------------------------------------------------------------------------------ test / keşif
def _classify(e: BaseException, token: str | None) -> tuple[str, str]:
    """(kategori, temizlenmiş mesaj)"""
    leaf = e
    while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:      # anyio TaskGroup hataları
        leaf = leaf.exceptions[0]
    text = redact_text(f"{type(leaf).__name__}: {leaf}")
    if token:
        text = text.replace(token, "<secret>")
    low = text.lower()
    if isinstance(leaf, (TimeoutError, asyncio.TimeoutError)) or "timeout" in low or "timed out" in low:
        kind = "timeout"
    elif "401" in low or "403" in low or "unauthori" in low or "forbidden" in low:
        kind = "auth"
    elif "certificate" in low or "ssl" in low or "tls" in low:
        kind = "tls"
    elif "connect" in low or "name or service" in low or "resolve" in low or "refused" in low:
        kind = "network"
    else:
        kind = "protocol"
    return kind, text[:500]


async def _session(c: Connector, token: str | None, fn):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {"Authorization": f"Bearer {token}"} if c.auth_type == "bearer" and token else None
    async with streamablehttp_client(c.endpoint, headers=headers, timeout=c.timeout_s,
                                     sse_read_timeout=c.timeout_s) as (r, w, _):
        async with ClientSession(r, w) as sess:
            init = await sess.initialize()
            return await fn(sess, init)


async def _prepare(s: AsyncSession, c: Connector) -> str | None:
    await asyncio.to_thread(netguard.check_url, c.endpoint)
    return await secrets.resolve(s, c.secret_ref) if c.auth_type == "bearer" else None


async def test(s: AsyncSession, c: Connector) -> dict[str, Any]:
    """Transport + kimlik doğrulama (initialize). Tool çalıştırılmaz."""
    t0 = time.monotonic()
    token = None
    try:
        token = await _prepare(s, c)

        async def info(_sess, init):
            si = getattr(init, "serverInfo", None)
            return {"server": {"name": getattr(si, "name", None), "version": getattr(si, "version", None)},
                    "protocol_version": getattr(init, "protocolVersion", None),
                    "capabilities": sorted(k for k, v in (init.capabilities.model_dump() if init.capabilities else {}).items()
                                           if v is not None)}
        data = await asyncio.wait_for(_session(c, token, info), c.timeout_s + 5)
        res = {"ok": True, "kind": "ok", "at": now_iso(), "elapsed_ms": int((time.monotonic() - t0) * 1000), **data}
        c.last_check = {**res, "last_success_at": res["at"]}
    except (netguard.UnsafeUrl, secrets.SecretError) as e:
        res = {"ok": False, "kind": "config", "message": str(e), "at": now_iso()}
        c.last_check = {**(c.last_check or {}), **res}
    except BaseException as e:  # noqa: BLE001 — anyio grup hataları dahil; mesaj temizlenir
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        kind, msg = _classify(e, token)
        res = {"ok": False, "kind": kind, "message": msg, "at": now_iso(),
               "elapsed_ms": int((time.monotonic() - t0) * 1000)}
        c.last_check = {**(c.last_check or {}), **res}
    await s.flush()
    return res


async def discover(s: AsyncSession, c: Connector) -> dict[str, Any]:
    """tools/list → yetenekler. Sonuç güvenilmeyen veridir; yalnız ad/açıklama/annotation saklanır."""
    token = None
    try:
        token = await _prepare(s, c)

        async def tools(sess, init):
            res = await sess.list_tools()
            si = getattr(init, "serverInfo", None)
            items = []
            for t in res.tools[:MAX_TOOLS]:
                ann = getattr(t, "annotations", None)
                items.append({"name": str(t.name)[:128], "id": f"{c.name}/{str(t.name)[:128]}",
                              "description": redact_text(str(t.description or ""))[:300],
                              "read_only": getattr(ann, "readOnlyHint", None) if ann else None,
                              "destructive": getattr(ann, "destructiveHint", None) if ann else None})
            return {"server": {"name": getattr(si, "name", None), "version": getattr(si, "version", None)},
                    "tools": items, "truncated": len(res.tools) > MAX_TOOLS}
        data = await asyncio.wait_for(_session(c, token, tools), c.timeout_s + 10)
        c.capabilities = {**data, "discovered_at": now_iso(),
                          "adapter": "DQL (execute-dql)" if c.provider == "dynatrace" else None}
        if c.provider == "dynatrace" and DQL_TOOL not in {t["name"] for t in data["tools"]}:
            c.capabilities["warning"] = "execute-dql bulunamadı; bu connector analiz kaynağı olarak çalışamaz"
        await s.flush()
        return {"ok": True, **c.capabilities}
    except (netguard.UnsafeUrl, secrets.SecretError) as e:
        return {"ok": False, "kind": "config", "message": str(e)}
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        kind, msg = _classify(e, token)
        return {"ok": False, "kind": kind, "message": msg}


# ------------------------------------------------------------------------------------------------ analiz gateway
async def analysis_source(s: AsyncSession) -> Connector | None:
    return (await s.execute(select(Connector).where(Connector.analysis_source))).scalar_one_or_none()


def unavailable_factory(reason: str):
    from airlab_agent.mcp_gateway import McpUnavailable

    def make(on_event=None):
        raise McpUnavailable(reason)
    return make


async def gateway_factory(s: AsyncSession, agent_settings) -> tuple[Any, dict]:
    """Koşu için gateway fabrikası + snapshot bilgisi. Bütçe/zaman aşımı koşunun etkili ayarlarından gelir."""
    c = await analysis_source(s)
    if c is None:
        return unavailable_factory("analiz veri kaynağı (MCP connector) tanımlı değil"), {"connector": None}
    info = {"connector": c.name, "connector_revision": c.revision, "provider": c.provider, "tool": f"{c.name}/{DQL_TOOL}"}
    if not c.enabled:
        return unavailable_factory(f"MCP connector '{c.name}' devre dışı"), {**info, "available": False}
    if DQL_TOOL not in (c.allowed_tools or []):
        return unavailable_factory(f"'{c.name}/{DQL_TOOL}' onaylı tool değil"), {**info, "available": False}
    try:
        token = await secrets.resolve(s, c.secret_ref)
    except secrets.SecretError as e:
        return unavailable_factory(f"MCP connector '{c.name}': {e}"), {**info, "available": False}
    try:
        await netguard.check_runtime(c.endpoint)    # P0.5: kayıt anı değil, her koşu öncesi (fail-closed)
    except netguard.UnsafeUrl as e:
        from ..security import audit
        await audit.record(s, actor="system:agent", action="egress.denied", section="connectors", target=c.name,
                           result="denied", reason_code="ssrf_guard", detail=str(e))
        return unavailable_factory(f"MCP connector '{c.name}' engellendi: {e}"), {**info, "available": False,
                                                                                   "egress_denied": True}
    from airlab_agent.mcp_gateway import McpGateway
    b = agent_settings.cfg["budgets"]
    # Agent ayarı ile connector sınırının küçüğü uygulanır (connector daha sıkı bir sınır koyabilir).
    url, maxq = c.endpoint, b["max_queries_per_run"]
    qt = min(int(b["query_timeout_seconds"]), c.timeout_s)
    retries = min(int(b["query_retries"]), c.retries)
    info.update(query_timeout_s=qt, query_retries=retries)

    def make(on_event=None):
        return McpGateway(url, token, timeout_s=qt, retries=retries, max_queries=maxq, on_event=on_event)
    return make, {**info, "available": True}
