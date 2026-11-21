"""Testler GERÇEK PostgreSQL'e karşı çalışır (SQLite taklidi yok: enum, tsvector, SKIP LOCKED, kısmi indeks
davranışı PostgreSQL'e özgü). SRE_TEST_DATABASE_URL verilmezse DB testleri atlanır.

Oturum başında geçici bir veritabanı (sre_test_<rastgele>) açılır, Alembic ile `head`e taşınır; her testten sonra
tablolar boşaltılır; oturum sonunda veritabanı silinir.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
import sqlalchemy as sa

ADMIN_URL = os.getenv("SRE_TEST_DATABASE_URL", "")
os.environ.setdefault("SRE_JWT_SECRET", "test-" + "x" * 40)


def run(coro_fn: Callable[..., Coroutine], *a, **kw) -> Any:
    """psycopg async Windows'ta ProactorEventLoop ile çalışmaz → SelectorEventLoop."""
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro_fn(*a, **kw), loop_factory=factory)


@pytest.fixture(scope="session")
def db_url():
    if not ADMIN_URL:
        pytest.skip("SRE_TEST_DATABASE_URL tanımlı değil (PostgreSQL gerektiren testler atlandı)")
    name = "sre_test_" + secrets.token_hex(4)
    admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    from sre_platform.db import migrate
    migrate.upgrade(url)
    yield url
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def settings(db_url, monkeypatch):
    monkeypatch.setenv("SRE_DATABASE_URL", db_url)
    from sre_platform.core.config import load_platform_settings
    return load_platform_settings()


@pytest.fixture(autouse=True)
def _clean(request):
    yield
    if "db_url" not in request.fixturenames:
        return
    url = request.getfixturevalue("db_url")
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with eng.begin() as c:
        tables = [r[0] for r in c.execute(sa.text(
            "select tablename from pg_tables where schemaname='sre' and tablename <> 'alembic_version'"))]
        if tables:
            c.execute(sa.text("TRUNCATE " + ", ".join(f"sre.{t}" for t in tables) + " RESTART IDENTITY CASCADE"))
    eng.dispose()


@pytest.fixture
def adb(settings):
    """Async oturum fabrikası döndüren yardımcı: `run(adb(fn))` → fn(session) bir transaction içinde."""
    from sre_platform.db.session import make_engine, make_sessionmaker

    def wrap(fn):
        async def go():
            eng = make_engine(settings, "pytest")
            try:
                sm = make_sessionmaker(eng)
                async with sm() as s:
                    async with s.begin():
                        return await fn(s)
            finally:
                await eng.dispose()
        return go
    return wrap
