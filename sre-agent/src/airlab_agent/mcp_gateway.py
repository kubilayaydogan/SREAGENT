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
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from .redact import redact_text
from .tracing import ToolSpan, parent_config

SERVER = "dynatrace"
DQL_TOOL = "execute-dql"
PERMISSION_RE = re.compile(r"missing oauth scope|missing required scope|missing_bucket_permissions|access denied|"
                           r"forbidden|not authorized|unauthori[sz]ed|\b401\b|\b403\b", re.I)


# P1.7 sınırları: çağrı anı DQL doğrulaması ve yanıt boyutu (güvenilmeyen sunucu çıktısı belleği tüketemez)
MAX_DQL_CHARS = 8000
MAX_RESPONSE_CHARS = 4_000_000
MAX_RECORDS = 5000
DQL_COMMANDS = ("fetch", "timeseries")            # katalogdaki sorguların başladığı komutlar (salt okuma)
_DQL_HEAD = re.compile(r"^\s*(" + "|".join(DQL_COMMANDS) + r")\b")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_dql(dql: Any) -> str | None:
    """Çağrıdan hemen önce DQL denetimi. Sorun yoksa None, varsa kısa neden (query GÖNDERİLMEZ)."""
    if not isinstance(dql, str) or not dql.strip():
        return "boş query"
    if len(dql) > MAX_DQL_CHARS:
        return f"query çok uzun ({len(dql)} > {MAX_DQL_CHARS})"
    if _CTRL.search(dql):
        return "kontrol karakteri içeriyor"
    if not _DQL_HEAD.match(dql):
        return f"izin verilmeyen başlangıç komutu (izinli: {', '.join(DQL_COMMANDS)})"
    return None


def tool_definition_hash(t: Any) -> str:
    """MCP tool tanımının özeti (ad + açıklama + inputSchema + annotations). Değişiklik = yeniden inceleme."""
    import json
    ann = getattr(t, "annotations", None)
    if hasattr(ann, "model_dump"):
        ann = ann.model_dump(exclude_none=True)
    d = {"name": getattr(t, "name", None), "description": getattr(t, "description", None) or "",
         "inputSchema": getattr(t, "inputSchema", None) or {}, "annotations": ann or None}
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


class McpUnavailable(RuntimeError):
    """MCP'ye ulaşılamıyor (ağ, auth, sunucu). Analiz kısmi raporla devam eder; başka kaynağa geçilmez.
    `kind`: timeout | unavailable | integrity (trace ve raporda ayrı gösterilir)."""

    def __init__(self, msg: str = "", kind: str = "unavailable"):
        super().__init__(msg)
        self.kind = kind


# Mantıksal MCP çağrısının sonucu (trace, platform çağrı kaydı ve SSE'de aynı sınıflandırma)
OUTCOME = {"OK": "success", "EMPTY": "empty", "PERMISSION": "permission_denied", "ERROR": "tool_error",
           "UNAVAILABLE": "unavailable", "TIMEOUT": "timeout", "SKIPPED_BUDGET": "skipped_budget",
           "REJECTED": "rejected", "CANCELLED": "cancelled"}
_TIME_RANGE = re.compile(r'from:\s*"([^"]+)"\s*,\s*to:\s*"([^"]+)"')


def time_range_of(dql: str) -> dict | None:
    """DQL'deki from/to aralıklarının kapsadığı toplam aralık (yalnız zaman damgaları; query'nin kendisi değil)."""
    found = _TIME_RANGE.findall(dql or "")
    return {"start": min(a for a, _ in found), "end": max(b for _, b in found)} if found else None


# ------------------------------------------------------------------ çağrı kaydı için güvenli özetler
# Connector/tool bazlı alan listesi: hangi parametre değeri olduğu gibi saklanabilir, hangisi yalnız özetlenir.
# Listede olmayan parametreler kaydedilmez (yalnız adları `omitted_params`'ta).
PREVIEW_FIELDS: dict[tuple[str, str], dict] = {
    ("dynatrace", "execute-dql"): {"raw": ("includeTypes",), "dql": "dqlQueryString"},
}
_STR_LIT = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")
_LIMIT = re.compile(r"\|\s*limit\s+(\d+)", re.I)
_HTTP_STATUS = re.compile(r"(?:\bHTTP\S*|status(?:[ _]code)?|error) ?[:=']? ?([1-5]\d\d)\b", re.I)


