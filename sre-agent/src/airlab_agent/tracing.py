"""LangSmith izleme. Girdi/çıktılar gönderilmeden önce redakte edilir; LangSmith erişilemezse agent çalışmaya devam eder
(istemci arka planda gönderir, hatalar yalnız loglanır)."""
from __future__ import annotations

import logging
import os

from .redact import redact

log = logging.getLogger("airlab_agent.tracing")


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
