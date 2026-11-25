"""Agent Trace: LangGraph node/state/stream, LLM çağrısı ve Pydantic doğrulama olaylarının yerel kaydı.

LangSmith'ten bağımsızdır (LangSmith'e allowlist gider; burada tam ama MASKELİ kopya platform DB'sine yazılır).
Kayıt yalnız gözlemdir: model girdisi ve agent state'i değiştirilmez — her olay verinin derin, maskelenmiş bir
KOPYASIDIR. Kayıttaki her hata yutulur ve sayılır; agent çalışması durmaz.

Yakalama noktaları
------------------
* LangGraph `debug` stream'i (`stream_mode=["updates", "debug"]`): `task` → State In (node'a verilen girdi),
  `task_result` → Node Update (node'un gerçek dönüş değeri), `checkpoint` → State Out (graph adımı sonunda reducer'lar
  uygulanmış state) ve `next` (gözlemlenen geçiş). `updates` mevcut tüketiciye (`on_node`) aynı biçimde iletilir.
* LangChain callback'leri (`AgentTraceCallback`): chat model başlangıç/bitiş/hata ve token chunk'ları (Framework
  payload), `PydanticOutputParser` runnable'ının girdi/çıktı/hatası (Parser Input / Validated Output / Validation
  Error) ve koşullu kenar fonksiyonlarının dönüşü (routing kararı).
* `TracedChatAnthropic`: sağlayıcıya giden request JSON'u (`_get_request_payload`) ve sağlayıcı yanıtı
  (`_format_output`, streaming olmayan çağrılar) — Provider payload.

Maskeleme (`safe_copy`): mevcut redaksiyon kuralları (`redact`, sink="trace") + hassas anahtar adları (authorization,
api_key, password, secret, token, cookie) + DQL alanları şablona çevrilir; boyut sınırı aşılırsa kırpılır ve
işaretlenir. Maskeleme kalıcı depoya, canlı yayına ve loga ulaşmadan ÖNCE, olay oluşturulurken yapılır.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable

from .redact import redact

log = logging.getLogger("airlab.agent_trace")

MAX_EVENT_BYTES = 200_000         # olay başına maskeli JSON üst sınırı
MAX_STR = 20_000                  # tek string
MAX_ITEMS = 200                   # liste / dict öğe sayısı
MAX_DEPTH = 14
MAX_CHUNKS = 400                  # streaming chunk kaydı
PREVIEW_CHARS = 16_000

_SENSITIVE_KEY = re.compile(r"(?i)^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api[-_]?key|"
                            r"[\w-]*password[\w-]*|passwd|[\w-]*secret[\w-]*|(access|refresh|id|auth|bearer)?[-_]?token)$")
_DQL_KEYS = {"dql", "dqlQueryString"}

# Etkin kaydedici: callback'ler başka bir kaydedicinin (iç içe analiz) olaylarını almasın diye
ACTIVE: contextvars.ContextVar["TraceRecorder | None"] = contextvars.ContextVar("airlab_agent_trace", default=None)
# Adaptör sınırındaki LLM çağrısı (kaydedici, LangChain run id)
_CALL: contextvars.ContextVar[tuple | None] = contextvars.ContextVar("airlab_agent_trace_call", default=None)


# ------------------------------------------------------------------ maskeleme ve boyut
def _jsonable(o: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "…(derinlik sınırı)"
    if o is None or isinstance(o, (bool, int, float, str)):
        return o
    if isinstance(o, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        seq = sorted(o, key=str) if isinstance(o, (set, frozenset)) else o
        return [_jsonable(v, depth + 1) for v in seq]
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, BaseException):
        return {"error_type": type(o).__name__, "message": str(o)}
    try:
        from langchain_core.messages import BaseMessage
        if isinstance(o, BaseMessage):
            return _message(o, depth)
    except Exception:  # noqa: BLE001
        pass
    dump = getattr(o, "model_dump", None)
    if callable(dump):
        try:
            return _jsonable(dump(mode="json"), depth + 1)
        except Exception:  # noqa: BLE001
            pass
    return repr(o)[:2000]


_ROLE = {"human": "user", "ai": "assistant", "AIMessageChunk": "assistant", "system": "system", "tool": "tool"}


def _message(m, depth: int = 0) -> dict:
    out = {"role": _ROLE.get(m.type, m.type), "content": _jsonable(m.content, depth + 1)}
    for k in ("name", "tool_calls", "invalid_tool_calls", "tool_call_id", "usage_metadata", "response_metadata", "id"):
        v = getattr(m, k, None)
        if v:
            out[k] = _jsonable(v, depth + 1)
    return out


def _records_summary(rows: list) -> dict:
    """Ham telemetri kaydı (log satırı, span, metrik serisi) trace'e kopyalanmaz: MCP Çağrıları sayfasındaki gibi
    yalnız sayı ve alan adları tutulur. Agent'ın gerçek state'i değişmez."""
    fields = sorted({str(k) for r in rows[:50] if isinstance(r, dict) for k in r})
    return {"_records_omitted": True, "count": len(rows), "fields": fields[:60],
            "reason": "ham telemetri trace kopyasına alınmaz"}


def _mask(o: Any, key: str | None = None) -> Any:
    if key == "records" and isinstance(o, list):
        return _records_summary(o)
    if isinstance(o, dict):
        return {k: _mask(v, k) for k, v in o.items()}
    if isinstance(o, list):
        return [_mask(v, key) for v in o]
    if isinstance(o, str):
        if key and _SENSITIVE_KEY.match(key):
            return "***"
        if key in _DQL_KEYS:
            from .mcp_gateway import dql_template
            return dql_template(o, 4000)
        return redact(o, "trace")
    return o


def _shrink(o: Any, flags: dict) -> Any:
    if isinstance(o, str) and len(o) > MAX_STR:
        flags["truncated"] = True
        return o[:MAX_STR] + f"…(+{len(o) - MAX_STR} karakter kırpıldı)"
    if isinstance(o, list):
        items = [_shrink(v, flags) for v in o[:MAX_ITEMS]]
        if len(o) > MAX_ITEMS:
            flags["truncated"] = True
            items.append({"_truncated_items": len(o) - MAX_ITEMS})
        return items
    if isinstance(o, dict):
        out = {}
        for i, (k, v) in enumerate(o.items()):
            if i >= MAX_ITEMS:
                flags["truncated"] = True
                out["_truncated_keys"] = len(o) - MAX_ITEMS
                break
            out[k] = _shrink(v, flags)
        return out
    return o


def safe_copy(obj: Any) -> tuple[Any, bool, int]:
    """Derin, maskeli, boyutu sınırlı kopya → (veri, kırpıldı mı, bayt). Orijinal nesneye dokunulmaz."""
    flags = {"truncated": False}
    data = _shrink(_mask(_jsonable(obj)), flags)
    raw = json.dumps(data, ensure_ascii=False, default=str)
    if len(raw.encode()) > MAX_EVENT_BYTES:
        flags["truncated"] = True
        data = {"_truncated": True, "_original_bytes": len(raw.encode()), "preview": raw[:PREVIEW_CHARS]}
        raw = json.dumps(data, ensure_ascii=False)
    return data, flags["truncated"], len(raw.encode())


# ------------------------------------------------------------------ state diff
def _flatten(o: Any, path: str, out: dict, limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(o, dict) and o:
        for k, v in o.items():
            _flatten(v, f"{path}.{k}" if path else str(k), out, limit)
    elif isinstance(o, list) and o:
        for i, v in enumerate(o):
            _flatten(v, f"{path}[{i}]", out, limit)
    else:
        out[path or "$"] = o


def state_diff(before: Any, after: Any, limit: int = 5000) -> list[dict]:
    """JSON path düzeyinde eklenen / silinen / değişen alanlar (yaprak değerler)."""
    a, b = {}, {}
    _flatten(before if before is not None else {}, "", a, limit)
    _flatten(after if after is not None else {}, "", b, limit)
    out = []
    for p in sorted(set(a) | set(b)):
        if p not in a:
            out.append({"path": p, "change": "added", "old": None, "new": b[p]})
        elif p not in b:
            out.append({"path": p, "change": "removed", "old": a[p], "new": None})
        elif a[p] != b[p]:
            out.append({"path": p, "change": "changed", "old": a[p], "new": b[p]})
    return out


# ------------------------------------------------------------------ kaydedici
class TraceRecorder:
    """Bir agent çalıştırmasının olaylarını sıra numarasıyla üretir ve `sink`'e verir. Thread-safe.
    Olay: event_id, sequence, timestamp, event_type, node, node_exec_id, step, attempt, call_id, related_call_id,
    status, duration_ms, payload (maskeli), truncated, size_bytes."""

    def __init__(self, run_id: str, sink: Callable[[dict], None], *, graph: str | None = None):
        self.run_id, self.sink, self.graph = run_id, sink, graph
        self._lock = threading.RLock()
        self._seq = 0
        self.capture_errors = 0
        self.truncated = 0
        self.events = 0
        self._execs: dict[str, dict] = {}            # LangGraph task id → {node, step, attempt, t0}
        self._node_count: dict[str, int] = {}
        self._step_tasks: dict[int, list[str]] = {}
        self._routes: dict[str, list] = {}            # task id → routing kararları
        self._runs: dict[str, dict] = {}              # LangChain run id → {parent, name, kind, exec, t0, ...}
        self._llm_by_exec: dict[tuple, int] = {}
        self._last_llm: dict[str, str] = {}           # exec → son LLM çağrısı (parser eşlemesi)
        self.callback = AgentTraceCallback(self)

    # -------------------------------------------------- olay
    def emit(self, event_type: str, payload: Any = None, *, node: str | None = None, node_exec_id: str | None = None,
             step: int | None = None, attempt: int | None = None, call_id: str | None = None,
             related_call_id: str | None = None, status: str | None = None, duration_ms: int | None = None) -> None:
        try:
            data, trunc, size = safe_copy(payload if payload is not None else {})
        except Exception as e:  # noqa: BLE001 — maskeleme başarısızsa veri YAZILMAZ (sızıntı yerine kayıp)
            self.capture_errors += 1
            log.warning("agent trace: olay verisi maskelenemedi run=%s tür=%s hata=%s", self.run_id, event_type,
                        type(e).__name__)
            data, trunc, size = {"_capture_failed": True, "error_type": type(e).__name__}, False, 0
        with self._lock:
            self._seq += 1
            ev = {"event_id": str(uuid.uuid4()), "sequence": self._seq,
                  "timestamp": datetime.now(timezone.utc).isoformat(), "event_type": event_type, "node": node,
                  "node_exec_id": node_exec_id, "step": step, "attempt": attempt, "call_id": call_id,
                  "related_call_id": related_call_id, "status": status, "duration_ms": duration_ms,
                  "payload": data, "truncated": trunc, "size_bytes": size}
            self.events += 1
            self.truncated += int(trunc)
            try:
                self.sink(ev)
            except Exception as e:  # noqa: BLE001
                self.capture_errors += 1
                log.warning("agent trace: olay iletilemedi run=%s seq=%s tür=%s hata=%s", self.run_id, self._seq,
                            event_type, type(e).__name__)

    def _safe(self, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001 — kayıt hatası agent'ı durdurmaz
            self.capture_errors += 1
            log.warning("agent trace: yakalama hatası run=%s yer=%s hata=%s", self.run_id, getattr(fn, "__name__", fn),
                        type(e).__name__)

    # -------------------------------------------------- LangGraph stream
    def on_stream(self, mode: str, chunk: Any, namespace: tuple = ()) -> None:
        self._safe(self._on_stream, mode, chunk, namespace)

    def _on_stream(self, mode, chunk, namespace):
        ns = list(namespace or ())
        if mode == "updates":
            for node, upd in (chunk or {}).items():
                ex = self._latest_exec(node)
                self.emit("stream", {"mode": "updates", "namespace": ns, "node": node,
                                     "keys": sorted((upd or {}).keys()) if isinstance(upd, dict) else None,
                                     "data_ref": "Node Update"}, node=node, node_exec_id=ex)
            return
        if mode != "debug":
            self.emit("stream", {"mode": mode, "namespace": ns}, node=None)
            return
        typ, step, p = chunk.get("type"), chunk.get("step"), chunk.get("payload") or {}
        if typ == "task":
            tid, node = p.get("id"), p.get("name")
            with self._lock:
                self._node_count[node] = self._node_count.get(node, 0) + 1
                att = self._node_count[node]
                self._execs[tid] = {"node": node, "step": step, "attempt": att, "t0": time.monotonic()}
                self._step_tasks.setdefault(step, []).append(tid)
            self.emit("stream", {"mode": "debug", "namespace": ns, "type": "task", "step": step,
                                 "data_ref": "State In"}, node=node, node_exec_id=tid, step=step, attempt=att)
            self.emit("node_start", {"state_in": p.get("input"), "triggers": list(p.get("triggers") or [])},
                      node=node, node_exec_id=tid, step=step, attempt=att, status="running")
        elif typ == "task_result":
            tid, node = p.get("id"), p.get("name")
            ex = self._execs.get(tid, {})
            err = p.get("error")
            self.emit("stream", {"mode": "debug", "namespace": ns, "type": "task_result", "step": step,
                                 "data_ref": "Node Update"}, node=node, node_exec_id=tid, step=step,
                      attempt=ex.get("attempt"))
            self.emit("node_end", {"node_update": p.get("result"),
                                   "error": {"error_type": type(err).__name__, "message": str(err)} if err else None},
                      node=node, node_exec_id=tid, step=step, attempt=ex.get("attempt"),
                      status="error" if err else "ok",
                      duration_ms=int((time.monotonic() - ex["t0"]) * 1000) if ex.get("t0") else None)
        elif typ == "checkpoint":
            tasks = self._step_tasks.get(step, [])
            nodes = [self._execs[t]["node"] for t in tasks if t in self._execs]
            nxt = list(p.get("next") or [])
            self.emit("stream", {"mode": "debug", "namespace": ns, "type": "checkpoint", "step": step,
                                 "data_ref": "State Out"}, step=step)
            self.emit("state_out", {"values": p.get("values"), "next": nxt, "node_exec_ids": tasks, "nodes": nodes,
                                    "combined": len(tasks) > 1,
                                    "checkpoint_id": ((p.get("config") or {}).get("configurable") or {})
                                    .get("checkpoint_id"),
                                    "source": (p.get("metadata") or {}).get("source")}, step=step)
            if tasks or step == 0:
                routes = {self._execs[t]["node"]: self._routes.get(t) for t in tasks if t in self._execs}
                self.emit("transition", {"from": nodes or ["__start__"], "from_exec_ids": tasks, "to": nxt,
                                         "routing": {k: v for k, v in routes.items() if v} or None,
                                         "observed_by": "checkpoint.next"}, step=step)

    def _latest_exec(self, node: str) -> str | None:
        for tid in reversed(list(self._execs)):
            if self._execs[tid]["node"] == node:
                return tid
        return None

    def exec_of(self, metadata: dict | None) -> tuple[str | None, dict]:
        ns = (metadata or {}).get("langgraph_checkpoint_ns") or ""
        tid = ns.split("|")[0].split(":", 1)[1] if ":" in ns else None
        return tid, self._execs.get(tid, {}) if tid else {}

    def record(self, event_type: str, payload: Any = None, **kw) -> None:
        """Agent kodundan açık işaret (ör. LLM yanıtı ayrıştırılamadı → deterministik yedek)."""
        self._safe(self.emit, event_type, payload, **kw)


def mark(event_type: str, payload: Any = None) -> None:
    """Etkin kaydedici varsa olay ekler; yoksa no-op. Çağrıldığı LangGraph node'una otomatik bağlanır."""
    rec = ACTIVE.get()
    if rec is None:
        return
    md = {}
    try:
        from langchain_core.runnables.config import var_child_runnable_config
        md = (var_child_runnable_config.get() or {}).get("metadata") or {}
    except Exception:  # noqa: BLE001
        pass
    tid, ex = rec.exec_of(md)
    rec.record(event_type, payload, node=ex.get("node"), node_exec_id=tid, step=ex.get("step"),
               attempt=ex.get("attempt"))


