"""LangSmith izleme. Girdi/çıktılar gönderilmeden önce redakte edilir; LangSmith erişilemezse agent çalışmaya devam eder
(istemci arka planda gönderir, hatalar yalnız loglanır)."""
from __future__ import annotations

import contextvars
import logging
import os

from .redact import redact

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


def _hide(d):
    return redact(d) if isinstance(d, dict) else {"value": redact(d)}


def setup_tracing(settings) -> list:
    # Ortamdaki otomatik izleme bayraklarını kapat: yalnız aşağıdaki redakte eden tracer kullanılsın.
    for k in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        os.environ[k] = "false"
    if not settings.langsmith_tracing:
        return []
    try:
        from langchain_core.tracers.langchain import LangChainTracer
        from langsmith import Client

        logging.getLogger("langsmith").setLevel(logging.ERROR)
        client = Client(api_key=settings.langsmith_key, hide_inputs=_hide, hide_outputs=_hide, timeout_ms=5000)
        return [LangChainTracer(project_name=settings.langsmith_project, client=client,
                                tags=["airlab", "sre-agent"])]
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
    """LangChain dışı bir aracı (Dynatrace MCP DQL) üstteki trace'e tool run olarak ekler. Tracing yoksa no-op."""

    def __init__(self, parent: dict | None, name: str, inputs: dict, metadata: dict | None = None):
        self.rm = None
        if not (parent and parent.get("callbacks")):
            return
        try:
            from langchain_core.runnables.config import get_callback_manager_for_config
            cm = get_callback_manager_for_config(parent)
            if metadata:
                cm.add_metadata(metadata, inherit=False)       # on_tool_start metadata kwarg'ını kabul etmiyor
            self.rm = cm.on_tool_start({"name": name}, str(inputs.get("dql", "")), run_name=name, inputs=inputs)
        except Exception:  # noqa: BLE001 — izleme hatası sorguyu etkilemez
            self.rm = None

    def end(self, outputs: dict) -> None:
        if self.rm is not None:
            try:
                self.rm.on_tool_end(outputs)
            except Exception:  # noqa: BLE001
                pass

    def error(self, message: str) -> None:
        if self.rm is not None:
            try:
                self.rm.on_tool_error(RuntimeError(message))
            except Exception:  # noqa: BLE001
                pass
