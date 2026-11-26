"""Context window gözlemi: her gerçek LLM çağrısı (ve her HTTP denemesi) için kapasite, planlanan bütçe, gerçekleşen
kullanım ve girdi dağılımı. Gözlem amaçlıdır; prompt, tool seçimi ve akış değişmez. Kayıt hatası çağrıyı etkilemez.

Bağlandığı katmanlar
* `middleware.run_model_sync / run_model_async / stream_model`: tüm model çağrılarının ortak yolu (Llm.interpret,
  Llm.explain, ClaudePort.route, ClaudePort.astream). Middleware'in engellediği çağrı sağlayıcıya gitmediği için kayıt
  üretmez.
* `TracedChatAnthropic._get_request_payload`: sağlayıcıya GERÇEKTEN gönderilen payload; dağılım buradan hesaplanır
  (redaction ve kırpma sonrası içerik). Ham içerik saklanmaz, yalnız token sayısı ve güvenli referans.
* Anthropic SDK'nın httpx istemcisine eklenen event hook'ları: SDK içi retry'ların her denemesi ayrı kayıttır
  (`x-stainless-retry-count`), aynı `logical_call_id` ile bağlanır.

Ölçüm kaynağı
* Girdi / çıktı / cache / thinking token sayıları: sağlayıcının `usage` alanı (streaming'de final usage).
* Bileşen dağılımı: yerel tahmin (karakter / 4, Agent Memory ile aynı yöntem); dağıtılamayan fark "Diğer / sağlayıcı ek
  yükü" olarak sağlayıcı toplamından çıkarılır.
* Kapasite: Anthropic Models API (`max_input_tokens`); alınamazsa bilinmiyor (yüzde hesaplanmaz).
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import re
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

log = logging.getLogger("airlab.context_window")

PROVIDER = "anthropic"
ESTIMATE_METHOD = "karakter / 4 (yerel tahmin; sağlayıcı tokenizer'ı çağrılmaz)"

COMPONENTS: dict[str, str] = {
    "system": "Sistem talimatları",
    "user_message": "Kullanıcı mesajı",
    "history": "Geçmiş mesajlar",
    "summary": "Özet (önceki turlar)",
    "state": "Node'dan aktarılan state",
    "rag": "RAG / memory kayıtları",
    "tool_results": "MCP / tool sonuçları",
    "tool_definitions": "Tool tanımları",
    "output_schema": "Structured output şeması",
    "other": "Diğer / sağlayıcı ek yükü",
}
# İstemdeki veri blokları → bileşen. <analysis_data> Dynatrace MCP sonuçlarından türetilmiş özettir (ham çıktı değil).
TAG_COMPONENT = {"analysis_data": "tool_results", "history_data": "rag", "memory_data": "rag", "rca_data": "rag",
                 "context_data": None}
CONTEXT_KEYS = {"previous_answer": "history", "earlier_turns": "summary", "focus": "state", "last_investigation": "rag"}
TASK_NODE = {"interpret_question": "interpret", "explain_findings": "explain", "route_question": "route",
             "answer": "respond", "rca_summary": "rca_summary"}
_REF_KEYS = ("id", "investigation_id", "incident_id", "rca_id", "runbook_id", "known_issue_id", "episode_id", "run_id",
             "memory_id", "ref")
_TAG_RE = re.compile(r"<(\w+_data)>\n?(.*?)\n?</\1>", re.S)

CAPACITY_TTL_S = 6 * 3600
CAPACITY_RETRY_S = 600


def estimate_tokens(text: str | None) -> int:
    return math.ceil(len(text) / 4) if text else 0


# ================================================================================================ sink / kapasite
_SINK: Callable[[dict], None] | None = None
_RESOLVER: Callable[[str], dict | None] | None = None
_CAP: dict[str, dict] = {}
_CAP_LOCK = threading.Lock()
_CAP_INFLIGHT: set[str] = set()
_STATS = {"lost": 0, "recorded": 0}


def set_sink(fn: Callable[[dict], None] | None) -> None:
    global _SINK
    _SINK = fn


def set_capacity_resolver(fn: Callable[[str], dict | None] | None, *, prefetch: list[str] | None = None) -> None:
    """`fn(model)` bloklayan bir sorgudur (ör. Models API); yalnız arka plan thread'inde çağrılır."""
    global _RESOLVER
    _RESOLVER = fn
    with _CAP_LOCK:
        _CAP.clear()
    for m in prefetch or []:
        capacity(m)


