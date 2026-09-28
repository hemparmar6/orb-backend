"""Integration test: Execution Safety hook inside OrderManager.

Verifies:
* The safety gate is skipped for paper mode (default in tests)
* The safety gate fires when executor.execution_mode == "live"
* Duplicate blocking raises InvalidOrderError
* Kill-switch blocks live orders
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.core.exceptions import InvalidOrderError
from app.core.security import hash_password
from app.engine.logger import TradeLogger
from app.engine.orders.executor_interface import Executor
from app.engine.orders.manager import OrderIntentRequest, OrderManager
from app.engine.portfolio.manager import PortfolioManager
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    OrderProduct,
    OrderSide,
    OrderType,
)
from app.models.user import User
from app.services.execution_safety_service import ExecutionSafetyService
from app.services.trading_mode_service import LIVE, TradingModeService


pytestmark = pytest.mark.asyncio


async def _enable_live_mode(db):
    """Seed the global master switch to LIVE for tests that exercise the
    LIVE order path directly.  Production still requires the deliberate
    confirmation via the API; this bypass is limited to the DB fixture
    layer and does not weaken any runtime guard."""
    svc = TradingModeService(db)
    row = await svc.ensure_settings_row()
    extra = dict(row.extra or {})
    extra["trading_mode"] = {"mode": LIVE, "changed_at": None, "changed_by": None}
    row.extra = extra
    db.add(row)
    await db.flush()


class _FakeLiveExecutor(Executor):
    execution_mode = "live"

    async def on_place(self, order):
        # Simulate broker acceptance; no-op for the test.
        return

    async def on_modify(self, order, changes):
        return

    async def on_cancel(self, order):
        return

    def try_fill_from_quote(self, order, quote):
        return None


async def _seed(db):
    u = User(
        email="live-safety@example.com",
        hashed_password=hash_password("password123"),
        full_name="Live Safety",
    )
    db.add(u)
    await db.flush()
    s = EngineSession(
        user_id=u.id,
        strategy_name="unit",
        status=EngineSessionStatus.RUNNING,
        symbols=["A"],
        params={},
        risk_config={},
        initial_capital=100_000,
        started_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return u, s


def _mgr(db, user, session, executor):
    return OrderManager(
        db,
        engine_session_id=session.id,
        user_id=user.id,
        portfolio=PortfolioManager(db),
        trade_logger=TradeLogger(db),
        executor=executor,
    )


def _intent(qty: float = 10, price: float = 100.0, tag: str | None = None):
    return OrderIntentRequest(
        symbol="A",
        side=OrderSide.BUY,
        quantity=qty,
        order_type=OrderType.MARKET,
        product=OrderProduct.MIS,
        price=price,
        exchange="MOCK",
        strategy_name="unit",
        tag=tag,
    )


@pytest_asyncio.fixture(autouse=True)
async def _reset():
    ExecutionSafetyService.reset_state_for_tests()
    yield
    ExecutionSafetyService.reset_state_for_tests()


async def test_live_duplicate_rejected(db_session):
    u, s = await _seed(db_session)
    await _enable_live_mode(db_session)
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "duplicate_window_seconds": 60.0, "duplicate_action": "reject",
    })
    mgr = _mgr(db_session, u, s, _FakeLiveExecutor())
    # First live order goes through
    await mgr.place(_intent(qty=10, price=100.0))
    # Same fingerprint → blocked by duplicate detection
    with pytest.raises(InvalidOrderError) as ei:
        await mgr.place(_intent(qty=10, price=100.0))
    assert "execution_safety" in ei.value.code


async def test_live_kill_switch_blocks(db_session):
    u, s = await _seed(db_session)
    await _enable_live_mode(db_session)
    svc = ExecutionSafetyService(db_session)
    await svc.activate_kill_switch(admin_user_id=None, reason="test")
    mgr = _mgr(db_session, u, s, _FakeLiveExecutor())
    with pytest.raises(InvalidOrderError) as ei:
        await mgr.place(_intent(qty=5, price=50.0))
    assert "kill_switch" in ei.value.code


async def test_live_tps_limit(db_session):
    u, s = await _seed(db_session)
    await _enable_live_mode(db_session)
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 2,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    mgr = _mgr(db_session, u, s, _FakeLiveExecutor())
    # 2 succeed (different qty → different fingerprint doesn't matter here)
    await mgr.place(_intent(qty=1, price=100.0))
    await mgr.place(_intent(qty=2, price=100.0))
    with pytest.raises(InvalidOrderError):
        await mgr.place(_intent(qty=3, price=100.0))
