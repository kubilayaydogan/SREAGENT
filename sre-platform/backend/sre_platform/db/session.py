"""Async SQLAlchemy motoru (psycopg 3) ve oturum fabrikası. Bağlantı havuzu sınırlı; her bağlantıda
statement_timeout ve application_name ayarlanır (DB tarafında izlenebilirlik)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import PlatformSettings


def make_engine(s: PlatformSettings, application_name: str = "sre-backend") -> AsyncEngine:
    return create_async_engine(
        s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_timeout=s.db_pool_timeout_s,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"application_name": application_name,
                      "options": f"-c statement_timeout={s.db_statement_timeout_ms} -c TimeZone=UTC",
                      "connect_timeout": 10},
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sm() as s:
        async with s.begin():
            yield s