def stats() -> dict:
    return dict(_STATS)


def _fetch(model: str) -> None:
    fn = _RESOLVER
    info: dict
    try:
        r = fn(model) if fn else None
        if r and r.get("capacity"):
            info = {"capacity": int(r["capacity"]), "max_output": r.get("max_output"),
                    "source": r.get("source") or "Models API (max_input_tokens)", "at": time.monotonic()}
        else:
            info = {"capacity": None, "max_output": None, "source": "unknown",
                    "note": "sağlayıcı kapasite bildirmedi", "at": time.monotonic(), "retry": True}
    except Exception as e:  # noqa: BLE001 — kapasite bilinmiyor; çağrı etkilenmez
        info = {"capacity": None, "max_output": None, "source": "unknown",
                "note": f"Models API alınamadı ({type(e).__name__})", "at": time.monotonic(), "retry": True}
    with _CAP_LOCK:
        _CAP[model] = info
        _CAP_INFLIGHT.discard(model)


def capacity(model: str | None, *, wait: bool = False) -> dict:
    """Önbellekteki kapasite. Yoksa arka planda alınır; bu çağrı için "bilinmiyor" döner (çağrı beklemez)."""
    if not model:
        return {"capacity": None, "max_output": None, "source": "unknown", "note": "model bilinmiyor"}
    with _CAP_LOCK:
        c = _CAP.get(model)
        stale = c is None or (time.monotonic() - c["at"] > (CAPACITY_RETRY_S if c.get("retry") else CAPACITY_TTL_S))
        start = stale and _RESOLVER is not None and model not in _CAP_INFLIGHT
        if start:
            _CAP_INFLIGHT.add(model)
    if start:
        if wait:
            _fetch(model)
        else:
            threading.Thread(target=_fetch, args=(model,), name="cw-capacity", daemon=True).start()
        with _CAP_LOCK:
            c = _CAP.get(model) or c
    if c is None:
        return {"capacity": None, "max_output": None, "source": "unknown",
                "note": "Models API yanıtı henüz yok" if _RESOLVER else "kapasite kaynağı yapılandırılmadı"}
    return {k: c.get(k) for k in ("capacity", "max_output", "source", "note")}


# ================================================================================================ context kararları
_PENDING: ContextVar[list | None] = ContextVar("airlab_cw_pending", default=None)


def note_context(section: str, *, included: list | None = None, summarized: list | None = None,
                 dropped: list | None = None, reason: str | None = None, token_estimate: int | None = None,
                 status: str | None = None) -> None:
    """Context hazırlama kararı (dahil / özetlendi / çıkarıldı + gerekçe). Aynı context'teki sonraki model çağrısına
    bağlanır. Yalnız referans ve sayı tutulur, içerik tutulmaz."""
    try:
        lst = _PENDING.get()
        if lst is None:
            lst = []
            _PENDING.set(lst)
        lst.append({"section": section, "status": status, "reason": reason, "token_estimate": token_estimate,
                    "included": _refs_list(included), "summarized": _refs_list(summarized),
                    "dropped": _refs_list(dropped)})
    except Exception:  # noqa: BLE001
        log.debug("context kararı kaydedilemedi", exc_info=True)


def note_history_window(history: list[dict], *, keep: int, max_chars: int) -> None:
    """Yönlendirme / yorumlama istemine giren konuşma geçmişi: son `keep` mesaj, mesaj başına `max_chars` karakter."""
    if not history:
        return
    kept = history[-keep:]
    n = len(history)
    dropped: list = []
    if n > keep:
        dropped.append({"ref": f"en eski {n - keep} mesaj", "count": n - keep, "reason": f"yalnız son {keep} mesaj"})
    for i, h in enumerate(kept):
        if len(h.get("content") or "") > max_chars:
            dropped.append({"ref": f"mesaj -{len(kept) - i} ({h.get('role')}) kısmen", "reason":
                            f"{max_chars} karakter sonrası kırpıldı", "chars": len(h["content"]) - max_chars})
    note_context("conversation_history", status="windowed",
                 included=[{"ref": f"mesaj -{len(kept) - i} ({h.get('role')})"} for i, h in enumerate(kept)],
                 dropped=dropped, reason=f"son {keep} mesaj, mesaj başına {max_chars} karakter")


