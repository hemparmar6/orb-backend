"""Milestone 9 — RiskManagementService unit + integration tests."""
from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.bot import Bot, BotStatus
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    PaperPosition,
    PaperTrade,
)
from app.models.risk_management import (
    RiskAction,
    RiskBreach,
    RiskEventType,
    RiskLimit,
    RiskSeverity,
)
from app.models.user import User, UserRole
from app.services.risk_management_service import (
    RiskManagementService,
    RiskOrderContext,
)


@pytest_asyncio.fixture
async def user_row(db_session):
    u = User(
        email="risk.user@example.com",
        hashed_password="$2b$12$fakehashfakehashfakehashfakehash",
        full_name="Risk User",
        role=UserRole.USER,
        is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    return u


@pytest_asyncio.fixture
async def engine_sess(db_session, user_row):
    es = EngineSession(
        user_id=user_row.id,
        strategy_name="orb_intraday",
        symbols=["ACME"],
        execution_mode=ExecutionMode.PAPER,
        status=EngineSessionStatus.RUNNING,
        initial_capital=Decimal("100000"),
    )
    db_session.add(es)
    await db_session.commit()
    return es


# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_crud(db_session, user_row):
    svc = RiskManagementService(db_session)
    # Fresh user → ensure creates a row with defaults
    row = await svc.ensure_limit(user_row.id)
    assert row.user_id == user_row.id
    assert row.live_trading_enabled is True
    assert row.force_paper_mode is False
    # Upsert some fields
    row = await svc.upsert_limit(
        user_row.id,
        daily_loss_limit=1000.0,
        max_trades_per_day=5,
        trading_session_start="09:15",
        trading_session_end="15:30",
        trading_session_timezone="Asia/Kolkata",
    )
    await db_session.commit()
    fresh = await RiskManagementService(db_session).get_limit(user_row.id)
    assert float(fresh.daily_loss_limit) == 1000.0
    assert fresh.max_trades_per_day == 5
    assert fresh.trading_session_start == "09:15"


@pytest.mark.asyncio
async def test_check_order_no_limits_allows(db_session, user_row):
    svc = RiskManagementService(db_session)
    ctx = RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=10, price=100, execution_mode="paper",
    )
    d = await svc.check_order(ctx)
    assert d.allowed is True


async def _seed_trade(db_session, user, engine_sess, pnl, symbol="ACME"):
    """Helper — insert a PaperOrder + PaperTrade pair."""
    from datetime import datetime, timezone
    order = PaperOrder(
        user_id=user.id, engine_session_id=engine_sess.id,
        symbol=symbol, exchange="MOCK",
        side=OrderSide.BUY, order_type=OrderType.MARKET,
        quantity=Decimal("1"), status=OrderStatus.FILLED,
        filled_quantity=Decimal("1"), average_fill_price=Decimal("100"),
    )
    db_session.add(order)
    await db_session.flush()
    trade = PaperTrade(
        user_id=user.id, engine_session_id=engine_sess.id,
        paper_order_id=order.id,
        symbol=symbol, exchange="MOCK", side=OrderSide.BUY,
        quantity=Decimal("1"), price=Decimal("100"),
        realized_pnl_delta=Decimal(str(pnl)),
        executed_at=datetime.now(timezone.utc),
    )
    db_session.add(trade)
    await db_session.flush()
    return trade


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_new_entries(db_session, user_row, engine_sess):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, daily_loss_limit=500.0)
    await _seed_trade(db_session, user_row, engine_sess, pnl=-600)
    await db_session.commit()

    ctx = RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=1, price=100, execution_mode="paper",
    )
    d = await svc.check_order(ctx)
    assert d.allowed is False
    assert d.event_type == RiskEventType.DAILY_LOSS_LIMIT
    assert d.severity == RiskSeverity.CRITICAL
    # And the breach was recorded
    breaches = list(
        (await db_session.execute(select(RiskBreach))).scalars().all()
    )
    assert len(breaches) == 1
    assert breaches[0].event_type == RiskEventType.DAILY_LOSS_LIMIT


@pytest.mark.asyncio
async def test_daily_loss_allows_exits(db_session, user_row, engine_sess):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, daily_loss_limit=100.0)
    await _seed_trade(db_session, user_row, engine_sess, pnl=-200)
    await db_session.commit()
    ctx = RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="sell",
        quantity=1, price=100, is_exit=True,
    )
    d = await svc.check_order(ctx)
    assert d.allowed is True  # exits bypass the loss gate


@pytest.mark.asyncio
async def test_max_trades_per_day(db_session, user_row, engine_sess):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, max_trades_per_day=2)
    for _ in range(2):
        await _seed_trade(db_session, user_row, engine_sess, pnl=0)
    await db_session.commit()
    d = await svc.check_order(RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=1, price=100,
    ))
    assert d.allowed is False
    assert d.event_type == RiskEventType.MAX_TRADES_PER_DAY


@pytest.mark.asyncio
async def test_max_position_size(db_session, user_row, engine_sess):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, max_position_size=10.0)
    db_session.add(PaperPosition(
        user_id=user_row.id, engine_session_id=engine_sess.id,
        symbol="ACME", exchange="MOCK",
        net_quantity=Decimal("8"), average_price=Decimal("100"),
        last_price=Decimal("100"),
    ))
    await db_session.commit()
    d = await svc.check_order(RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=5, price=100,
    ))
    assert d.allowed is False
    assert d.event_type == RiskEventType.MAX_POSITION_SIZE


