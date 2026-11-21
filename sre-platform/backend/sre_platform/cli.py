"""sre-platform komut satırı.

  sre-platform migrate [--seed]        Alembic upgrade head + LangGraph checkpoint/store kurulumu (+ bilgi bankası tohumu)
  sre-platform serve [--port 8000]     FastAPI (tek worker; SSE çalıştırma kaydı süreç içidir)
  sre-platform worker                  zamanlanmış işler (watch / sağlık kontrolü / saklama)
  sre-platform create-user NAME ROLE   parola ortamdan: SRE_NEW_USER_PASSWORD
  sre-platform import-reports DIR      eski airlab-agent JSON raporlarını araştırma kaydı olarak içe aktarır
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .core.config import load_platform_settings
from .core.logging import setup_logging

log = logging.getLogger("sre.cli")


def _loop_factory():
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None


def cmd_migrate(a) -> int:
    from .db import migrate
    ps = load_platform_settings()
    migrate.upgrade(ps.database_url)
    migrate.setup_langgraph(ps.libpq_url)
    log.info("migration tamam", extra={"revision": migrate.current_revision(ps.database_url)})
    if a.seed:
        from .seed import run_seed
        n = asyncio.run(run_seed(ps), loop_factory=_loop_factory())
        log.info("tohum kayıtlar", extra=n)
    return 0


def cmd_serve(a) -> int:
    import uvicorn

    from .api.app import create_app
    uvicorn.run(create_app(), host=a.host, port=a.port, workers=1, proxy_headers=True, forwarded_allow_ips="*",
                log_config=None, access_log=False, timeout_graceful_shutdown=25)
    return 0


def cmd_worker(a) -> int:
    from .worker import main as worker_main
    return asyncio.run(worker_main(load_platform_settings(), once=a.once), loop_factory=_loop_factory())


def cmd_create_user(a) -> int:
    from .db.models import Role
    from .db.session import make_engine, make_sessionmaker, transaction
    from .repositories import users
    pw = os.getenv("SRE_NEW_USER_PASSWORD", "")
    if len(pw) < 10:
        print("SRE_NEW_USER_PASSWORD ortam değişkeni (en az 10 karakter) gerekli", file=sys.stderr)
        return 2
    ps = load_platform_settings()

    async def go():
        eng = make_engine(ps, "sre-cli")
        try:
            async with transaction(make_sessionmaker(eng)) as s:
                u = await users.create(s, a.username, a.display_name or a.username, Role(a.role), pw)
                return str(u.id)
        finally:
            await eng.dispose()
    print(asyncio.run(go(), loop_factory=_loop_factory()))
    return 0


def cmd_import(a) -> int:
    from .seed import import_reports
    n = asyncio.run(import_reports(load_platform_settings(), a.dir), loop_factory=_loop_factory())
    print(f"içe aktarılan rapor: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.getenv("SRE_LOG_LEVEL", "INFO"))
    p = argparse.ArgumentParser(prog="sre-platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate")
    m.add_argument("--seed", action="store_true")
    m.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    w = sub.add_parser("worker")
    w.add_argument("--once", action="store_true", help="vadesi gelen işleri bir kez çalıştır ve çık")
    w.set_defaults(fn=cmd_worker)
    u = sub.add_parser("create-user")
    u.add_argument("username")
    u.add_argument("role", choices=["admin", "sre", "viewer"])
    u.add_argument("--display-name")
    u.set_defaults(fn=cmd_create_user)
    i = sub.add_parser("import-reports")
    i.add_argument("dir")
    i.set_defaults(fn=cmd_import)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