def note_analysis_payload(payload: dict | None) -> None:
    """Analiz istemi: Dynatrace MCP sonuçlarının ham hali değil, deterministik analiz özeti gönderilir."""
    if not isinstance(payload, dict):
        return
    note_context("analysis_payload", status="summarized",
                 included=[{"ref": f"alan:{k}"} for k, v in payload.items() if v not in (None, [], {})],
                 summarized=[{"ref": "Dynatrace MCP tool sonuçları", "reason":
                              "ham MCP çıktısı yerine analiz özeti (risk, mechanism, features, coverage) gönderildi"}],
                 reason="ham tool çıktısı modele gönderilmez")


def _refs_list(v) -> list:
    out = []
    for x in (v or [])[:50]:
        if isinstance(x, dict):
            out.append({k: (str(x[k])[:120] if x[k] is not None else None) for k in list(x)[:4]
                        if k in ("ref", "id", "kind", "reason", "type", "count", "chars")})
        else:
            out.append(str(x)[:120])
    return out


def _take_pending() -> list | None:
    lst = _PENDING.get()
    if not lst:
        return None
    out = list(lst)
    lst.clear()
    return out


# ================================================================================================ çağrı durumu
@dataclass
class CwCall:
    logical_id: str
    task: str | None
    node: str | None
    model: str | None
    run: Any = None
    seq: int | None = None
    envelope_id: str | None = None
    streaming: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    t0: float = field(default_factory=time.perf_counter)
    hints: list = field(default_factory=list)
    payload: dict | None = None
    trace_call_id: str | None = None
    attempts: list = field(default_factory=list)
    decisions: list | None = None
    usage: dict | None = None
    stop_reason: str | None = None
    served_model: str | None = None
    stream: dict | None = None


CURRENT: ContextVar[CwCall | None] = ContextVar("airlab_cw_call", default=None)


def _node_name(task: str | None) -> str | None:
    try:
        from langgraph.config import get_config
        n = (get_config().get("metadata") or {}).get("langgraph_node")
        if n:
            return str(n)
    except Exception:  # noqa: BLE001
        pass
    return TASK_NODE.get(task or "", task)


def begin(call, *, envelope_id: str | None = None, streaming: bool = False):
    """Model çağrısı sağlayıcıya gitmeden önce. Dönen token `end` ile kapatılır."""
    try:
        from . import middleware as mw
        run = mw.RUN.get()
        seq = None
        if run is not None:
            ctr = getattr(run, "_cw_seq", None)
            if ctr is None:
                ctr = itertools.count(1)
                run._cw_seq = ctr
            seq = next(ctr)
        c = CwCall(logical_id=str(uuid.uuid4()), task=call.task, node=_node_name(call.task), model=call.target,
                   run=run, seq=seq, envelope_id=envelope_id, streaming=streaming,
                   hints=list(getattr(call, "context_hints", None) or []), decisions=_take_pending())
        return CURRENT.set(c)
    except Exception:  # noqa: BLE001
        _STATS["lost"] += 1
        log.debug("context window kaydı başlatılamadı", exc_info=True)
        return None


def on_payload(payload: dict) -> None:
    """Sağlayıcı payload'ı (gerçekte gönderilen). Yalnız referansı tutulur; dağılım `end`'de hesaplanır."""
    c = CURRENT.get()
    if c is None:
        return
    try:
        c.payload = payload
        from .agent_trace import _CALL
        cur = _CALL.get()
        if cur is not None:
            c.trace_call_id = cur[1]
    except Exception:  # noqa: BLE001
        pass


def on_chunk(chunk) -> None:
    """Streaming: chunk'lardaki usage parçaları toplanır (final usage); içerik tutulmaz."""
    c = CURRENT.get()
    if c is None:
        return
    try:
        u = getattr(chunk, "usage_metadata", None)
        if u:
            from langchain_core.messages.ai import add_usage
            c.usage = add_usage(c.usage, u) if c.usage else dict(u)
        rm = getattr(chunk, "response_metadata", None) or {}
        if rm.get("stop_reason"):
            c.stop_reason = rm["stop_reason"]
        if rm.get("model_name") or rm.get("model"):
            c.served_model = rm.get("model_name") or rm.get("model")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------ HTTP denemeleri
def _is_messages(request) -> bool:
    try:
        p = request.url.path
        return p.endswith("/v1/messages")
    except Exception:  # noqa: BLE001
        return False


def on_http_request(request) -> None:
    c = CURRENT.get()
    if c is None or not _is_messages(request):
        return
    try:
        n = int(request.headers.get("x-stainless-retry-count") or len(c.attempts))
    except (TypeError, ValueError):
        n = len(c.attempts)
    c.attempts.append({"attempt_no": n, "t0": time.perf_counter(), "started_at": datetime.now(timezone.utc),
                       "http_status": None, "t1": None})