def dql_template(dql: str, max_chars: int = 400) -> str:
    """Güvenli DQL şablonu: tüm string literal'leri (zaman damgaları, filtre değerleri) `"…"` olur; boşluk sadeleşir.
    Ham DQL kaydedilmez — şablon yalnız query'nin şeklini gösterir."""
    t = _STR_LIT.sub('"…"', dql or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= max_chars else t[:max_chars] + " …"


def request_preview(connector: str, tool: str, args: dict, query_key: str | None = None) -> dict:
    """Gerçekten gönderilen `tools/call` parametrelerinden güvenli önizleme (PREVIEW_FIELDS alan listesine göre)."""
    spec = PREVIEW_FIELDS.get((connector, tool), {})
    raw = spec.get("raw", ())
    out: dict = {"method": "tools/call", "connector": connector, "tool": tool, "query_key": query_key,
                 "params": {k: args[k] for k in raw if k in args and isinstance(args[k], (bool, int, float))}}
    dql_param = spec.get("dql")
    dql = args.get(dql_param) if dql_param else None
    if isinstance(dql, str):
        tr = time_range_of(dql) or {}
        lim = _LIMIT.findall(dql)
        out.update(query_start=tr.get("start"), query_end=tr.get("end"), limit=int(lim[-1]) if lim else None,
                   dql_template=dql_template(dql), dql_chars=len(dql),
                   dql_sha256=hashlib.sha256(dql.encode()).hexdigest()[:16])
    out["omitted_params"] = sorted(k for k in args if k not in raw and k != dql_param)
    return out


def safe_error_summary(text: str | None, max_chars: int = 200) -> str | None:
    """Kayda yazılabilir hata özeti: ilk satır, string literal'ler maskeli, secret'lar redakte, kısa."""
    if not text:
        return None
    first = next((ln.strip() for ln in str(text).splitlines() if ln.strip()), "")
    first = redact_text(_STR_LIT.sub('"…"', first))
    return first if len(first) <= max_chars else first[:max_chars] + "…"


def http_status_of(e: BaseException | None) -> int | None:
    """Exception zincirinden HTTP durum kodu (httpx yanıtı ya da mesajdaki kod). Bulunamazsa None."""
    seen = 0
    stack = [e]
    while stack and seen < 20:
        x = stack.pop()
        seen += 1
        if x is None:
            continue
        resp = getattr(x, "response", None)
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
        m = _HTTP_STATUS.search(str(x))
        if m:
            return int(m.group(1))
        stack += list(getattr(x, "exceptions", []) or []) + [x.__cause__, x.__context__]
    return None


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
    call_id: str | None = None       # mantıksal MCP çağrısının kimliği (retry'lar aynı kimlikte)
    attempts: list[dict] = field(default_factory=list)
    outcome: str | None = None

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
    if len(text) > MAX_RESPONSE_CHARS:
        return "ERROR", [], meta.get("queryId"), f"yanıt boyutu sınırı aşıldı ({len(text)} > {MAX_RESPONSE_CHARS})", notes
    if getattr(res, "isError", False):
        status = "PERMISSION" if PERMISSION_RE.search(text) else "ERROR"
        return status, [], None, redact_text(text[:600]), notes
    records = sc.get("records")
    if records is None:                       # yapılandırılmış içerik yoksa metinden oku (eski sunucu biçimi)
        import json
        marker = "Query result records:"
        records = json.loads(text.split(marker, 1)[1]) if marker in text else []
    if not isinstance(records, list):
        return "ERROR", [], meta.get("queryId"), "beklenmeyen yanıt biçimi (records liste değil)", notes
    if len(records) > MAX_RECORDS:
        notes = notes + [f"kayıt sınırı: {len(records)} kayıttan ilk {MAX_RECORDS} kullanıldı"]
        records = records[:MAX_RECORDS]
    return ("OK" if records else "EMPTY"), records, meta.get("queryId"), None, notes


class McpGateway:
    def __init__(self, url: str, token: str, *, timeout_s: float = 60, retries: int = 2, max_queries: int = 40,
                 concurrency: int = 4, on_event=None, expected_tool_hash: str | None = None):
        if not url or not token:
            raise McpUnavailable("DT_MCP_URL/DT_ENV ve DT_PLATFORM_TOKEN tanımlı değil")
        self._conn = {SERVER: {"transport": "streamable_http", "url": url, "timeout": timeout_s,
                               "headers": {"Authorization": f"Bearer {token}"}}}
        self.client = MultiServerMCPClient(self._conn)
        self.timeout_s, self.retries, self.max_queries, self.concurrency = timeout_s, retries, max_queries, concurrency
        self.queries_used = 0
        self._trace_parent = None
        self.log: list[QueryResult] = []
        # İsteğe bağlı gözlemci: {"phase": "start"|"end", "key", "dql", ...}. Hata atarsa sorgu etkilenmez.
        self.on_event = on_event
        # Onaylanmış execute-dql tanım özeti: her oturumda sunucunun bildirdiği tanımla karşılaştırılır (rug-pull)
        self.expected_tool_hash = expected_tool_hash

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
        """Tek MANTIKSAL MCP çağrısı: retry denemeleri aynı `call_id` altında; trace'te tek tool span'i (kimliği
        call_id, platformdaki çağrı kaydının kimliği de aynı)."""
        call_id = str(uuid.uuid4())
        tr = time_range_of(dql)
        span = ToolSpan(self._trace_parent, f"mcp:{key}",
                        {"connector": SERVER, "tool": DQL_TOOL, "query_key": key, "time_range": tr,
                         "mcp_call_id": call_id},
                        {"connector": SERVER, "tool": DQL_TOOL, "query_key": key, "mcp_call_id": call_id},
                        run_id=uuid.UUID(call_id))
        try:
            r = await self._one_inner(session, key, dql, call_id, traced=span.active)
        except McpUnavailable as e:
            att = getattr(e, "attempts", [])
            span.error(f"outcome={e.kind} attempts={len(att)} "
                       f"elapsed_ms={sum(a.get('elapsed_ms', 0) for a in att)} mcp_call_id={call_id}")
            raise
        except asyncio.CancelledError:              # paralel bir çağrı MCP'yi erişilemez buldu: oturum kapandı
            span.error(f"outcome=cancelled mcp_call_id={call_id}")
            raise
        out = {"outcome": r.outcome, "result_status": r.status, "records": len(r.records),
               "elapsed_ms": r.elapsed_ms, "evidence_id": r.evidence_id, "dynatrace_query_id": r.query_id,
               "attempts_count": len(r.attempts), "attempts": r.attempts, "mcp_call_id": call_id}
        if r.status in ("OK", "EMPTY"):
            span.end(out)
        else:                                        # yetki / tool hatası / bütçe / reddedilen DQL: hatalı span
            span.error(f"outcome={r.outcome} attempts={len(r.attempts)} elapsed_ms={r.elapsed_ms} "
                       f"evidence_id={r.evidence_id} mcp_call_id={call_id}")
        return r

    def _end(self, r: QueryResult, *, elapsed_ms: int, traced: bool, outcome: str | None = None,
             sent: bool = True, http_status: int | None = None, preview: dict | None = None) -> QueryResult:
        r.outcome = outcome or OUTCOME.get(r.status, r.status.lower())
        counted = r.status in ("OK", "EMPTY")               # kayıt sayısı yalnız başarılı yanıtta anlamlı
        self._emit({"phase": "end", "key": r.key, "dql": r.dql, "status": r.status, "outcome": r.outcome,
                    "records": len(r.records) if counted else None, "query_id": r.query_id,
                    "elapsed_ms": elapsed_ms, "error": r.error, "error_summary": safe_error_summary(r.error),
                    "attempts": len(r.attempts) or None, "attempt_log": list(r.attempts),
                    "evidence_id": r.evidence_id, "mcp_call_id": r.call_id, "traced": traced, "sent": sent,
                    "connector": SERVER, "tool": DQL_TOOL, "http_status": http_status,
                    "request_preview": preview})
        return r

    async def _one_inner(self, session, key: str, dql: str, call_id: str | None = None,
                         traced: bool = False) -> QueryResult:
        """wrap_tool_call (Agent Middleware) bu doğrudan MCP istemci çağrısını sarar: allowlist, DQL doğrulama ve
        query budget middleware'leri tools/call GÖNDERİLMEDEN karar verir; engellenen çağrı sunucuya gitmez."""
        from . import middleware as mw
        call_id = call_id or str(uuid.uuid4())
        args = {"dqlQueryString": dql, "includeTypes": False}
        preview = request_preview(SERVER, DQL_TOOL, args if isinstance(dql, str) else {}, key)
        call = mw.Call(kind="tool", target=DQL_TOOL, task=key, connector=SERVER, tool_call_id=call_id,
                       # kayıt görünümünde ham DQL YOK (MCP çağrı kaydıyla aynı politika): yalnız güvenli önizleme
                       request={"query_key": key, "tool": DQL_TOOL, "connector": SERVER, "preview": preview,
                                "_dql": dql},
                       state={"queries_used": self.queries_used, "max_queries": self.max_queries})

        def blocked(d) -> QueryResult:                # tools/call GÖNDERİLMEDİ; sonuç eski davranışla aynı
            if d.code == "query_budget":
                r = QueryResult(key, dql, "SKIPPED_BUDGET", error="query budget doldu", call_id=call_id)
                return self._end(r, elapsed_ms=0, traced=traced, sent=False, preview=preview)
            msg = f"DQL reddedildi: {d.reason}" if d.code == "dql_rejected" else                 f"middleware engelledi ({d.code or 'blocked'}): {d.reason}"
            r = QueryResult(key, str(dql)[:MAX_DQL_CHARS], "ERROR", error=msg, call_id=call_id)
            return self._end(r, elapsed_ms=0, traced=traced, outcome="rejected", sent=False, preview=preview)

        async def send(_req) -> QueryResult:
            # Guardrails before_tool / before_mcp_request: zorunlu middleware'ler geçtikten sonra, tools/call'dan önce
            from .guardrails import runtime as gr
            from .guardrails.model import GuardrailBlocked, GuardrailTerminated
            try:
                gr.mcp_request(server=SERVER, tool=DQL_TOOL, args=args, dql=dql if isinstance(dql, str) else None,
                               call_id=call_id, view={"query_key": key, "preview": preview})
            except GuardrailTerminated:
                raise
            except GuardrailBlocked as e:
                r = QueryResult(key, str(dql)[:MAX_DQL_CHARS], "ERROR", error=f"guardrail engelledi: {e}"[:600],
                                call_id=call_id)
                call.response = {"status": "ERROR", "outcome": "rejected", "guardrail": e.code}
                return self._end(r, elapsed_ms=0, traced=traced, outcome="rejected", sent=False, preview=preview)
            try:
                r = await self._send(session, key, dql, call_id, traced, args, preview)
            except McpUnavailable as e:
                call.attempts = list(getattr(e, "attempts", []))
                raise
            if r.status in ("OK", "EMPTY"):
                # after MCP response: güvenilmeyen veri (boyut, biçim, talimat, secret) persist / LLM öncesi
                try:
                    r.records = gr.mcp_response(server=SERVER, tool=DQL_TOOL, status=r.status, records=r.records,
                                                call_id=call_id)
                except GuardrailTerminated:
                    raise
                except GuardrailBlocked as e:
                    r.status, r.records, r.error = "ERROR", [], f"guardrail yanıtı reddetti: {e}"[:600]
                    r.outcome = "rejected"
                if r.status == "OK" and not r.records:
                    r.status = "EMPTY"
            call.attempts = list(r.attempts)
            call.response = {"status": r.status, "outcome": r.outcome, "records": len(r.records),
                             "query_id": r.query_id}
            return r
        return await mw.run_tool_async(call, send, blocked)

    async def _send(self, session, key: str, dql: str, call_id: str, traced: bool, args: dict,
                    preview: dict) -> QueryResult:
        bad = validate_dql(dql)                       # son savunma hattı (middleware ile aynı kural, çoğaltma değil)
        if bad:
            r = QueryResult(key, str(dql)[:MAX_DQL_CHARS], "ERROR", error=f"DQL reddedildi: {bad}", call_id=call_id)
            return self._end(r, elapsed_ms=0, traced=traced, outcome="rejected", sent=False, preview=preview)
        if self.queries_used >= self.max_queries:
            r = QueryResult(key, dql, "SKIPPED_BUDGET", error="query budget doldu", call_id=call_id)
            return self._end(r, elapsed_ms=0, traced=traced, sent=False, preview=preview)
        self.queries_used += 1
        self._emit({"phase": "start", "key": key, "dql": dql, "tool": DQL_TOOL, "mcp_call_id": call_id,
                    "traced": traced, "connector": SERVER, "request_preview": preview})
        last = None
        attempts: list[dict] = []
        t_all = time.monotonic()
        for attempt in range(self.retries + 1):
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(session.call_tool(DQL_TOOL, args), self.timeout_s)
                status, recs, qid, err, notes = _classify(res)
                # Yanıt geldi → HTTP 200; tool hatası (isError) yine BAŞARISIZ sayılır (outcome tool_error/permission)
                attempts.append({"attempt": attempt + 1, "outcome": OUTCOME.get(status, status.lower()),
                                 "elapsed_ms": int((time.monotonic() - t0) * 1000), "http_status": 200})
                r = QueryResult(key, dql, status, recs, qid, err, int((time.monotonic() - t0) * 1000), notes,
                                call_id=call_id, attempts=attempts)
                return self._end(r, elapsed_ms=int((time.monotonic() - t_all) * 1000), traced=traced,
                                 http_status=200, preview=preview)
            except asyncio.CancelledError:
                # Paralel bir çağrı MCP'yi erişilemez buldu ve oturum kapandı: kayıt açık kalmasın
                attempts.append({"attempt": attempt + 1, "outcome": "cancelled",
                                 "elapsed_ms": int((time.monotonic() - t0) * 1000)})
                self._emit({"phase": "end", "key": key, "dql": dql, "status": "CANCELLED", "outcome": "cancelled",
                            "records": None, "query_id": None, "elapsed_ms": int((time.monotonic() - t_all) * 1000),
                            "error": None, "error_summary": "çağrı iptal edildi (paralel çağrı MCP'yi erişilemez buldu)",
                            "attempts": len(attempts), "attempt_log": attempts, "mcp_call_id": call_id,
                            "traced": traced, "sent": True, "connector": SERVER, "tool": DQL_TOOL,
                            "http_status": None, "request_preview": preview})
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                code = http_status_of(e)
                oc = "timeout" if isinstance(e, TimeoutError) else \
                    "permission_denied" if code in (401, 403) else "unavailable"
                attempts.append({"attempt": attempt + 1, "outcome": oc,
                                 "elapsed_ms": int((time.monotonic() - t0) * 1000),
                                 "error_type": type(e).__name__, "http_status": code})
                if oc == "permission_denied":           # yetki hatası tekrar denenmez
                    break
                if attempt < self.retries:               # son denemeden sonra beklemeden hata döner
                    await asyncio.sleep(min(2 ** attempt, 8))
        kind = "timeout" if isinstance(last, TimeoutError) else "unavailable"
        outcome = attempts[-1]["outcome"]
        err = redact_text(f"{type(last).__name__}: {last}")
        self._emit({"phase": "end", "key": key, "dql": dql,
                    "status": {"timeout": "TIMEOUT", "permission_denied": "PERMISSION"}.get(outcome, "UNAVAILABLE"),
                    "outcome": outcome, "records": None, "query_id": None,
                    "elapsed_ms": int((time.monotonic() - t_all) * 1000), "error": err,
                    "error_summary": safe_error_summary(err), "attempts": len(attempts), "attempt_log": attempts,
                    "mcp_call_id": call_id, "traced": traced, "sent": True, "connector": SERVER, "tool": DQL_TOOL,
                    "http_status": attempts[-1].get("http_status"), "request_preview": preview})
        ex = McpUnavailable(err, kind=kind)
        ex.attempts = attempts
        raise ex

    async def _many(self, items: list[tuple[str, str]]) -> list[QueryResult]:
        async with self.client.session(SERVER) as session:
            if self.expected_tool_hash:
                await self._verify_tool(session)
            sem = asyncio.Semaphore(self.concurrency)

            async def run(k, d):
                async with sem:
                    return await self._one(session, k, d)
            return list(await asyncio.gather(*(run(k, d) for k, d in items)))

    async def _verify_tool(self, session) -> None:
        res = await asyncio.wait_for(session.list_tools(), self.timeout_s)
        t = next((x for x in res.tools if x.name == DQL_TOOL), None)
        got = tool_definition_hash(t) if t is not None else None
        if got != self.expected_tool_hash:
            msg = (f"'{DQL_TOOL}' tool tanımı onaylanan sürümden farklı; connector yeniden incelenmeli"
                   if got else f"sunucu '{DQL_TOOL}' tool'unu artık sunmuyor")
            self._emit({"phase": "integrity", "tool": DQL_TOOL, "expected": self.expected_tool_hash[:12],
                        "got": (got or "")[:12], "error": msg})
            raise McpUnavailable(msg, kind="integrity")

    def run_many(self, items: list[tuple[str, str]]) -> dict[str, QueryResult]:
        """Sorguları tek MCP oturumunda (sınırlı eşzamanlılıkla) çalıştırır. MCP yoksa McpUnavailable."""
        if not items:
            return {}
        self._trace_parent = parent_config()          # çağıran graf adımı; DQL'ler onun altında tool run olur
        try:
            results = asyncio.run(asyncio.wait_for(self._many(items), self.timeout_s * (2 + len(items) / 2)))
        except McpUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise McpUnavailable(redact_text(f"{type(e).__name__}: {e}"),
                                 kind="timeout" if isinstance(e, TimeoutError) else "unavailable") from None
        self.log.extend(results)
        return {r.key: r for r in results}
