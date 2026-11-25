"""FastAPI uygulaması: yaşam döngüsü, correlation ID, yapılandırılmış erişim logu, güvenli CORS, hata eşlemesi."""
from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from ..core.config import PlatformSettings, get_settings
from ..core.logging import correlation_id, new_correlation_id, setup_logging
from ..repositories.base import Conflict, InvalidTransition, NotFound
from ..services.runs import RunRegistry
from ..services.telemetry import TelemetryService

log = logging.getLogger("sre.api")
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def _err(status: int, code: str, msg: str, details=None) -> JSONResponse:
    return JSONResponse({"error": code, "message": msg, "correlation_id": correlation_id.get(), "details": details},
                        status_code=status, headers={"X-Correlation-ID": correlation_id.get()})


async def bootstrap(rt) -> None:
    """Açılışta: şema sürümü doğrula, uygulama kataloğunu ve ilk admini hazırla (idempotent)."""
    import asyncio

    from ..db.migrate import check_ready
    from ..db.session import transaction
    from ..repositories import applications, users
    ok, detail = await asyncio.to_thread(check_ready, rt.ps.database_url)
    if not ok:
        raise RuntimeError(f"veritabanı şeması hazır değil ({detail}); önce 'sre-platform migrate' çalıştırın")
    async with transaction(rt.sm) as s:
        await applications.sync_from_config(s, rt.agent.cfg)
        if await users.ensure_admin(s, rt.ps.admin_username, rt.ps.admin_password):
            log.info("ilk admin kullanıcı oluşturuldu", extra={"username": rt.ps.admin_username})
    from ..settings.runtime import apply_startup
    await apply_startup(rt)                     # connector/LLM profili tohumlama + 'restart' ayarlarının uygulanması


