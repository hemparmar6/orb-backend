"""Task 3 — LIVE strategy restart/recovery + broker reconciliation.

These tests lock in the fail-closed recovery contract implemented in
``app.services.live_recovery_service.LiveRecoveryService`` +
``StrategyManager.resume_session``:

- Running LIVE session + clean restart          -> safely recovered (resumed).
- LIVE broker position matches ORB              -> recovered/reconciled.
- Broker has position, ORB DB does not          -> does NOT blindly resume.
- ORB has position but broker is flat           -> ORB state reconciled safely.
- Broker has unknown pending order              -> no duplicate order created.
- Existing broker order                         -> recovery submits no new order.
- Broker connection failure                     -> live strategy does not resume.
- Conflicting broker/ORB state                  -> manual reconciliation state.
- Recovery called twice                         -> no duplicate orders (idempotent).
- PAPER sessions remain unaffected.

The engine reaches the broker only through the ``BrokerAdapter`` abstraction,
so exercising it with the sanctioned ``MockLiveBroker`` proves the production
behaviour without opening a real broker connection. A slow-tick mock
market-data provider is injected so the resumed runner does not trade during
assertions.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from app.brokers.mock_live import MockLiveBroker
from app.core.exceptions import BrokerError
from app.engine.market_data.mock import MockMarketDataProvider
from app.engine.strategy.base import BaseStrategy
from app.engine.strategy.manager import StrategyManager
from app.engine.strategy.registry import register_strategy
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    PaperPosition,
)
from app.models.user import User
from app.services.live_recovery_service import LiveRecoveryService

BROKER_ACCOUNT_ID = "recover-acct-1"


# A strategy that never trades — keeps recovery/resume assertions deterministic.
@register_strategy("noop_recovery")
class _NoOpStrategy(BaseStrategy):
    async def on_tick(self, quote) -> None:  # noqa: D401
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slow_provider() -> MockMarketDataProvider:
    # 60s tick cadence → effectively no ticks during a test.
    return MockMarketDataProvider(tick_interval_ms=60_000)


# ---- fixtures -------------------------------------------------------------


@pytest_asyncio.fixture
async def factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def rec_manager(factory):
    m = StrategyManager(session_factory=factory)
    yield m
    await m.shutdown()


@pytest_asyncio.fixture
async def user_id(factory) -> str:
    async with factory() as db:
        u = User(
            email=f"rec-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            full_name="Recovery Tester",
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


# ---- db helpers -----------------------------------------------------------


async def _make_session(
    factory,
    user_id: str,
    *,
    status: EngineSessionStatus = EngineSessionStatus.RUNNING,
    mode: ExecutionMode = ExecutionMode.LIVE,
    broker_account_id: str | None = BROKER_ACCOUNT_ID,
    symbols: list[str] | None = None,
    strategy: str = "noop_recovery",
) -> str:
    async with factory() as db:
        sess = EngineSession(
            user_id=user_id,
            strategy_name=strategy,
            status=status,
            execution_mode=mode,
            broker_account_id=broker_account_id,
            symbols=symbols or ["A"],
            params={},
            risk_config={},
            initial_capital=100_000,
            started_at=_now(),
            last_heartbeat_at=_now(),
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        return sess.id


async def _add_position(factory, user_id, session_id, symbol, net, *, exchange="NSE"):
    async with factory() as db:
        db.add(
            PaperPosition(
                user_id=user_id,
                engine_session_id=session_id,
                symbol=symbol,
                exchange=exchange,
                product=OrderProduct.MIS,
                net_quantity=net,
                average_price=100,
                realized_pnl=0,
                opened_at=_now(),
            )
        )
        await db.commit()


async def _add_open_order(
    factory, user_id, session_id, symbol, *, broker_order_id, exchange="NSE"
) -> str:
    async with factory() as db:
        o = PaperOrder(
            user_id=user_id,
            engine_session_id=session_id,
            symbol=symbol,
            exchange=exchange,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product=OrderProduct.MIS,
            quantity=5,
            price=99,
            status=OrderStatus.OPEN,
            broker_order_id=broker_order_id,
            broker_account_id=BROKER_ACCOUNT_ID,
            strategy_name="noop_recovery",
            placed_at=_now(),
        )
        db.add(o)
        await db.commit()
        await db.refresh(o)
        return o.id


async def _status(factory, session_id) -> EngineSessionStatus:
    async with factory() as db:
        s = await db.get(EngineSession, session_id)
        return s.status


async def _position_net(factory, session_id, symbol) -> float:
    async with factory() as db:
        stmt = select(PaperPosition).where(
            PaperPosition.engine_session_id == session_id,
            PaperPosition.symbol == symbol,
        )
        rows = (await db.execute(stmt)).scalars().all()
        return sum(float(p.net_quantity) for p in rows)


async def _order_count(factory, session_id) -> int:
    async with factory() as db:
        rows = (
            await db.execute(
                select(PaperOrder).where(PaperOrder.engine_session_id == session_id)
            )
        ).scalars().all()
        return len(rows)


def _seed_broker_position(adapter: MockLiveBroker, symbol, net, *, exchange="NSE", product="mis"):
    adapter._positions[(symbol, exchange, product)] = BrokerPositionSnapshot(
        symbol=symbol, exchange=exchange, product=product,
        net_quantity=net, average_price=100.0, last_price=100.0,
    )


async def _adapter(auto_fill: bool = False) -> MockLiveBroker:
    a = MockLiveBroker({"auto_fill": auto_fill})
    await a.start()
    return a


def _svc(factory, rec_manager) -> LiveRecoveryService:
    return LiveRecoveryService(session_factory=factory, manager=rec_manager)


# ==========================================================================
# 1. Clean restart of a running LIVE session -> safely recovered.
#    Also proves: existing broker order is NOT re-submitted during recovery.
# ==========================================================================


@pytest.mark.asyncio
async def test_clean_restart_resumes_and_no_duplicate_order(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 10)

    adapter = await _adapter(auto_fill=False)
    _seed_broker_position(adapter, "A", 10)
    # A resting broker order that ORB already knows about.
    placed = await adapter.place_order(BrokerOrderRequest(
        client_order_id="c1", symbol="A", exchange="NSE",
        side="buy", order_type="limit", product="mis", quantity=5, price=99,
    ))
    await _add_open_order(factory, user_id, sid, "A", broker_order_id=placed.broker_order_id)

    broker_orders_before = len(await adapter.list_orders())
    orb_orders_before = await _order_count(factory, sid)

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )

    assert res.action == "resumed"
    assert res.resumed is True
    assert rec_manager.is_running(sid)
    assert await _status(factory, sid) == EngineSessionStatus.RUNNING
    # Recovery must not submit or duplicate any order.
    assert len(await adapter.list_orders()) == broker_orders_before
    assert await _order_count(factory, sid) == orb_orders_before


# ==========================================================================
# 2. LIVE broker position exists and matches ORB -> recovered/reconciled.
# ==========================================================================


@pytest.mark.asyncio
async def test_matching_live_position_is_recovered(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 7)
    adapter = await _adapter()
    _seed_broker_position(adapter, "A", 7)

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "resumed"
    assert rec_manager.is_running(sid)


# ==========================================================================
# 3. Broker has a position ORB does not -> strategy does NOT blindly resume.
# ==========================================================================


@pytest.mark.asyncio
async def test_broker_position_unknown_to_orb_blocks_resume(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    adapter = await _adapter()
    _seed_broker_position(adapter, "A", 5)  # ORB has NO position

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "needs_reconcile"
    assert res.resumed is False
    assert not rec_manager.is_running(sid)
    assert await _status(factory, sid) == EngineSessionStatus.NEEDS_RECONCILE
    # Broker position left untouched — never auto-closed.
    positions = await adapter.list_positions()
    assert any(abs(float(p.net_quantity)) > 0 for p in positions)


# ==========================================================================
# 4. ORB thinks it has a position but broker is flat -> reconcile ORB safely.
# ==========================================================================


@pytest.mark.asyncio
async def test_orb_position_broker_flat_is_reconciled(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 10)
    adapter = await _adapter()  # broker reports NO positions (flat)

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "resumed"
    assert "A" in res.reconciled_positions
    # ORB state reconciled DOWN to the broker (flat) — not recreated.
    assert await _position_net(factory, sid, "A") == 0
    assert await _order_count(factory, sid) == 0  # never recreated a position


# ==========================================================================
# 5. Broker has an unknown pending order -> block resume, no duplicate.
# ==========================================================================


@pytest.mark.asyncio
async def test_unknown_broker_pending_order_blocks_resume_no_duplicate(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    adapter = await _adapter(auto_fill=False)
    # A pending broker order ORB does NOT know about (no PaperOrder for it).
    await adapter.place_order(BrokerOrderRequest(
        client_order_id="ghost", symbol="A", exchange="NSE",
        side="buy", order_type="limit", product="mis", quantity=5, price=99,
    ))
    broker_orders_before = len(await adapter.list_orders())

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "needs_reconcile"
    assert res.resumed is False
    assert res.decision["unknown_broker_orders"]
    # No duplicate order placed, and ORB created no local order.
    assert len(await adapter.list_orders()) == broker_orders_before
    assert await _order_count(factory, sid) == 0


# ==========================================================================
# 6. Existing broker order -> recovery does not submit another order.
#    (Standalone: an ORB open order that exists at the broker.)
# ==========================================================================


@pytest.mark.asyncio
async def test_existing_broker_order_not_resubmitted(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    adapter = await _adapter(auto_fill=False)
    placed = await adapter.place_order(BrokerOrderRequest(
        client_order_id="c1", symbol="A", exchange="NSE",
        side="buy", order_type="limit", product="mis", quantity=5, price=99,
    ))
    await _add_open_order(factory, user_id, sid, "A", broker_order_id=placed.broker_order_id)
    before = len(await adapter.list_orders())

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "resumed"
    assert len(await adapter.list_orders()) == before


# ==========================================================================
# 7. Broker connection failure -> live strategy does not resume.
# ==========================================================================


class _FailingBroker(MockLiveBroker):
    async def list_positions(self):
        raise BrokerError("broker unreachable")


@pytest.mark.asyncio
async def test_broker_connection_failure_does_not_resume(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 10)
    adapter = _FailingBroker({"auto_fill": False})
    await adapter.start()

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "needs_reconcile"
    assert res.resumed is False
    assert not rec_manager.is_running(sid)
    assert await _status(factory, sid) == EngineSessionStatus.NEEDS_RECONCILE
    assert "broker_connection_failed" in (res.reason or "")


# ==========================================================================
# 8. Conflicting broker/ORB state -> manual reconciliation state.
# ==========================================================================


@pytest.mark.asyncio
async def test_quantity_conflict_marks_needs_reconcile(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 10)
    adapter = await _adapter()
    _seed_broker_position(adapter, "A", 5)  # broker holds a DIFFERENT quantity

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "needs_reconcile"
    assert not rec_manager.is_running(sid)
    assert await _status(factory, sid) == EngineSessionStatus.NEEDS_RECONCILE
    assert any(
        m["type"] == "quantity_mismatch" for m in res.decision["position_mismatches"]
    )


# ==========================================================================
# 9. Recovery called twice -> idempotent, no duplicate orders.
# ==========================================================================


@pytest.mark.asyncio
async def test_recovery_twice_is_idempotent(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 10)
    adapter = await _adapter(auto_fill=False)
    _seed_broker_position(adapter, "A", 10)
    placed = await adapter.place_order(BrokerOrderRequest(
        client_order_id="c1", symbol="A", exchange="NSE",
        side="buy", order_type="limit", product="mis", quantity=5, price=99,
    ))
    await _add_open_order(factory, user_id, sid, "A", broker_order_id=placed.broker_order_id)

    svc = _svc(factory, rec_manager)
    res1 = await svc.recover_session(sid, adapter=adapter, provider=_slow_provider())
    assert res1.action == "resumed"
    broker_after_first = len(await adapter.list_orders())
    orb_after_first = await _order_count(factory, sid)

    res2 = await svc.recover_session(sid, adapter=adapter, provider=_slow_provider())
    assert res2.action == "already_running"
    assert rec_manager.is_running(sid)
    # No new/duplicate orders on the second pass.
    assert len(await adapter.list_orders()) == broker_after_first
    assert await _order_count(factory, sid) == orb_after_first


# ==========================================================================
# 10. PAPER sessions remain unaffected.
# ==========================================================================


@pytest.mark.asyncio
async def test_paper_session_is_untouched_by_recovery(factory, rec_manager, user_id):
    sid = await _make_session(
        factory, user_id, mode=ExecutionMode.PAPER, broker_account_id=None
    )

    results = await _svc(factory, rec_manager).recover_all()

    # recover_all only targets RUNNING *live* sessions.
    assert all(r.engine_session_id != sid for r in results)
    assert await _status(factory, sid) == EngineSessionStatus.RUNNING
    assert not rec_manager.is_running(sid)


# ==========================================================================
# 11. Kill switch still works AFTER recovery (Task 2 stays intact).
# ==========================================================================


@pytest.mark.asyncio
async def test_kill_switch_after_recovery(factory, rec_manager, user_id):
    sid = await _make_session(factory, user_id)
    await _add_position(factory, user_id, sid, "A", 5)
    adapter = MockLiveBroker({"auto_fill": True, "simulated_ltp": {"A": 100.0}})
    await adapter.start()
    _seed_broker_position(adapter, "A", 5)

    res = await _svc(factory, rec_manager).recover_session(
        sid, adapter=adapter, provider=_slow_provider()
    )
    assert res.action == "resumed"
    assert rec_manager.is_running(sid)

    flatten_results = await rec_manager.emergency_flatten_all(reason="test")
    assert len(flatten_results) == 1
    positions = await adapter.list_positions()
    assert all(abs(float(p.net_quantity)) <= 1e-9 for p in positions)


# ==========================================================================
# 12. Pure reconcile() decision matrix (fast, no DB).
# ==========================================================================


def _decision(svc, *, orb_net=None, orb_orders=None, broker_pos=None, broker_ord=None, known=None):
    return svc.reconcile(
        orb_net=orb_net or {},
        orb_open_orders=orb_orders or [],
        broker_positions=broker_pos or [],
        broker_orders=broker_ord or [],
        known_broker_ids=known or set(),
    )


def test_reconcile_decision_matrix():
    svc = LiveRecoveryService(session_factory=None, manager=SimpleNamespace(is_running=lambda _: False))

    pos_a10 = BrokerPositionSnapshot("A", "NSE", "mis", 10, 100.0)
    pos_a5 = BrokerPositionSnapshot("A", "NSE", "mis", 5, 100.0)

    # exact match -> safe
    assert _decision(svc, orb_net={"A": 10}, broker_pos=[pos_a10]).safe is True

    # broker extra -> unsafe
    d = _decision(svc, orb_net={}, broker_pos=[pos_a10])
    assert d.safe is False and d.position_mismatches

    # orb-only (broker flat) -> safe, flagged for DB reconcile
    d = _decision(svc, orb_net={"A": 10}, broker_pos=[])
    assert d.safe is True and d.orb_only_symbols == ["A"]

    # quantity mismatch -> unsafe
    d = _decision(svc, orb_net={"A": 10}, broker_pos=[pos_a5])
    assert d.safe is False

    # unknown open broker order -> unsafe
    bo = BrokerOrderResult(broker_order_id="BX", status=BrokerOrderStatus.OPEN)
    assert _decision(svc, broker_ord=[bo], known=set()).safe is False
    # ...but known -> safe
    assert _decision(svc, broker_ord=[bo], known={"BX"}).safe is True

    # orb open order missing at broker -> unsafe
    o = SimpleNamespace(id="o1", broker_order_id="B1")
    assert _decision(svc, orb_orders=[o], broker_ord=[]).safe is False
    # orb open order with no broker id (crashed mid-submit) -> unsafe
    o2 = SimpleNamespace(id="o2", broker_order_id=None)
    assert _decision(svc, orb_orders=[o2]).safe is False