def on_http_response(response) -> None:
    c = CURRENT.get()
    if c is None or not c.attempts or not _is_messages(response.request):
        return
    a = c.attempts[-1]
    if a["http_status"] is None:
        a["http_status"] = response.status_code
        a["t1"] = time.perf_counter()
        a["request_id"] = (response.headers.get("request-id") or "")[:80] or None


def instrument(chat_model) -> bool:
    """ChatAnthropic'in Anthropic istemcilerinin httpx client'ına deneme hook'ları ekler (bir kez). Başarısızsa retry
    denemeleri ayrı görünmez; çağrı etkilenmez."""
    ok = False
    for attr, is_async in (("_client", False), ("_async_client", True)):
        try:
            hc = getattr(getattr(chat_model, attr), "_client", None)
            if hc is None or getattr(hc, "_airlab_cw", False):
                ok = ok or hc is not None
                continue
            if is_async:
                async def req(r):
                    on_http_request(r)

                async def resp(r):
                    on_http_response(r)
            else:
                req, resp = on_http_request, on_http_response
            hc.event_hooks["request"].append(req)
            hc.event_hooks["response"].append(resp)
            hc._airlab_cw = True
            ok = True
        except Exception:  # noqa: BLE001
            log.debug("context window HTTP hook'u eklenemedi (%s)", attr, exc_info=True)
    return ok


# ------------------------------------------------------------------------------------------------ dağılım
def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    out.append(str(b.get("text") or ""))
                elif b.get("type") in ("tool_result", "tool_use"):
                    out.append(json.dumps(b, ensure_ascii=False, default=str))
            else:
                out.append(str(b))
        return "\n".join(out)
    return str(content)


def _walk_refs(obj, out: list, depth: int = 0) -> None:
    if len(out) >= 30 or depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _REF_KEYS and isinstance(v, (str, int)) and len(str(v)) <= 80:
                out.append(f"{k}:{v}")
            else:
                _walk_refs(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:50]:
            _walk_refs(v, out, depth + 1)


def _block_refs(tag: str, inner: str) -> list[str]:
    try:
        data = json.loads(inner)
    except Exception:  # noqa: BLE001
        return []
    if tag == "analysis_data" and isinstance(data, dict):
        return [f"alan:{k}" for k in data if data.get(k) not in (None, [], {})][:30]
    out: list[str] = []
    _walk_refs(data, out)
    return sorted(set(out))[:30]


def breakdown(payload: dict | None, hints: list | None = None) -> tuple[dict[str, int], dict[str, list]]:
    """Gerçekte gönderilen payload'dan bileşen başına TAHMİNİ token. Bilinmeyen alanlar sayılmaz (Diğer'e düşer)."""
    acc: dict[str, int] = defaultdict(int)
    refs: dict[str, list] = defaultdict(list)
    if not payload:
        return {}, {}

    def strip_hints(text: str) -> str:
        for comp, sub in hints or []:
            if sub and sub in text:
                acc[comp] += estimate_tokens(sub)
                text = text.replace(sub, "", 1)
        return text

    acc["system"] += estimate_tokens(strip_hints(_text(payload.get("system"))))
    msgs = payload.get("messages") or []
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    for i, m in enumerate(msgs):
        t = strip_hints(_text(m.get("content")))
        if m.get("role") == "system":
            acc["system"] += estimate_tokens(t)
        elif i != last_user:
            acc["history"] += estimate_tokens(t)
        else:
            rest = t
            for mt in _TAG_RE.finditer(t):
                tag, inner = mt.group(1), mt.group(2)
                rest = rest.replace(mt.group(0), "", 1)
                comp = TAG_COMPONENT.get(tag, "state")
                if comp is None:                       # <context_data>: anahtar başına
                    try:
                        data = json.loads(inner)
                    except Exception:  # noqa: BLE001
                        data = None
                    if isinstance(data, dict):
                        used = 0
                        for k, v in data.items():
                            if v in (None, "", [], {}):
                                continue
                            n = estimate_tokens(json.dumps(v, ensure_ascii=False, default=str))
                            ck = CONTEXT_KEYS.get(k, "state")
                            acc[ck] += n
                            used += n
                            if ck == "rag":
                                r: list[str] = []
                                _walk_refs(v, r)
                                refs[ck] += r
                            else:
                                refs[ck].append(f"context_data.{k}")
                        acc["state"] += max(0, estimate_tokens(mt.group(0)) - used)
                    else:
                        acc["state"] += estimate_tokens(mt.group(0))
                    continue
                acc[comp] += estimate_tokens(mt.group(0))
                refs[comp] += [f"<{tag}>"] + _block_refs(tag, inner)
            acc["user_message"] += estimate_tokens(rest)
    if payload.get("tools"):
        acc["tool_definitions"] += estimate_tokens(json.dumps(payload["tools"], ensure_ascii=False, default=str))
    for key in ("output_config", "output_format", "response_format"):
        v = payload.get(key)
        sch = None
        if isinstance(v, dict):
            sch = v.get("format") if isinstance(v.get("format"), dict) else (v if "schema" in v else None)
        if sch:
            acc["output_schema"] += estimate_tokens(json.dumps(sch, ensure_ascii=False, default=str))
    return {k: v for k, v in acc.items() if v}, {k: sorted(set(v))[:30] for k, v in refs.items() if v}