def create_app(settings: PlatformSettings | None = None, *, runtime_factory=None) -> FastAPI:
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ps = settings or get_settings()
        from ..agent.runtime import create_runtime
        rt = await (runtime_factory(ps) if runtime_factory else create_runtime(ps))
        await bootstrap(rt)
        app.state.rt = rt
        app.state.runs = RunRegistry()
        app.state.telemetry = TelemetryService(rt)
        log.info("sre-backend hazır", extra={"llm": rt.llm_enabled, "mcp": bool(rt.agent.mcp_url)})
        try:
            yield
        finally:
            await app.state.runs.shutdown()
            await rt.close()

    ps0 = settings
    try:
        ps_cfg = ps0 or get_settings()
    except RuntimeError:
        ps_cfg = None
    docs = bool(ps_cfg and ps_cfg.api_docs)       # varsayılan kapalı: kimliksiz API şeması/keşif yüzeyi yok
    app = FastAPI(title="airlab SRE Operations Platform", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs" if docs else None, openapi_url="/api/openapi.json" if docs else None,
                  redoc_url=None)
    from ..security import context as sec_ctx
    trusted = sec_ctx.parse_networks(ps_cfg.trusted_proxies if ps_cfg else ())
    origins = list((ps0.cors_origins if ps0 else None) or [])
    if not origins:
        try:
            origins = list(get_settings().cors_origins)
        except RuntimeError:
            origins = []
    if origins:                                   # aynı origin (nginx proxy) için CORS gerekmez; yalnız beyaz liste
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type",
                                                                                  "Last-Event-ID", "X-Request-ID"],
                           expose_headers=["X-Correlation-ID"])

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        cid = request.headers.get("X-Request-ID", "")
        cid = cid if CID_RE.match(cid) else new_correlation_id()
        token = correlation_id.set(cid)
        sec_ctx.client_ip.set(sec_ctx.resolve_client_ip(request.client.host if request.client else None,
                                                        request.headers.get("x-forwarded-for"), trusted))
        sec_ctx.session_id.set(None)
        sec_ctx.api_key.set(None)
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            log.exception("işlenmemiş hata", extra={"path": request.url.path})
            response = _err(500, "internal_error", "beklenmeyen bir hata oluştu")
        response.headers["X-Correlation-ID"] = cid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path not in ("/api/health",):
            log.info("http", extra={"method": request.method, "path": request.url.path, "status": response.status_code,
                                    "ms": int((time.monotonic() - t0) * 1000),
                                    "user": getattr(request.state, "user_id", None)})
        correlation_id.reset(token)
        return response

    @app.exception_handler(NotFound)
    async def _nf(_, e):
        return _err(404, "not_found", str(e))

    @app.exception_handler(Conflict)
    async def _cf(_, e):
        return _err(409, "conflict", str(e))

    @app.exception_handler(InvalidTransition)
    async def _it(_, e):
        return _err(409, "invalid_transition", str(e))

    @app.exception_handler(ValueError)
    async def _ve(request: Request, e):
        # P1.9: yalnız UserError mesajı istemciye gider; diğer ValueError'lar iç ayrıntı (yol, SQL, kütüphane
        # mesajı) taşıyabilir → genel mesaj, ayrıntı redakte edilerek log'a.
        from airlab_agent.errors import UserError
        if isinstance(e, UserError):
            return _err(422, "invalid_value", str(e))
        log.warning("beklenmeyen ValueError", extra={"path": request.url.path, "err_type": type(e).__name__,
                                                       "err": str(e)[:500]})
        return _err(422, "invalid_value", "istek değeri işlenemedi")

    from pydantic import ValidationError

    from ..settings.runtime import RunNotAllowed
    from ..settings.service import SettingsConflict, SettingsInvalid

    @app.exception_handler(SettingsConflict)
    async def _sc(_, e: SettingsConflict):
        return _err(409, "revision_conflict", str(e), {"current_revision": e.current})

    @app.exception_handler(SettingsInvalid)
    async def _si(_, e: SettingsInvalid):
        return _err(422, "invalid_settings", str(e), e.details or None)

    @app.exception_handler(RunNotAllowed)
    async def _rn(request: Request, e):
        from ..security import audit
        await audit.record_separate(request.app.state.rt.sm, actor=_actor(request), action="run.denied", section="agent",
                                    target=f"{request.method} {request.url.path}"[:128], result="denied",
                                    reason_code=e.reason_code, detail=str(e),
                                    after={"policy_version": getattr(e.decision, "policy_version", None)})
        return _err(409, "agent_disabled", str(e))

    def _actor(request: Request) -> str:
        return getattr(request.state, "username", None) or getattr(request.state, "user_id", None) or "anonymous"

    @app.exception_handler(ValidationError)
    async def _pv(_, e: ValidationError):
        details = [{"loc": list(x.get("loc", [])), "msg": x.get("msg"), "type": x.get("type")} for x in e.errors()]
        return _err(422, "validation_error", "ayar doğrulanamadı", details)

    @app.exception_handler(IntegrityError)
    async def _ie(_, e):
        # P1.9: DB mesajı (constraint / kolon / değer) istemciye gitmez; yalnız log'a
        log.warning("integrity error", extra={"err": str(getattr(e, "orig", e)).split("\n")[0][:300]})
        return _err(409, "integrity_error", "veri bütünlüğü kuralı ihlal edildi")

    @app.exception_handler(PoolTimeout)
    async def _pt(_, e):
        return _err(503, "db_busy", "veritabanı Connection Pool dolu; kısa süre sonra tekrar deneyin")

    @app.exception_handler(OperationalError)
    async def _oe(_, e):
        log.error("veritabanı hatası", extra={"err": str(getattr(e, 'orig', e))[:300]})
        return _err(503, "db_unavailable", "veritabanına erişilemiyor")

    @app.exception_handler(HTTPException)
    async def _he(_, e: HTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
                429: "rate_limited"}.get(e.status_code, "http_error")
        r = _err(e.status_code, code, str(e.detail))
        for k, v in (e.headers or {}).items():
            r.headers[k] = v
        return r

    @app.exception_handler(RequestValidationError)
    async def _rv(_, e: RequestValidationError):
        details = [{"loc": list(x.get("loc", [])), "msg": x.get("msg"), "type": x.get("type")} for x in e.errors()]
        return _err(422, "validation_error", "istek doğrulanamadı", details)

    from .routes import agent, auth, catalog, mcp_calls, records, settings as settings_routes
    for r in (auth.router, catalog.router, catalog.system, agent.router, records.router, mcp_calls.router,
              settings_routes.router):
        app.include_router(r, prefix="/api")
    return app
