"""Ajan çalışma zamanı: DB motoru, LangGraph checkpointer/store havuzları, MCP gateway fabrikası, LLM bağlantısı,
eşzamanlılık sınırı. FastAPI uygulaması ve worker tek bir örnek oluşturur."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from airlab_agent.config import Settings, load_settings
from airlab_agent.redact import register_secrets

from ..core.config import PlatformSettings
from ..db.migrate import langgraph_conninfo
from ..db.session import make_engine, make_sessionmaker
from .llm_port import ClaudePort, LlmPort
from .memory import MemoryService

log = logging.getLogger("sre.runtime")


@dataclass
class AgentRuntime:
    ps: PlatformSettings
    agent: Settings
    gateway_factory: Callable[..., object]            # (on_event=None) -> gateway
    llm: LlmPort | None = None
    engine: object = None
    sm: object = None
    saver: object = None                              # sync PostgresSaver (çekirdek analiz grafiği, thread'de)
    asaver: object = None                             # AsyncPostgresSaver (sohbet grafiği)
    store: object = None                              # AsyncPostgresStore (uzun vadeli kapsamlı notlar)
    memory: MemoryService | None = None
    mem_events: object = None                         # memory.events.MemoryEventPublisher
    cw_writer: object = None                          # context_window.writer.ContextWindowWriter
    sem: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    callbacks: list = field(default_factory=list)      # LangSmith tracer (redakte eder); tracing kapalıysa boş
    settings_started: dict | None = None              # süreç açılışındaki etkili ayarlar ("restart gerekli" tespiti)
    _closers: list = field(default_factory=list)

    @property
    def llm_enabled(self) -> bool:
        return self.llm is not None

    async def close(self) -> None:
        for c in reversed(self._closers):
            try:
                r = c()
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # noqa: BLE001
                log.exception("kapatma hatası")
        self._closers.clear()


def default_gateway_factory(agent: Settings) -> Callable[..., object]:
    from airlab_agent.mcp_gateway import McpGateway
    b = agent.cfg["budgets"]

    def make(on_event=None):
        return McpGateway(agent.mcp_url, agent.dt_token, timeout_s=b["query_timeout_seconds"], retries=b["query_retries"],
                          max_queries=b["max_queries_per_run"], on_event=on_event)
    make._settings_default = True          # koşu başında Settings'teki analiz connector'ı ile değiştirilir
    return make


def default_llm(agent: Settings) -> LlmPort | None:
    if not agent.llm_enabled:
        return None
    try:
        from airlab_agent.llm import Llm
        from airlab_agent.tracing import setup_tracing
        return ClaudePort(Llm(agent, setup_tracing(agent)))
    except Exception as e:  # noqa: BLE001
        log.warning("Claude istemcisi kurulamadı, deterministik mod: %s", type(e).__name__)
        return None


async def create_runtime(ps: PlatformSettings, *, agent: Settings | None = None, gateway_factory=None,
                         llm: LlmPort | None | bool = True, application_name: str = "sre-backend") -> AgentRuntime:
    """`llm=True` → ortamdan Claude; `None/False` → LLM yok (deterministik); nesne → verilen bağlantı (test)."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool, ConnectionPool

    agent = agent or load_settings()
    register_secrets(agent.secrets() + ps.secrets())
    rt = AgentRuntime(ps=ps, agent=agent, gateway_factory=gateway_factory or default_gateway_factory(agent),
                      llm=default_llm(agent) if llm is True else (llm or None),
                      sem=asyncio.Semaphore(ps.max_concurrent_investigations))
    core = getattr(rt.llm, "llm", None)
    rt.callbacks = list(getattr(core, "callbacks", None) or [])
    if not rt.callbacks and agent.langsmith_tracing:
        from airlab_agent.tracing import setup_tracing
        rt.callbacks = setup_tracing(agent)             # LLM yokken de deterministik analizler izlenir
    rt.engine = make_engine(ps, application_name)
    rt.sm = make_sessionmaker(rt.engine)
    rt._closers.append(rt.engine.dispose)

    conninfo = langgraph_conninfo(ps.libpq_url)
    kw = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
          "application_name": f"{application_name}-lg"}
    pool = ConnectionPool(conninfo, min_size=1, max_size=4, kwargs=kw, open=True, name="lg-sync")
    rt._closers.append(pool.close)
    # Agent Memory gözlemi: checkpointer ve MemoryService alt sınıfları (davranış aynı; olaylar + retrieval kaydı)
    from ..memory import events as mem_events
    from ..memory.observed import ObservedMemoryService, observed_savers
    saver_cls, asaver_cls = observed_savers()
    rt.saver = saver_cls(pool)
    apool = AsyncConnectionPool(conninfo, min_size=1, max_size=4, kwargs=kw, open=False, name="lg-async")
    await apool.open()
    rt._closers.append(apool.close)
    rt.asaver = asaver_cls(apool)
    rt.store = AsyncPostgresStore(apool)
    rt.memory = ObservedMemoryService(rt.sm, rt.store)
    rt.mem_events = mem_events.MemoryEventPublisher(rt)
    await rt.mem_events.start()
    mem_events.install(rt.mem_events)

    async def _stop_events():
        await rt.mem_events.stop()
        if mem_events.publisher() is rt.mem_events:
            mem_events.install(None)
    rt._closers.append(_stop_events)
    # Context Windows: LLM çağrısı başına kayıt (yazma hatası agent çalışmasını kesmez)
    from ..context_window import writer as cw_writer
    rt.cw_writer = await cw_writer.install(rt.sm, core)

    async def _stop_cw():
        await cw_writer.uninstall(rt.cw_writer)
    rt._closers.append(_stop_cw)
    return rt


def partial_gateway(factory, on_event):
    return partial(factory, on_event=on_event)
