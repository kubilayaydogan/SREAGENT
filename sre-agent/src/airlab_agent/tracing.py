"""LangSmith izleme — güvenli varsayılan: LangSmith'e yalnız İZİNLİ alanlar ve özetler gider.

Veri kontrolü (allowlist; yalnız eklenen metadata'ya değil, otomatik nested trace'ler dahil her run'a uygulanır):
* `hide_inputs` / `hide_outputs` → `trace_safe`: yalnız izinli skaler alanlar (run_id, status, süre, sayılar, model
  adı, token kullanımı…) ve kodun ürettiği `step_summary` / `extra_decision` özetleri kalır. Ham agent state'i,
  kullanıcı sorusu, prompt ve model çıktısı, ham DQL, MCP payload'ı, Dynatrace kayıtları atılır (yalnız atılan
  alanların ADLARI `_omitted` altında görünür).
* `hide_metadata` → `safe_metadata`: yalnız izinli anahtarlar (platform kimlikleri, LangGraph düğüm bilgisi, `ls_*`
  model bilgisi) skaler değerleriyle + `usage_metadata`.
* `hide_*` fonksiyonlarından geçmeyen run alanları (`serialized` model repr'ı, `extra.invocation_params`) tracer'da
  gönderimden önce süzülür: yalnız model adı ve sayısal parametreler kalır.
* Hata/stack trace → `SafeTracer._get_stacktrace`: yalnız exception tipi (ve kodun ürettiği `TraceSafeError`
  mesajı); ham mesaj ve traceback gönderilmez. Streaming token olayları kaydedilmez.
LangSmith kapalı ya da erişilemez olsa da agent çalışmaya devam eder (istemci arka planda gönderir, hatalar yalnız
loglanır; `timeout_ms=5000`).
"""
from __future__ import annotations

import contextvars
import logging
import os
from collections import OrderedDict
from datetime import date, datetime
from typing import Any

from .redact import redact_text

log = logging.getLogger("airlab_agent.tracing")

# Sampling dışı ya da ayarlardan kapatılmış koşular: bu bağlamdaki LLM/graf çağrıları trace göndermez.
_TRACING_OFF: contextvars.ContextVar[bool] = contextvars.ContextVar("airlab_tracing_off", default=False)


def tracing_off() -> bool:
    return _TRACING_OFF.get()


def without_tracing(fn, *args, **kwargs):
    """`fn`'i tracing kapalı bağlamda çalıştırır (thread içinde de geçerli: to_thread bağlamı kopyalar)."""
    token = _TRACING_OFF.set(True)
    try:
        return fn(*args, **kwargs)
    finally:
        _TRACING_OFF.reset(token)


# ------------------------------------------------------------------ veri kontrolü (allowlist)
MAX_STR = 200
MAX_LIST = 50
# Değeri skaler (str/sayı/bool/None) ise gönderilen alanlar. Liste/dict değer taşırlarsa atılır.
SAFE_SCALARS = frozenset({
    # analiz grafı (kök/düğüm girdi-çıktıları)
    "run_id", "platform_run_id", "mode", "status", "iterations", "start", "end", "baseline_start", "baseline_end",
    "use_llm", "fixture", "explanation_source", "generated_at", "window_source", "baseline_source",
    # MCP span
    "connector", "tool", "query_key", "mcp_call_id", "outcome", "records", "elapsed_ms", "evidence_id",
    "dynatrace_query_id", "attempt", "attempts_count", "error_type", "result_status",
    # LLM çıktısı: model ve token kullanımı (içerik değil)
    "input_tokens", "output_tokens", "total_tokens", "cache_read", "cache_creation", "model_name", "model",
    "stop_reason", "finish_reason", "validation",
})
# İçine inilen (yine allowlist ile süzülen) yapılar
SAFE_NESTED = frozenset({
    "generations", "message", "kwargs", "usage_metadata", "input_token_details", "output_token_details",
    "response_metadata", "llm_output", "usage", "generation_info", "raw", "time_range", "attempts", "output",
})
# Kodun kendisinin ürettiği özetler: anahtar adları serbest, değerler yine redakte + kısaltılır
TRUSTED = frozenset({"step_summary", "extra_decision"})


def _scalar(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, str):
        return redact_text(v, "trace")[:MAX_STR]
    return f"<{type(v).__name__}>"


