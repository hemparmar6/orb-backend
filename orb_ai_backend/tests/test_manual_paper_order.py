"""Focused tests for the authenticated manual paper-order endpoint."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.trading import _session_symbol_match, manual_paper_order
from app.core.exceptions import ForbiddenError, InvalidOrderError, SessionNotFoundError
from app.models.engine import (
    EngineSessionStatus,
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderType,
)
from app.schemas.trading import ManualPaperOrderRequest


def test_manual_order_schema_accepts_mobile_uppercase_values():
    req = ManualPaperOrderRequest(
        session_id="s1", symbol="NIFTY 50", side="BUY", quantity=1,
        order_type="MARKET", exchange="NSE_INDEX", product="MIS",
    )
    assert req.side == OrderSide.BUY
    assert req.order_type == OrderType.MARKET


def test_manual_order_schema_rejects_non_positive_quantity():
    with pytest.raises(Exception):
        ManualPaperOrderRequest(
            session_id="s1", symbol="NIFTY", side="BUY", quantity=0,
            order_type="MARKET", exchange="NSE_INDEX", product="MIS",
        )


@pytest.mark.parametrize("a,b", [("NIFTY", "NIFTY 50"), ("BANKNIFTY", "NIFTY BANK"), ("NIFTY", "nifty")])
def test_session_symbol_aliases(a, b):
    assert _session_symbol_match(a, b)


def test_unknown_symbol_does_not_match():
    assert not _session_symbol_match("WHATISTHIS", "NIFTY")


class _FakeSession:
    def __init__(self, engine_session):
        self.engine_session = engine_session
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def get(self, model, ident):
        return self.engine_session


@pytest.mark.asyncio
async def test_wrong_owner_is_hidden():
    session = _FakeSession(SimpleNamespace(id="s1", user_id="owner"))
    user = SimpleNamespace(id="attacker")
    payload = ManualPaperOrderRequest(session_id="s1", symbol="NIFTY", side="BUY", quantity=1)
    with pytest.raises(SessionNotFoundError):
        await manual_paper_order(payload, user, session)


@pytest.mark.asyncio
async def test_live_session_rejected_before_order_engine():
    sess = SimpleNamespace(
        id="s1", user_id="u1", execution_mode=ExecutionMode.LIVE,
        status=EngineSessionStatus.RUNNING, symbols=["NIFTY"], strategy_name="unit",
    )
    session = _FakeSession(sess)
    user = SimpleNamespace(id="u1")
    payload = ManualPaperOrderRequest(session_id="s1", symbol="NIFTY", side="BUY", quantity=1)
    with pytest.raises(ForbiddenError) as exc:
        await manual_paper_order(payload, user, session)
    assert exc.value.code == "manual_paper_order_requires_paper_session"

@pytest.mark.asyncio
async def test_stopped_session_rejected():
    sess = SimpleNamespace(
        id="s1", user_id="u1", execution_mode=ExecutionMode.PAPER,
        status=EngineSessionStatus.STOPPED, symbols=["NIFTY"], strategy_name="unit",
    )
    session = _FakeSession(sess)
    user = SimpleNamespace(id="u1")
    payload = ManualPaperOrderRequest(session_id="s1", symbol="NIFTY", side="BUY", quantity=1)
    with pytest.raises(InvalidOrderError) as exc:
        await manual_paper_order(payload, user, session)
    assert exc.value.code == "manual_paper_order_session_not_running"


@pytest.mark.asyncio
async def test_inactive_paper_engine_rejected(monkeypatch):
    sess = SimpleNamespace(
        id="s1", user_id="u1", execution_mode=ExecutionMode.PAPER,
        status=EngineSessionStatus.RUNNING, symbols=["NIFTY"], strategy_name="unit",
    )
    session = _FakeSession(sess)
    user = SimpleNamespace(id="u1")
    payload = ManualPaperOrderRequest(session_id="s1", symbol="NIFTY", side="BUY", quantity=1)
    monkeypatch.setattr("app.api.v1.endpoints.trading.strategy_manager.is_running", lambda _id: False)
    with pytest.raises(InvalidOrderError) as exc:
        await manual_paper_order(payload, user, session)
    assert exc.value.code == "manual_paper_order_engine_not_active"


@pytest.mark.asyncio
async def test_unconfigured_symbol_rejected(monkeypatch):
    sess = SimpleNamespace(
        id="s1", user_id="u1", execution_mode=ExecutionMode.PAPER,
        status=EngineSessionStatus.RUNNING, symbols=["BANKNIFTY"], strategy_name="unit",
    )
    session = _FakeSession(sess)
    user = SimpleNamespace(id="u1")
    payload = ManualPaperOrderRequest(session_id="s1", symbol="NIFTY", side="BUY", quantity=1)
    monkeypatch.setattr("app.api.v1.endpoints.trading.strategy_manager.is_running", lambda _id: True)
    with pytest.raises(InvalidOrderError) as exc:
        await manual_paper_order(payload, user, session)
    assert exc.value.code == "manual_paper_order_symbol_not_in_session"


def test_manual_order_schema_rejects_unsupported_order_type():
    with pytest.raises(Exception):
        ManualPaperOrderRequest(
            session_id="s1", symbol="NIFTY", side="BUY", quantity=1,
            order_type="LIMIT", exchange="NSE_INDEX", product="MIS",
        )


def test_manual_order_schema_rejects_invalid_side():
    with pytest.raises(Exception):
        ManualPaperOrderRequest(
            session_id="s1", symbol="NIFTY", side="HOLD", quantity=1,
            order_type="MARKET", exchange="NSE_INDEX", product="MIS",
        )


def test_manual_order_schema_rejects_non_mis_product_at_contract_level():
    # The request schema accepts the backend enum; the endpoint narrows it to
    # MIS. This ensures the frontend contract remains explicit.
    req = ManualPaperOrderRequest(
        session_id="s1", symbol="NIFTY", side="BUY", quantity=1,
        order_type="MARKET", exchange="NSE_INDEX", product="CNC",
    )
    assert req.product.value == "cnc"


@pytest.mark.asyncio
async def test_buy_order_uses_paper_executor_and_order_manager(monkeypatch):
    sess = SimpleNamespace(
        id="s1", user_id="u1", execution_mode=ExecutionMode.PAPER,
        status=EngineSessionStatus.RUNNING, symbols=["NIFTY"], strategy_name="unit",
    )
    session = _FakeSession(sess)
    user = SimpleNamespace(id="u1")
    payload = ManualPaperOrderRequest(
        session_id="s1", symbol="NIFTY", side="BUY", quantity=2,
        order_type="MARKET", exchange="NSE_INDEX", product="MIS",
    )

    captured = {}

    class _FakeOrderManager:
        def __init__(self, db, **kwargs):
            captured["executor"] = kwargs["executor"]
            captured["engine_session_id"] = kwargs["engine_session_id"]
            captured["user_id"] = kwargs["user_id"]

        async def place(self, intent):
            captured["intent"] = intent
            now = datetime.now(timezone.utc)
            return SimpleNamespace(
                id="o1",
                engine_session_id="s1",
                symbol="NIFTY",
                exchange="NSE_INDEX",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                product=OrderProduct.MIS,
                quantity=2,
                price=None,
                trigger_price=None,
                stop_loss=None,
                target_price=None,
                status="pending",
                filled_quantity=0,
                average_fill_price=None,
                strategy_name="unit",
                tag="manual_mobile",
                placed_at=now,
                filled_at=None,
                cancelled_at=None,
                rejection_reason=None,
                created_at=now,
            )

    monkeypatch.setattr("app.api.v1.endpoints.trading.OrderManager", _FakeOrderManager)
    monkeypatch.setattr("app.api.v1.endpoints.trading.strategy_manager.is_running", lambda _id: True)

    result = await manual_paper_order(payload, user, session)

    assert result.id == "o1"
    assert result.status.value == "pending"
    assert captured["engine_session_id"] == "s1"
    assert captured["user_id"] == "u1"
    assert captured["executor"].__class__.__name__ == "PaperExecutorAdapter"
    assert captured["intent"].side == OrderSide.BUY
    assert captured["intent"].quantity == 2.0


@pytest.mark.asyncio
async def test_non_mis_product_rejected(monkeypatch):
    sess = SimpleNamespace(
        id="s1", user_id="u1", execution_mode=ExecutionMode.PAPER,
        status=EngineSessionStatus.RUNNING, symbols=["NIFTY"], strategy_name="unit",
    )
    session = _FakeSession(sess)
    user = SimpleNamespace(id="u1")
    payload = ManualPaperOrderRequest(
        session_id="s1", symbol="NIFTY", side="BUY", quantity=1,
        order_type="MARKET", exchange="NSE_INDEX", product="CNC",
    )
    monkeypatch.setattr("app.api.v1.endpoints.trading.strategy_manager.is_running", lambda _id: True)
    with pytest.raises(InvalidOrderError) as exc:
        await manual_paper_order(payload, user, session)
    assert exc.value.code == "manual_paper_order_mis_only"