# ------------------------------------------------------------------ LangChain callback
def _handler_base():
    from langchain_core.callbacks import BaseCallbackHandler
    return BaseCallbackHandler


class AgentTraceCallback(_handler_base()):
    """LLM, parser ve routing olaylarını kaydedicisine yazar. `run_inline`: olay sırası korunur.
    `airlab_local`: LangSmith kapalıyken (sampling dışı) bile devralınan yerel handler'dır."""
    run_inline = True
    raise_error = False
    airlab_local = True

    def __init__(self, rec: TraceRecorder):
        super().__init__()
        self.rec = rec

    def _mine(self) -> bool:
        a = ACTIVE.get()
        return a is None or a is self.rec

    # -------------------------------------------------- yardımcılar
    def _register(self, run_id, parent_run_id, name, kind, metadata, tags) -> dict:
        tid, ex = self.rec.exec_of(metadata)
        info = {"parent": str(parent_run_id) if parent_run_id else None, "name": name, "kind": kind, "exec": tid,
                "node": ex.get("node"), "step": ex.get("step"), "node_attempt": ex.get("attempt"),
                "t0": time.monotonic(), "tags": list(tags or []), "chunks": [], "chunks_dropped": 0,
                "lg_node": (metadata or {}).get("langgraph_node")}
        self.rec._runs[str(run_id)] = info
        return info

    def _ancestors(self, rid: str) -> list[str]:
        out, seen = [], 0
        cur = self.rec._runs.get(rid, {}).get("parent")
        while cur and seen < 50:
            out.append(cur)
            cur = self.rec._runs.get(cur, {}).get("parent")
            seen += 1
        return out

    # -------------------------------------------------- chain: parser ve routing
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kw):
        if not self._mine():
            return
        self.rec._safe(self._chain_start, serialized, inputs, run_id, parent_run_id, tags, metadata, kw)

    def _chain_start(self, serialized, inputs, run_id, parent_run_id, tags, metadata, kw):
        name = kw.get("name") or (serialized or {}).get("name") or ""
        ids = " ".join((serialized or {}).get("id") or [])
        kind = "parser" if ("OutputParser" in name or "OutputParser" in ids) else "chain"
        info = self._register(run_id, parent_run_id, name, kind, metadata, tags)
        if kind == "parser":
            rid = str(run_id)
            llm = self._match_llm(rid, info.get("exec"))
            info["llm"] = llm
            text = _text_of(inputs)
            parsed_json = None
            try:
                parsed_json = json.loads(text) if isinstance(text, str) else None
            except Exception:  # noqa: BLE001
                pass
            self.rec.emit("parser_input", {"parser": name, "input_type": type(inputs).__name__, "text": text,
                                           "json": parsed_json, "raw_input": inputs},
                          node=info["node"], node_exec_id=info["exec"], step=info["step"],
                          attempt=info["node_attempt"], call_id=rid, related_call_id=llm, status="running")

    def _match_llm(self, parser_rid: str, exec_id: str | None) -> str | None:
        node_run = self._node_run(exec_id) if exec_id else None
        outer = ({node_run, *self._ancestors(node_run)} if node_run else set())   # node ve üstü ortak sayılmaz
        anc = set(self._ancestors(parser_rid)) - outer
        cands = [r for r, i in self.rec._runs.items() if i.get("kind") == "llm" and i.get("exec") == exec_id]
        for r in reversed(cands):                       # aynı structured output zincirindeki LLM çağrısı
            if anc & set(self._ancestors(r)):
                return r
        return self.rec._last_llm.get(exec_id or "")

    def _node_run(self, exec_id):
        return next((r for r, i in self.rec._runs.items() if i.get("exec") == exec_id and i.get("kind") == "chain"
                     and i.get("name") == i.get("lg_node")), None)

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kw):
        if not self._mine():
            return
        self.rec._safe(self._chain_end, outputs, run_id)

    def _chain_end(self, outputs, run_id):
        rid = str(run_id)
        info = self.rec._runs.get(rid)
        if not info:
            return
        dur = int((time.monotonic() - info["t0"]) * 1000)
        if info["kind"] == "parser":
            self.rec.emit("parser_output", {"parser": info["name"], "model": type(outputs).__name__,
                                            "validated": outputs},
                          node=info["node"], node_exec_id=info["exec"], step=info["step"],
                          attempt=info["node_attempt"], call_id=rid, related_call_id=info.get("llm"), status="ok",
                          duration_ms=dur)
            return
        # koşullu kenar fonksiyonu: node run'ının çocuğu, adı node'dan farklı, dönüşü hedef adı
        parent = self.rec._runs.get(info.get("parent") or "", {})
        is_branch = (info.get("exec") and parent.get("name") == parent.get("lg_node")
                     and parent.get("exec") == info.get("exec") and info["name"] != info.get("lg_node")
                     and any(t.startswith("seq:step") for t in info["tags"])
                     and (isinstance(outputs, str) or (isinstance(outputs, list) and all(isinstance(x, str) for x in outputs))))
        if is_branch:
            self.rec._routes.setdefault(info["exec"], []).append({"function": info["name"], "result": outputs})
            self.rec.emit("routing", {"function": info["name"], "result": outputs, "source": info.get("node")},
                          node=info.get("node"), node_exec_id=info["exec"], step=info["step"],
                          attempt=info["node_attempt"], status="ok", duration_ms=dur)

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kw):
        if not self._mine():
            return
        self.rec._safe(self._chain_error, error, run_id)

    def _chain_error(self, error, run_id):
        rid = str(run_id)
        info = self.rec._runs.get(rid)
        if not info or info["kind"] != "parser":
            return
        self.rec.emit("validation_error", {"parser": info["name"], **validation_details(error)},
                      node=info["node"], node_exec_id=info["exec"], step=info["step"], attempt=info["node_attempt"],
                      call_id=rid, related_call_id=info.get("llm"), status="error",
                      duration_ms=int((time.monotonic() - info["t0"]) * 1000))

    # -------------------------------------------------- chat model
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, tags=None, metadata=None,
                            invocation_params=None, **kw):
        if not self._mine():
            return
        self.rec._safe(self._llm_start, serialized, messages, run_id, parent_run_id, tags, metadata,
                       invocation_params, kw)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, tags=None, metadata=None, **kw):
        if not self._mine():
            return
        self.rec._safe(self._llm_start, serialized, [[{"role": "user", "content": p} for p in prompts]], run_id,
                       parent_run_id, tags, metadata, kw.get("invocation_params"), kw)

    def _llm_start(self, serialized, messages, run_id, parent_run_id, tags, metadata, params, kw):
        name = kw.get("name") or (serialized or {}).get("name") or "llm"
        info = self._register(run_id, parent_run_id, name, "llm", metadata, tags)
        rid = str(run_id)
        task = (metadata or {}).get("ls_run_name") or self._task_name(rid) or name
        key = (info.get("exec"), task)
        with self.rec._lock:
            self.rec._llm_by_exec[key] = self.rec._llm_by_exec.get(key, 0) + 1
            info["attempt"] = self.rec._llm_by_exec[key]
        info["task"] = task
        self.rec._last_llm[info.get("exec") or ""] = rid
        params = dict(params or {})
        schema = ((metadata or {}).get("ls_structured_output_format") or params.pop("ls_structured_output_format", None)
                  or (kw.get("options") or {}).get("ls_structured_output_format"))
        msgs = messages[0] if messages and isinstance(messages[0], list) else messages
        self.rec.emit("llm_request", {
            "source": "framework", "task": task, "model": params.get("model") or params.get("model_name")
            or (metadata or {}).get("ls_model_name") or (metadata or {}).get("model"),
            "provider": (metadata or {}).get("ls_provider"), "messages": msgs, "params": params,
            "structured_output": schema, "tools": params.get("tools")},
            node=info["node"], node_exec_id=info["exec"], step=info["step"], attempt=info["attempt"], call_id=rid,
            status="running")

    def _task_name(self, rid: str) -> str | None:
        """child_config(run_name=…) → LLM'i saran adım adı (route_question, explain_findings, answer…)."""
        for a in self._ancestors(rid):
            n = self.rec._runs.get(a, {}).get("name")
            if n and n not in ("RunnableSequence", "RunnableParallel<raw>", "RunnableMap") and not n.startswith("Runnable"):
                return n
        return None

    def on_llm_new_token(self, token, *, chunk=None, run_id, **kw):
        info = self.rec._runs.get(str(run_id))
        if not info or not self._mine():
            return
        if len(info["chunks"]) < MAX_CHUNKS:
            info["chunks"].append({"index": len(info["chunks"]) + info["chunks_dropped"], "text": token,
                                   "t_ms": int((time.monotonic() - info["t0"]) * 1000)})
        else:
            info["chunks_dropped"] += 1

    def on_llm_end(self, response, *, run_id, **kw):
        if not self._mine():
            return
        self.rec._safe(self._llm_end, response, run_id, None)

    def on_llm_error(self, error, *, run_id, **kw):
        if not self._mine():
            return
        self.rec._safe(self._llm_end, None, run_id, error)

    def _llm_end(self, response, run_id, error):
        rid = str(run_id)
        info = self.rec._runs.get(rid) or {}
        gen = None
        try:
            gen = response.generations[0][0] if response is not None else None
        except Exception:  # noqa: BLE001
            gen = None
        msg = getattr(gen, "message", None)
        prov = info.pop("provider_response", None)
        payload = {
            "source": "framework", "task": info.get("task"),
            "message": msg if msg is not None else (getattr(gen, "text", None) if gen is not None else None),
            "content": getattr(msg, "content", None), "tool_calls": getattr(msg, "tool_calls", None),
            "finish_reason": ((getattr(msg, "response_metadata", None) or {}).get("stop_reason")
                              or (getattr(gen, "generation_info", None) or {}).get("finish_reason")),
            "usage": getattr(msg, "usage_metadata", None) or (getattr(response, "llm_output", None) or {}).get("usage"),
            "llm_output": getattr(response, "llm_output", None),
            "streamed": bool(info.get("chunks")), "chunks": info.get("chunks") or [],
            "chunks_dropped": info.get("chunks_dropped", 0), "chunk_limit": MAX_CHUNKS,
            "provider_response": prov,
            "provider_response_status": "captured" if prov is not None else
            ("not_capturable_streaming" if info.get("chunks") or info.get("streaming") else "not_captured"),
            "error": {"error_type": type(error).__name__, "message": str(error)[:2000]} if error else None}
        self.rec.emit("llm_response", payload, node=info.get("node"), node_exec_id=info.get("exec"),
                      step=info.get("step"), attempt=info.get("attempt"), call_id=rid,
                      status="error" if error else "ok",
                      duration_ms=int((time.monotonic() - info["t0"]) * 1000) if info.get("t0") else None)


