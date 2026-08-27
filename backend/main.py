"""WealthSail FastAPI application entrypoint.

Mounts every domain router under /api/v1, wires the unified error envelope,
the request-id middleware and the asyncpg pool lifecycle.

Run:  uvicorn backend.main:app --reload
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.common.database import Pool, create_pool, verify_runtime_login_boundary
from backend.config import Settings, get_settings, validate_runtime_settings
from backend.errors import MoneyJSONResponse, register_exception_handlers, request_id_of
from backend.chat.api import router as chat_router
from backend.identity.api import router as identity_router
from backend.integrations.wecom import close_http_client, router as wecom_router
from backend.market.api import router as market_router
from backend.operations.api import router as operations_router
from backend.portfolio.api import router as portfolio_router
from backend.reports.api import router as reports_router
from backend.research.api import router as research_router
from backend.risk.api import router as risk_router

logger = logging.getLogger("wealthsail.main")
http_logger = logging.getLogger("wealthsail.http")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    await verify_runtime_login_boundary(
        pool, process="api", skip=not settings.is_production
    )
    app.state.db_pool = pool
    logger.info("database pool ready (env=%s)", settings.app_env)
    try:
        yield
    finally:
        await close_http_client()
        await pool.close()
        logger.info("database pool closed")


def create_app() -> FastAPI:
    settings = validate_runtime_settings(get_settings(), process="api")
    app = FastAPI(
        title="WealthSail API",
        version=__version__,
        description="投研平台模块化单体后端（资产看板 / 研报 / 风控 / 身份 / 运维）",
        lifespan=lifespan,
        default_response_class=MoneyJSONResponse,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request_id_of(request)
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Request-Time-Ms"] = str(duration_ms)

        # Keep diagnostics at the HTTP boundary so proxy rewrites can be
        # distinguished from application validation failures. Never log the
        # query string, request body, cookies or authorization headers: invite
        # tokens and passwords cross this boundary during registration.
        def safe_header(name: str) -> str:
            value = request.headers.get(name, "-")
            return value.replace("\r", " ").replace("\n", " ")[:160] or "-"

        allow = response.headers.get("Allow", "-")[:80] or "-"
        location = response.headers.get("Location", "-").split("?", 1)[0][:240] or "-"
        http_logger.log(
            logging.WARNING if response.status_code >= 400 else logging.INFO,
            (
                "http_request method=%s path=%s status=%s request_id=%s "
                "duration_ms=%s allow=%s host=%s forwarded_proto=%s "
                "forwarded_host=%s location=%s"
            ),
            request.method,
            request.url.path,
            response.status_code,
            request_id,
            duration_ms,
            allow,
            safe_header("Host"),
            safe_header("X-Forwarded-Proto"),
            safe_header("X-Forwarded-Host"),
            location,
        )
        return response

    register_exception_handlers(app)

    # 静态资源（头像上传产物）: /static/avatars/...
    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    prefix = settings.api_prefix
    app.include_router(portfolio_router, prefix=prefix)
    app.include_router(reports_router, prefix=prefix)
    app.include_router(research_router, prefix=prefix)
    app.include_router(risk_router, prefix=prefix)
    app.include_router(identity_router, prefix=prefix)
    app.include_router(operations_router, prefix=prefix)
    app.include_router(wecom_router, prefix=prefix)
    app.include_router(chat_router, prefix=prefix)
    app.include_router(market_router, prefix=prefix)

    @app.get("/healthz", tags=["system"])
    async def healthz(request: Request) -> JSONResponse:
        pool: Pool = request.app.state.db_pool
        db_ok = False
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:  # noqa: BLE001
            logger.exception("healthz db probe failed")
        return JSONResponse(
            {
                "status": "ok" if db_ok else "degraded",
                "db": "up" if db_ok else "down",
                "version": __version__,
                "requestId": request.state.request_id,
            },
            status_code=200 if db_ok else 503,
        )

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {
            "service": "wealthsail-api",
            "version": __version__,
            "docs": "/docs",
            "health": "/healthz",
        }

    return app


app = create_app()