# ------------------------------------------------------------------------------------------------ sonuç
_CONTEXT_LIMIT_TEXT = ("prompt is too long", "context window", "context_length", "maximum context length")


def _classify_error(e: BaseException) -> tuple[str, str, int | None]:
    status = getattr(e, "status_code", None)
    msg = str(e).lower()
    if any(t in msg for t in _CONTEXT_LIMIT_TEXT):
        return "context_limit", "prompt_too_long", status
    name = type(e).__name__
    if name in ("CancelledError", "GeneratorExit"):
        return "cancelled", "cancelled", status
    if "Timeout" in name:
        return "error", "timeout", status
    if status == 429:
        return "error", "rate_limited", status
    if status == 529:
        return "error", "overloaded", status
    return "error", name[:64], status


def _attempt_code(status: int | None) -> str:
    if status is None:
        return "connection_or_timeout"
    return {429: "rate_limited", 529: "overloaded", 408: "timeout"}.get(status, f"http_{status}")


def _usage_of(msg) -> dict | None:
    if msg is None:
        return None
    if isinstance(msg, dict) and "raw" in msg:
        msg = msg.get("raw")
    u = getattr(msg, "usage_metadata", None)
    return dict(u) if u else None


def end(token, *, result=None, error: BaseException | None = None, stream: dict | None = None) -> None:
    if token is None:
        return
    c = CURRENT.get()
    try:
        CURRENT.reset(token)
    except Exception:  # noqa: BLE001
        pass
    if c is None:
        return
    try:
        rows = _rows(c, result, error, stream)
    except Exception:  # noqa: BLE001
        _STATS["lost"] += 1
        log.warning("context window kaydı üretilemedi", exc_info=True)
        return
    sink = _SINK
    if sink is None:
        return
    for r in rows:
        try:
            sink(r)
            _STATS["recorded"] += 1
        except Exception:  # noqa: BLE001 — telemetry hatası çağrıyı etkilemez
            _STATS["lost"] += 1