def _text_of(inputs: Any) -> Any:
    """Parser'a verilen AIMessage'ın metni (PydanticOutputParser bunu JSON olarak ayrıştırır)."""
    m = inputs.get("input", inputs) if isinstance(inputs, dict) and "input" in inputs else inputs
    t = getattr(m, "text", None)             # langchain-core 1.x: property (str alt sınıfı)
    if isinstance(t, str):
        return str(t)
    c = getattr(m, "content", None)
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return m if isinstance(m, str) else None


def validation_details(error: BaseException) -> dict:
    """Pydantic ValidationError'ı exception zincirinde arar: alan yolu (loc), tür, mesaj. Bulunamazsa genel hata."""
    seen, cur = 0, error
    while cur is not None and seen < 10:
        errs = getattr(cur, "errors", None)
        if callable(errs) and type(cur).__name__ == "ValidationError":
            try:
                items = cur.errors(include_url=False, include_input=False)
            except TypeError:
                items = cur.errors()
            return {"error_type": "ValidationError", "model": getattr(cur, "title", None),
                    "errors": [{"loc": list(e.get("loc") or []), "path": ".".join(str(x) for x in e.get("loc") or []),
                                "type": e.get("type"), "message": e.get("msg")} for e in items],
                    "wrapped_by": type(error).__name__ if cur is not error else None}
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return {"error_type": type(error).__name__, "model": None, "errors": [],
            "message": str(error)[:2000]}


