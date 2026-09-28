"""Pytest configuration.

Uses an in-memory SQLite database so tests run fast without Postgres.
For Postgres parity in CI, set DATABASE_URL and TEST_DATABASE_URL and this
config will pick it up.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Configure env BEFORE importing the app so Settings picks these up.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-pytest-only-do-not-use-in-prod")
# Deterministic Fernet key for the test suite. Do NOT reuse in prod.
os.environ.setdefault("ENCRYPTION_KEY", "zmWkPq3xR6-7NtV8lJdA0GYHiCEuBoXKvsMFDIhL4Rk=")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_ENABLED", "false")
# The LIVE post-arm cooldown is a production safety.  Tests set it to 0
# by default so existing suites (which arm LIVE and immediately execute)
# stay green; the dedicated cooldown tests opt in explicitly.
os.environ.setdefault("TRADING_MODE_LIVE_COOLDOWN_SECONDS", "0")

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
import app.models  # noqa: E402,F401  register models


# NOTE: A custom session-scoped ``event_loop`` fixture was removed here. In
# pytest-asyncio >= 0.21 that fixture is deprecated, and a *session*-scoped loop
# gets closed mid-run, which makes later async modules fail with
# "RuntimeError: There is no current event loop" when the whole suite runs
# together (each module still passes in isolation). We now let pytest-asyncio
# manage a fresh function-scoped loop per test (see
# ``asyncio_default_fixture_loop_scope = function`` in pytest.ini). No test
# logic is changed by this — only the loop lifecycle.


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(
        os.environ["DATABASE_URL"], echo=False, future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed default subscription plans + strategy catalog so tests mirror
    # production startup (see app.main._seed_subscription_plans).
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        from app.services.subscriptions import (
            seed_default_plans,
            seed_default_strategies,
        )
        await seed_default_plans(session)
        await seed_default_strategies(session)
        await session.commit()
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db

    # Point the process-scoped StrategyManager at the test DB so /trading/start
    # can spin up runners against sqlite+aiosqlite.
    from app.engine.strategy.manager import manager as strategy_manager
    strategy_manager._session_factory = factory

    # Point the failed-login + rate-limit-event persistence at the same
    # test DB. Save & restore the original so tests that don't opt into
    # Module 10 fixtures aren't affected.
    import app.db.session as _db_sess
    _orig_factory = getattr(_db_sess, "async_session_factory", None)
    _db_sess.async_session_factory = factory  # type: ignore[assignment]

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # Cleanup: stop any leftover runners so their asyncio tasks don't leak.
    await strategy_manager.shutdown()
    fastapi_app.dependency_overrides.clear()
    if _orig_factory is not None:
        _db_sess.async_session_factory = _orig_factory  # type: ignore[assignment]


# =====================================================================
# Module 10 shared fixtures
# =====================================================================

_ADMIN_EMAIL = "admin.module10@example.com"
_ADMIN_PASSWORD = "AdminPass123!"
_USER_EMAIL = "user.module10@example.com"
_USER_PASSWORD = "UserPass123!"


@pytest.fixture
def admin_email() -> str:
    return _ADMIN_EMAIL


@pytest_asyncio.fixture(scope="function")
async def admin_headers(client) -> dict[str, str]:
    """Register a user, promote to admin, return an Authorization header."""
    from app.models.user import User, UserRole
    from app.db.session import async_session_factory

    await client.post(
        "/api/v1/auth/register",
        json={
            "email": _ADMIN_EMAIL,
            "password": _ADMIN_PASSWORD,
            "full_name": "Admin Tester",
        },
    )

    async with async_session_factory() as db:
        from sqlalchemy import select
        u = (await db.execute(select(User).where(User.email == _ADMIN_EMAIL))).scalar_one()
        u.role = UserRole.ADMIN
        u.is_active = True
        u.is_verified = True
        await db.commit()

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture(scope="function")
async def user_headers(client) -> dict[str, str]:
    """Register a regular user and return an Authorization header."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": _USER_EMAIL,
            "password": _USER_PASSWORD,
            "full_name": "Regular Tester",
        },
    )
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": _USER_EMAIL, "password": _USER_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
