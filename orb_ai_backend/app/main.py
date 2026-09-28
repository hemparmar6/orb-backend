"""FastAPI application factory / entrypoint.

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.v1 import api_router as v1_router
from app.api.v1.ws import ws_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, get_redis
from app.engine.strategy.manager import manager as strategy_manager
from app.engine.market_data.instrument_runtime import (
    start_dhan_instrument_runtime,
    stop_dhan_instrument_runtime,
)
from app.middleware.metrics import MetricsMiddleware
from app.middleware.path_normalize import PathNormalizationMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.ws import order_broadcaster, quote_broadcaster

configure_logging()
logger = get_logger(__name__)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _maybe_run_admin_seeder() -> None:
    """Idempotently create the default admin user in production.

    Enabled by setting ``ADMIN_SEED_ON_STARTUP=true``. The seeder itself
    (``scripts/seed_admin.py``) is a no-op if the account already exists.
    """
    if not _truthy(os.environ.get("ADMIN_SEED_ON_STARTUP")):
        return
    try:
        import importlib.util

        seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed_admin.py"
        if not seed_path.exists():
            logger.warning("admin_seeder_missing", extra={"path": str(seed_path)})
            return
        spec = importlib.util.spec_from_file_location("orb_seed_admin", str(seed_path))
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        await mod.main()
    except Exception:
        logger.exception("admin_seeder_failed")
        # Fail fast in production: a misconfigured/insecure admin seed must
        # not be silently ignored (Task 5 — require explicit secure config).
        if settings.is_production:
            raise


async def _seed_subscription_plans() -> None:
    """Idempotently seed default subscription plans and strategy catalog."""
    try:
        from app.db.session import async_session_factory
        from app.services.subscriptions import (
            seed_default_plans,
            seed_default_strategies,
        )
        async with async_session_factory() as session:
            await seed_default_plans(session)
            await seed_default_strategies(session)
            await session.commit()
    except Exception:  # pragma: no cover
        logger.exception("subscription_plans_seed_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "startup",
        extra={"app": settings.APP_NAME, "version": settings.APP_VERSION, "env": settings.APP_ENV},
    )
    # Warm the optional Redis client (non-blocking on failure).
    await get_redis()
    # Load one cached, refreshable Dhan instrument master when Dhan is active.
    # Failures leave the runtime without a master so market-data endpoints
    # fall back honestly instead of fabricating broker identifiers.
    await start_dhan_instrument_runtime()
    # Optionally seed the admin account (production only).
    await _maybe_run_admin_seeder()
    # Seed default subscription plans (idempotent).
    await _seed_subscription_plans()
    # Bind DB session factory into BrokerHealthTracker so it can persist
    # circuit-breaker events for disconnect / reconnect.
    try:
        from app.db.session import async_session_factory
        from app.services.broker_health import tracker as broker_health_tracker
        broker_health_tracker.bind_session_factory(async_session_factory)
    except Exception:  # pragma: no cover
        logger.exception("broker_health_bind_failed")
    # Task 3: recover LIVE strategy sessions after a backend/Railway restart.
    # Reconnects to each RUNNING live session's broker, reconciles broker vs
    # ORB state, and ONLY resumes when safely consistent (fail-closed on any
    # mismatch). Never places or duplicates a broker order. Never raises.
    try:
        from app.services.live_recovery_service import LiveRecoveryService

        await LiveRecoveryService().recover_all()
    except Exception:  # pragma: no cover
        logger.exception("live_recovery_failed")
    # Start background scheduler (opt-in via SCHEDULER_ENABLED)
    from app.services.scheduler import start_scheduler, stop_scheduler
    await start_scheduler()
    # Start Module 10 backup scheduler (opt-in via BACKUP_ENABLED + scheduler)
    from app.services.backup_scheduler import (
        start_backup_scheduler,
        stop_backup_scheduler,
    )
    await start_backup_scheduler()
    yield
    # Graceful shutdown: stop every in-process engine session.
    await stop_dhan_instrument_runtime()
    await strategy_manager.shutdown()
    await quote_broadcaster.stop()
    await order_broadcaster.stop()
    await stop_scheduler()
    await stop_backup_scheduler()
    await close_redis()
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "ORB AI — Backend API (Module 1).\n\n"
            "Auth, user, trade, strategy, and settings foundation for the "
            "ORB AI algorithmic trading platform. Trading logic and broker "
            "integrations are intentionally out of scope for this module."
        ),
        # Built-in Swagger UI is disabled here and re-served from a custom
        # route below using locally-hosted assets, so /docs works under the
        # strict same-origin CSP without any external CDN (jsDelivr).
        docs_url=None,
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        debug=settings.DEBUG,
    )

    # Disable FastAPI's built-in 307 slash redirect. Trailing-slash mismatches
    # are normalized in-process by PathNormalizationMiddleware below so
    # clients never lose their Authorization header on redirect.
    app.router.redirect_slashes = False

    # ---- Middleware ----
    # Order matters (Starlette runs them in *reverse* registration order for
    # request handling): SecurityHeaders (outermost) -> CORS -> RateLimit ->
    # Metrics -> RequestID -> PathNormalization (innermost, right before routing).
    app.add_middleware(PathNormalizationMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # ---- Exception handlers ----
    register_exception_handlers(app)

    # ---- Self-hosted Swagger UI (/docs) --------------------------------
    # Serve Swagger UI's JS/CSS from the same origin instead of the external
    # jsDelivr CDN. The strict CSP (`script-src 'self' 'unsafe-inline'`,
    # `style-src 'self' 'unsafe-inline'`) then permits every asset, so /docs
    # renders with no "SwaggerUIBundle is not defined" / CSP errors and the
    # backend stays fully self-contained (works offline / air-gapped).
    _swagger_static_dir = Path(__file__).resolve().parent / "static" / "swagger"
    if _swagger_static_dir.is_dir():
        app.mount(
            "/docs-static",
            StaticFiles(directory=str(_swagger_static_dir)),
            name="swagger-static",
        )

        @app.get("/docs", include_in_schema=False)
        async def custom_swagger_ui_html() -> HTMLResponse:  # noqa: D401
            return get_swagger_ui_html(
                openapi_url=app.openapi_url or "/openapi.json",
                title=f"{settings.APP_NAME} — Swagger UI",
                swagger_js_url="/docs-static/swagger-ui-bundle.js",
                swagger_css_url="/docs-static/swagger-ui.css",
                swagger_favicon_url="/docs-static/favicon-32x32.png",
            )

    # ---- Routes ----
    @app.get("/health", tags=["health"], summary="Liveness probe")
    async def liveness() -> dict:  # noqa: D401
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "api": settings.API_V1_PREFIX,
        }

    app.include_router(v1_router, prefix=settings.API_V1_PREFIX)
    # WebSocket routes — served under the same /api/v1 prefix but not part of OpenAPI schema
    # (WebSocket endpoints don't render meaningfully in Swagger).
    app.include_router(ws_router, prefix=f"{settings.API_V1_PREFIX}/ws")

    # ---- Admin dashboard static mount (production) ----
    # Env-gated so tests / preview stay unaffected. The Emergent-preview
    # shim at /app/backend/server.py mounts the same routes for dev; in a
    # self-hosted Docker/VPS deployment set ADMIN_UI_DIST=/app/admin_web_dist
    # (baked into the image) to serve the SPA from the API container.
    admin_ui_dist = os.environ.get("ADMIN_UI_DIST", "").strip()
    if admin_ui_dist and Path(admin_ui_dist).is_dir():
        dist_path = Path(admin_ui_dist)

        app.mount(
            "/api/admin-ui/assets",
            StaticFiles(directory=str(dist_path / "assets")),
            name="admin-ui-assets",
        )

        @app.get("/api/admin-ui", include_in_schema=False)
        @app.get("/api/admin-ui/", include_in_schema=False)
        async def _admin_ui_index() -> FileResponse:
            return FileResponse(str(dist_path / "index.html"))

        @app.get("/api/admin-ui/{full_path:path}", include_in_schema=False)
        async def _admin_ui_spa(full_path: str):
            candidate = dist_path / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(dist_path / "index.html"))

        logger.info("admin_ui_mounted", extra={"path": str(dist_path)})

    return app


app = create_app()