# ------------------------------------------------------------------ sağlayıcı adaptörü
def traced_chat_anthropic_cls():
    """ChatAnthropic alt sınıfı: sağlayıcı request/response JSON'unu kaydedicinin LLM çağrısına bağlar.
    Davranış değişmez; yalnız `_get_request_payload` dönüşü ve `_format_output` girdisi kopyalanır."""
    from langchain_anthropic import ChatAnthropic

    def _rec_of(run_manager):
        for h in (getattr(run_manager, "handlers", None) or []):
            if isinstance(h, AgentTraceCallback):
                return h.rec
        return None

    class TracedChatAnthropic(ChatAnthropic):
        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            cur = _CALL.get()
            if cur is not None:
                rec, rid, streaming = cur
                info = rec._runs.get(rid) or {}
                if streaming:
                    info["streaming"] = True
                rec._safe(rec.emit, "llm_provider_request", {"source": "provider", "payload": payload,
                                                              "streaming": streaming},
                          node=info.get("node"), node_exec_id=info.get("exec"), step=info.get("step"),
                          attempt=info.get("attempt"), call_id=rid, status="sent")
            return payload

        def _format_output(self, data, **kwargs):
            cur = _CALL.get()
            if cur is not None:
                rec, rid, _ = cur
                try:
                    dump = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
                    (rec._runs.setdefault(rid, {}))["provider_response"] = dump
                except Exception:  # noqa: BLE001
                    rec.capture_errors += 1
            return super()._format_output(data, **kwargs)

        def _wrap(self, run_manager, streaming):
            rec = _rec_of(run_manager)
            return _CALL.set((rec, str(run_manager.run_id), streaming)) if rec is not None else None

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tok = self._wrap(run_manager, False) if run_manager else None
            try:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            finally:
                if tok is not None:
                    _CALL.reset(tok)

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            tok = self._wrap(run_manager, False) if run_manager else None
            try:
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            finally:
                if tok is not None:
                    _CALL.reset(tok)

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            tok = self._wrap(run_manager, True) if run_manager else None
            try:
                yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
            finally:
                if tok is not None:
                    try:
                        _CALL.reset(tok)
                    except ValueError:
                        pass

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            tok = self._wrap(run_manager, True) if run_manager else None
            try:
                async for c in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yield c
            finally:
                if tok is not None:
                    try:
                        _CALL.reset(tok)
                    except ValueError:              # farklı context'te kapanan async generator
                        pass

    return TracedChatAnthropic


