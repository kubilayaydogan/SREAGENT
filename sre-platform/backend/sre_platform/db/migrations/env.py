"""Alembic ortamı: yalnız `sre` şemasını yönetir (LangGraph tabloları `langgraph` şemasında, kütüphane kurar)."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from sre_platform.db.migrate import compare_type
from sre_platform.db.models import SCHEMA, Base

config = context.config


def _url() -> str:
    url = config.attributes.get("url") or config.get_main_option("sqlalchemy.url")
    if not url:
        from sre_platform.core.config import load_platform_settings  # noqa: PLC0415
        url = load_platform_settings().database_url
    return url


def include_object(obj, name, type_, reflected, compare_to):
    schema = getattr(obj, "schema", None) or (getattr(obj, "table", None) is not None and obj.table.schema)
    if type_ == "table":
        return schema == SCHEMA
    return True


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool,
                           connect_args={"application_name": "sre-migrate", "connect_timeout": 10,
                                         "options": "-c search_path=public"})
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.commit()
        context.configure(connection=conn, target_metadata=Base.metadata, include_schemas=True,
                          include_object=include_object, version_table_schema=SCHEMA,
                          compare_type=compare_type, compare_server_default=False)
        with context.begin_transaction():
            # aynı anda iki migrate süreci çalışmasın
            conn.execute(text("SELECT pg_advisory_xact_lock(724501)"))
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline mod desteklenmiyor; canlı DB'ye karşı çalıştırın")
run_migrations_online()