def _trusted(v: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "<depth>"
    if isinstance(v, dict):
        return {str(k)[:64]: _trusted(x, depth + 1) for k, x in list(v.items())[:MAX_LIST]}
    if isinstance(v, (list, tuple, set)):
        return [_trusted(x, depth + 1) for x in list(v)[:MAX_LIST]]
    return _scalar(v)


def _safe(v: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<depth>"
    if isinstance(v, (list, tuple)):
        return [_safe(x, depth + 1) for x in list(v)[:MAX_LIST]]
    if not isinstance(v, dict):
        return _scalar(v) if depth == 0 else None
    out: dict[str, Any] = {}
    omitted: list[str] = []
    for k, x in v.items():
        if k in TRUSTED:
            out[k] = _trusted(x)
        elif k == "parsing_error":                      # structured output validation sonucu (mesaj değil)
            out["validation"] = "failed" if x else "ok"
        elif k in SAFE_SCALARS and not isinstance(x, (dict, list, tuple, set)):
            out[k] = _scalar(x)
        elif k in SAFE_NESTED and isinstance(x, (dict, list, tuple)):
            out[k] = _safe(x, depth + 1)
        else:
            omitted.append(str(k)[:64])
    if "parsed" in v and "validation" not in out:
        out["validation"] = "ok" if v.get("parsed") is not None else "failed"
    if omitted:
        out["_omitted"] = sorted(omitted)[:30]
    return out


def trace_safe(d: Any) -> dict:
    """LangSmith'e gidecek inputs/outputs: allowlist dışındaki her şey atılır."""
    r = _safe(d)
    return r if isinstance(r, dict) else {"value": r}


SAFE_META = frozenset({
    "run_id", "platform_run_id", "thread_id", "mode", "trigger", "environment", "attempt", "resumed",
    "investigation_id", "conversation_id", "user_id", "application_id", "config_hash", "model", "window_start",
    "window_end", "connector", "tool", "query_key", "mcp_call_id", "checkpoint_ns", "revision_id",
})
SAFE_META_PREFIX = ("ls_", "langgraph_")
SAFE_INVOCATION = frozenset({"model", "model_name", "_type", "max_tokens", "temperature", "top_p", "top_k",
                             "stream"})


def safe_metadata(m: dict) -> dict:
    """Metadata allowlist'i: izinli anahtarların skaler değerleri + usage_metadata. Liste/dict değerler atılır."""
    out: dict[str, Any] = {}
    for k, v in (m or {}).items():
        if k == "usage_metadata" and isinstance(v, dict):
            out[k] = _safe(v, 1)
        elif (k in SAFE_META or k.startswith(SAFE_META_PREFIX)) and not isinstance(v, (dict, list, tuple, set)):
            out[k] = _scalar(v)
    return out


def scrub_run(run) -> None:
    """hide_* fonksiyonlarının görmediği run alanları: model repr'ı (serialized) ve invocation_params."""
    try:
        run.serialized = {}
        ex = run.extra if isinstance(run.extra, dict) else {}
        ip = ex.get("invocation_params")
        if isinstance(ip, dict):
            ex["invocation_params"] = {k: v for k, v in ip.items()
                                       if k in SAFE_INVOCATION and isinstance(v, (str, int, float, bool))}
        ex.pop("options", None)
    except Exception:  # noqa: BLE001
        pass


class TraceSafeError(RuntimeError):
    """Mesajı kodun kendisinin ürettiği (ham veri içermeyen) hata: trace'e mesajıyla gider."""


def safe_error(error: BaseException) -> str:
    """Trace'e giden hata metni: exception tipi (+ varsa güvenli sınıflandırma). Ham mesaj/traceback yok."""
    name = type(error).__name__
    if isinstance(error, TraceSafeError):
        return f"{name}: {redact_text(str(error), 'trace')[:300]}"
    kind = getattr(error, "kind", None)
    return name + (f" (kind={kind})" if isinstance(kind, str) else "") + " — ayrıntı platform kaydında"


# ------------------------------------------------------------------ tracer
_TRACER_CLS = None
TRACE_ROOT_NAMES = ("airlab-", "chat-turn")


def _tracer_cls():
    """LangChainTracer'ın güvenli alt sınıfı (langchain_core yalnız tracing açıkken içe aktarılır)."""
    global _TRACER_CLS
    if _TRACER_CLS is not None:
        return _TRACER_CLS
    from langchain_core.tracers.langchain import LangChainTracer

    class SafeTracer(LangChainTracer):
        """* Hata metni: yalnız exception tipi (safe_error). * Streaming token olayı kaydedilmez.
        * Analiz/sohbet run'larının LangSmith trace_id'si hatırlanır (platform run_id ↔ trace_id eşlemesi)."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._trace_ids: OrderedDict[str, str] = OrderedDict()

        @staticmethod
        def _get_stacktrace(error: BaseException) -> str:
            return safe_error(error)

        def _llm_run_with_token_event(self, token, run_id, chunk=None, parent_run_id=None):
            return self._get_run(run_id, run_type={"llm", "chat_model"})

        def _persist_run_single(self, run) -> None:
            scrub_run(run)
            super()._persist_run_single(run)

        def _update_run_single(self, run) -> None:
            scrub_run(run)
            LangChainTracer._update_run_single(run)

        def _start_trace(self, run) -> None:
            super()._start_trace(run)
            try:
                if (run.name or "").startswith(TRACE_ROOT_NAMES):
                    self._trace_ids[str(run.id)] = str(run.trace_id)
                    while len(self._trace_ids) > 4096:
                        self._trace_ids.popitem(last=False)
            except Exception:  # noqa: BLE001
                pass

        def trace_id_for(self, run_id) -> str | None:
            return self._trace_ids.get(str(run_id))

    _TRACER_CLS = SafeTracer
    return SafeTracer


def make_client(api_key: str | None, *, cls=None, **kw):
    """Güvenli LangSmith istemcisi: inputs/outputs/metadata allowlist'ten geçer. `cls`: test için Client alt sınıfı."""
    from langsmith import Client
    return (cls or Client)(api_key=api_key, hide_inputs=trace_safe, hide_outputs=trace_safe, hide_metadata=safe_metadata,
                  timeout_ms=kw.pop("timeout_ms", 5000), **kw)


def make_tracer(project: str, client):
    return _tracer_cls()(project_name=project, client=client, tags=["airlab", "sre-agent"])


def trace_id_for(callbacks: list | None, run_id) -> str | None:
    """Verilen run'ın LangSmith trace_id'si (kök değilse üst trace'in kimliği). Tracer yoksa None."""
    for cb in callbacks or []:
        f = getattr(cb, "trace_id_for", None)
        if f is not None:
            t = f(run_id)
            if t:
                return t
    return None


def setup_tracing(settings) -> list:
    # Ortamdaki otomatik izleme bayraklarını kapat: yalnız aşağıdaki güvenli tracer kullanılsın.
    for k in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        os.environ[k] = "false"
    if not settings.langsmith_tracing:
        return []
    try:
        logging.getLogger("langsmith").setLevel(logging.ERROR)
        return [make_tracer(settings.langsmith_project, make_client(settings.langsmith_key))]
    except Exception as e:  # noqa: BLE001
        log.warning("LangSmith tracing başlatılamadı, tracing olmadan devam: %s", type(e).__name__)
        return []


def langsmith_ping(settings) -> tuple[bool, str]:
    if not settings.langsmith_key:
        return False, "LANGSMITH_API_KEY yok"
    try:
        import httpx
        r = httpx.get("https://api.smith.langchain.com/info", headers={"x-api-key": settings.langsmith_key}, timeout=10)
        return r.status_code == 200, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


# ------------------------------------------------------------------ trace hiyerarşisi
def parent_config() -> dict | None:
    """Şu an çalışan LangChain/LangGraph adımının (varsa) çocuk config'i. Thread'e/asyncio.run'a contextvar ile taşınır."""
    try:
        from langchain_core.runnables.config import var_child_runnable_config
        return var_child_runnable_config.get()
    except Exception:  # noqa: BLE001
        return None


def child_config(callbacks: list | None, run_name: str, **extra) -> dict:
    """Bir LLM/graf çağrısı için config. Üstte çalışan bir adım varsa callbacks VERİLMEZ: LangChain üst adımın
    callback yöneticisini devralır ve çağrı aynı trace'in altına yerleşir. Üst adım yoksa (CLI, tek başına çağrı)
    verilen tracer'larla yeni kök trace açılır."""
    cfg: dict = {"run_name": run_name, **extra}
    if _TRACING_OFF.get():
        cfg["callbacks"] = []
        return cfg
    p = parent_config()
    if not (p and p.get("callbacks")):
        cfg["callbacks"] = callbacks or []
    return cfg


class ToolSpan:
    """LangChain dışı bir aracı (Dynatrace MCP) üstteki trace'e tool run olarak ekler. Tracing yoksa no-op.
    `run_id` verilirse span'in LangSmith kimliği odur (MCP çağrısında platformdaki mcp_call_id)."""

    def __init__(self, parent: dict | None, name: str, inputs: dict, metadata: dict | None = None, run_id=None):
        self.rm = None
        if not (parent and parent.get("callbacks")) or _TRACING_OFF.get():
            return
        try:
            from langchain_core.runnables.config import get_callback_manager_for_config
            cm = get_callback_manager_for_config(parent)
            if metadata:
                cm.add_metadata(metadata, inherit=False)       # on_tool_start metadata kwarg'ını kabul etmiyor
            # input_str ham sorgu DEĞİL (yalnız ad); asıl girdiler allowlist'ten geçen `inputs`
            self.rm = cm.on_tool_start({"name": name}, name, run_name=name, inputs=inputs, run_id=run_id)
        except Exception:  # noqa: BLE001 — izleme hatası sorguyu etkilemez
            self.rm = None

    @property
    def active(self) -> bool:
        return self.rm is not None

    def end(self, outputs: dict) -> None:
        if self.rm is not None:
            try:
                self.rm.on_tool_end(outputs)
            except Exception:  # noqa: BLE001
                pass

    def error(self, message: str) -> None:
        """`message` kodun ürettiği güvenli özet olmalı (ham hata metni değil)."""
        if self.rm is not None:
            try:
                self.rm.on_tool_error(TraceSafeError(message))
            except Exception:  # noqa: BLE001
                pass