# ------------------------------------------------------------------ graph çalıştırma yardımcıları
STREAM_MODES = ["updates", "debug"]


def with_callback(cfg: dict, rec: TraceRecorder | None) -> dict:
    """Config'e kaydedicinin callback'ini ekler (mevcut callback'ler korunur)."""
    if rec is None:
        return cfg
    cbs = cfg.get("callbacks")
    if cbs is None:                               # üst adımın callback yöneticisi devralınır (LangSmith iç içeliği)
        try:
            from langchain_core.runnables.config import var_child_runnable_config
            cbs = (var_child_runnable_config.get() or {}).get("callbacks")
        except Exception:  # noqa: BLE001
            cbs = None
    if cbs is None:
        cbs = []
    if isinstance(cbs, list):
        cfg["callbacks"] = [*cbs, rec.callback]
    else:                                         # CallbackManager
        cbs = cbs.copy()
        cbs.add_handler(rec.callback, inherit=True)
        cfg["callbacks"] = cbs
    return cfg


def local_handlers(callbacks: Any) -> list:
    """LangSmith kapalıyken de devralınacak yerel handler'lar (Agent Trace)."""
    hs = callbacks if isinstance(callbacks, list) else (getattr(callbacks, "handlers", None) or [])
    return [h for h in hs if getattr(h, "airlab_local", False)]