@pytest.mark.asyncio
async def test_max_exposure_per_symbol(db_session, user_row):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, max_exposure_per_symbol=1000.0)
    d = await svc.check_order(RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=20, price=100,
    ))
    assert d.allowed is False
    assert d.event_type == RiskEventType.MAX_EXPOSURE_PER_SYMBOL


@pytest.mark.asyncio
async def test_live_trading_disabled_blocks_live_orders(db_session, user_row):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, live_trading_enabled=False)
    d = await svc.check_order(RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=1, price=100, execution_mode="live",
    ))
    assert d.allowed is False
    assert d.event_type == RiskEventType.LIVE_TRADING_DISABLED
    # Paper still allowed
    d2 = await svc.check_order(RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=1, price=100, execution_mode="paper",
    ))
    assert d2.allowed is True


@pytest.mark.asyncio
async def test_force_paper_mode_blocks_live_orders(db_session, user_row):
    svc = RiskManagementService(db_session)
    await svc.upsert_limit(user_row.id, force_paper_mode=True)
    d = await svc.check_order(RiskOrderContext(
        user_id=user_row.id, symbol="ACME", side="buy",
        quantity=1, price=100, execution_mode="live",
    ))
    assert d.allowed is False
    assert d.event_type == RiskEventType.PAPER_MODE_FORCED


@pytest.mark.asyncio
async def test_pause_all_and_resume_all(db_session, user_row):
    for i in range(3):
        db_session.add(Bot(
            user_id=user_row.id, name=f"b{i}", strategy_key="orb",
            symbols=["ACME"], params={}, risk_config={},
            status=BotStatus.RUNNING,
        ))
    await db_session.commit()
    svc = RiskManagementService(db_session)
    ids, n = await svc.pause_all_bots(user_row.id, reason="test_pause")
    await db_session.commit()
    assert n == 3
    for bid in ids:
        b = await db_session.get(Bot, bid)
        assert b.status == BotStatus.PAUSED

    ids, n = await svc.resume_all_bots(user_row.id)
    assert n == 3
    for bid in ids:
        b = await db_session.get(Bot, bid)
        assert b.status == BotStatus.IDLE


@pytest.mark.asyncio
async def test_disable_live_trading_flow(db_session, user_row):
    db_session.add(Bot(
        user_id=user_row.id, name="live_bot", strategy_key="orb",
        symbols=["ACME"], params={}, risk_config={},
        execution_mode="live", status=BotStatus.RUNNING,
    ))
    await db_session.commit()
    svc = RiskManagementService(db_session)
    ids, n = await svc.disable_live_trading(user_row.id, reason="risk_disable_live")
    await db_session.commit()
    assert n == 1
    row = await svc.get_limit(user_row.id)
    assert row.live_trading_enabled is False


@pytest.mark.asyncio
async def test_return_to_paper_trading_flow(db_session, user_row):
    db_session.add(Bot(
        user_id=user_row.id, name="live_bot2", strategy_key="orb",
        symbols=["ACME"], params={}, risk_config={},
        execution_mode="live", status=BotStatus.RUNNING,
    ))
    await db_session.commit()
    svc = RiskManagementService(db_session)
    ids, n = await svc.return_to_paper_trading(user_row.id)
    await db_session.commit()
    assert n == 1
    row = await svc.get_limit(user_row.id)
    assert row.force_paper_mode is True
    b = (await db_session.execute(
        select(Bot).where(Bot.user_id == user_row.id)
    )).scalars().first()
    assert b.execution_mode == "paper"


@pytest.mark.asyncio
async def test_prune_old_breaches(db_session, user_row):
    from datetime import datetime, timedelta, timezone
    svc = RiskManagementService(db_session)
    old = RiskBreach(
        user_id=user_row.id, event_type=RiskEventType.DAILY_LOSS_LIMIT,
        severity=RiskSeverity.CRITICAL, action_taken=RiskAction.BLOCKED,
        reason="old", payload={},
    )
    db_session.add(old)
    await db_session.commit()
    old.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    await db_session.commit()
    removed = await svc.prune_old_breaches(retention_days=90)
    await db_session.commit()
    assert removed == 1


@pytest.mark.asyncio
async def test_admin_overview(db_session, user_row):
    svc = RiskManagementService(db_session)
    db_session.add_all([
        RiskBreach(
            user_id=user_row.id, event_type=RiskEventType.DAILY_LOSS_LIMIT,
            severity=RiskSeverity.CRITICAL, action_taken=RiskAction.BLOCKED,
            reason="a", payload={},
        ),
        RiskBreach(
            user_id=user_row.id, event_type=RiskEventType.MAX_TRADES_PER_DAY,
            severity=RiskSeverity.WARNING, action_taken=RiskAction.BLOCKED,
            reason="b", payload={},
        ),
    ])
    await db_session.commit()
    out = await svc.admin_overview(since_minutes=60 * 24 * 7)
    assert out["total_breaches"] == 2
    assert any(x["label"] == "daily_loss_limit" for x in out["by_event_type"])