def _rows(c: CwCall, result, error, stream) -> list[dict]:
    raw = result.get("raw") if isinstance(result, dict) else result
    if raw is not None:
        rm = getattr(raw, "response_metadata", None) or {}
        c.stop_reason = c.stop_reason or rm.get("stop_reason")
        c.served_model = c.served_model or rm.get("model_name") or rm.get("model")
    usage = c.usage if c.streaming else (_usage_of(raw) or c.usage)
    ended = datetime.now(timezone.utc)
    duration = round((time.perf_counter() - c.t0) * 1000, 1)

    # durum
    if error is not None:
        status, code, http = _classify_error(error)
    elif c.stop_reason == "model_context_window_exceeded":
        status, code, http = "context_limit", "model_context_window_exceeded", None
    elif stream and stream.get("interrupted"):
        status, code, http = "cancelled", "stream_interrupted", None
    else:
        status, code, http = "ok", None, None

    cap = capacity(c.model)
    p = c.payload or {}
    est, refs = breakdown(c.payload, c.hints)
    est_input = sum(est.values()) if c.payload else None
    reserved = p.get("max_tokens") if isinstance(p.get("max_tokens"), int) else None

    in_tot = out_tot = cache_read = cache_create = reasoning = None
    if usage:
        in_tot = usage.get("input_tokens")
        out_tot = usage.get("output_tokens")
        d_in = usage.get("input_token_details") or {}
        d_out = usage.get("output_token_details") or {}
        cache_read = d_in.get("cache_read")
        cache_create = d_in.get("cache_creation")
        if cache_create is None and (d_in.get("ephemeral_5m_input_tokens") or d_in.get("ephemeral_1h_input_tokens")):
            cache_create = (d_in.get("ephemeral_5m_input_tokens") or 0) + (d_in.get("ephemeral_1h_input_tokens") or 0)
        reasoning = d_out.get("reasoning")

    capv = cap.get("capacity")
    planned = round((est_input + reserved) / capv, 6) if capv and est_input is not None and reserved else None
    actual = round((in_tot + out_tot) / capv, 6) if capv and in_tot is not None and out_tot is not None else None

    comps = [{"key": k, "label": COMPONENTS[k], "tokens": v, "method": "estimate", "refs": refs.get(k, [])}
             for k, v in sorted(est.items(), key=lambda kv: -kv[1])]
    over = None
    if c.payload:
        if in_tot is not None and est_input is not None:
            diff = in_tot - est_input
            comps.append({"key": "other", "label": COMPONENTS["other"], "tokens": max(diff, 0),
                          "method": "provider_minus_estimate", "refs": []})
            if diff < 0:
                over = -diff
        else:
            comps.append({"key": "other", "label": COMPONENTS["other"], "tokens": None, "method": "unknown",
                          "refs": []})

    run = c.run
    base = {
        "logical_call_id": c.logical_id, "run_id": getattr(run, "run_id", None), "run_kind": getattr(run, "kind", None),
        "thread_id": getattr(run, "thread_id", None), "user_id": getattr(run, "user_id", None),
        "node": c.node, "task": c.task, "call_seq": c.seq, "provider": PROVIDER, "model": c.model,
        "served_model": c.served_model if c.served_model and c.served_model != c.model else None,
        "streaming": c.streaming, "capacity": capv, "capacity_source": cap.get("source"),
        "capacity_note": cap.get("note"), "model_max_output": cap.get("max_output"),
        "reserved_output": reserved, "estimated_input": est_input,
        "estimate_method": ESTIMATE_METHOD if c.payload else None,
        "trace_call_id": c.trace_call_id, "middleware_execution_id": c.envelope_id,
    }
    attempts = c.attempts or []
    rows: list[dict] = []
    for i, a in enumerate(attempts[:-1]):           # önceki (retry edilen) denemeler
        t1 = a["t1"] or (attempts[i + 1]["t0"])
        rows.append({**base, "call_id": str(uuid.uuid4()), "attempt_no": a["attempt_no"], "attempt_count": len(attempts),
                     "started_at": a["started_at"],
                     "ended_at": a["started_at"] + timedelta(seconds=max(t1 - a["t0"], 0)),
                     "duration_ms": round((t1 - a["t0"]) * 1000, 1),
                     "status": "retried", "error_code": _attempt_code(a["http_status"]), "http_status": a["http_status"],
                     "stop_reason": None, "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
                     "cache_creation_tokens": None, "reasoning_tokens": None, "usage_source": "missing",
                     "planned_fill": planned, "actual_fill": None, "components": None, "estimate_over": None,
                     "context_decisions": None, "stream": None, "request_id": a.get("request_id")})
    last = attempts[-1] if attempts else None
    rows.append({**base, "call_id": str(uuid.uuid4()) if attempts[:-1] else c.logical_id,
                 "attempt_no": last["attempt_no"] if last else 0, "attempt_count": max(len(attempts), 1),
                 "started_at": last["started_at"] if last else c.started_at, "ended_at": ended,
                 "duration_ms": duration if not last else round((time.perf_counter() - last["t0"]) * 1000, 1),
                 "status": status, "error_code": code,
                 "http_status": http if http is not None else (last or {}).get("http_status"),
                 "stop_reason": c.stop_reason, "input_tokens": in_tot, "output_tokens": out_tot,
                 "cache_read_tokens": cache_read, "cache_creation_tokens": cache_create,
                 "reasoning_tokens": reasoning, "usage_source": "provider" if usage else "missing",
                 "planned_fill": planned, "actual_fill": actual, "components": comps or None, "estimate_over": over,
                 "context_decisions": c.decisions, "stream": stream if c.streaming else None,
                 "request_id": (last or {}).get("request_id"),
                 "error_message": (str(error)[:300] if error is not None and status == "context_limit" else None)})
    return rows
