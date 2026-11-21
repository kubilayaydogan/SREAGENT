"""Kontrollü migration: Alembic (sre şeması) + LangGraph checkpoint/store kurulumu (langgraph şeması).

`sre-platform migrate` tek seferlik `sre-migrate` container'ında çalışır; backend/worker yalnız şema sürümünü
DOĞRULAR, kendisi migrate etmez (birden çok replika aynı anda şema değiştirmesin).
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool

LANGGRAPH_SCHEMA = "langgraph"


def compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """Varsayılan olmayan şemadaki PostgreSQL enum'ları yansıtmada VARCHAR görünür → yanlış 'tip değişti'.
    Enum sütunları için karşılaştırmayı atla (enum değer değişiklikleri elle migration ile yapılır)."""
    import sqlalchemy as sa
    if isinstance(metadata_type, sa.Enum):
        return False
    return None


def alembic_config(url: str) -> Config:
    ini = Path(str(resources.files("sre_platform.db").joinpath("alembic.ini")))
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    cfg.attributes["url"] = url
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)


def head_revision(url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def current_revision(url: str) -> str | None:
    eng = create_engine(url, poolclass=pool.NullPool)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c, opts={"version_table_schema": "sre"}).get_current_revision()
    finally:
        eng.dispose()


def langgraph_conninfo(libpq_url: str) -> str:
    sep = "&" if "?" in libpq_url else "?"
    return f"{libpq_url}{sep}options=-csearch_path%3D{LANGGRAPH_SCHEMA}"


def setup_langgraph(libpq_url: str) -> None:
    """Checkpointer ve Store tablolarını kurar/günceller (idempotent; kütüphanenin kendi migration'ları)."""
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.store.postgres import PostgresStore

    with psycopg.connect(libpq_url, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {LANGGRAPH_SCHEMA}")
    with PostgresSaver.from_conn_string(langgraph_conninfo(libpq_url)) as saver:
        saver.setup()
    with PostgresStore.from_conn_string(langgraph_conninfo(libpq_url)) as store:
        store.setup()


def check_ready(url: str) -> tuple[bool, str]:
    cur, head = current_revision(url), head_revision(url)
    return (cur == head, f"şema {cur or 'yok'} / beklenen {head}")
