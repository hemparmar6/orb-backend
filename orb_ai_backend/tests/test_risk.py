"""RiskEngine tests."""
from __future__ import annotations

from datetime import datetime, time

import pytest

from app.engine.risk.engine import (
    OrderIntent,
    RiskConfig,
    RiskEngine,
    SessionSnapshot,
)


def _snap(**overrides):
    base = dict(initial_capital=100_000, current_capital=100_000, day_pnl=0, positions={})
    base.update(overrides)
    return SessionSnapshot(**base)


def test_allows_within_all_limits():
    eng = RiskEngine(RiskConfig(max_position_size=100, max_daily_loss=5000, max_risk_per_trade_pct=5))
    d = eng.check(OrderIntent("A", "buy", 10, price=100), _snap())
    assert d.allowed


def test_max_daily_loss_blocks_new_entries_but_allows_exits():
    eng = RiskEngine(RiskConfig(max_daily_loss=1000))
    snap = _snap(day_pnl=-1500, positions={"A": 10})
    entry = eng.check(OrderIntent("A", "buy", 5, price=100), snap)
    assert not entry.allowed
    exit_ = eng.check(OrderIntent("A", "sell", 5, price=100, is_exit=True), snap)
    assert exit_.allowed


def test_max_position_size_gates_when_adding():
    eng = RiskEngine(RiskConfig(max_position_size=50))
    d = eng.check(
        OrderIntent("A", "buy", 40, price=100),
        _snap(positions={"A": 20}),  # existing 20 + 40 = 60 > 50
    )
    assert not d.allowed
    assert "Position size" in (d.reason or "")


def test_max_risk_per_trade_pct_gates():
    eng = RiskEngine(RiskConfig(max_risk_per_trade_pct=1.0))
    # notional 10*100 = 1000; 1% of 100k = 1000 — exactly at limit, should allow
    ok = eng.check(OrderIntent("A", "buy", 10, price=100), _snap())
    assert ok.allowed
    # 11*100 = 1100 > 1% of 100k
    d = eng.check(OrderIntent("A", "buy", 11, price=100), _snap())
    assert not d.allowed


def test_trading_window_gates():
    # Force "now" to 05:00 — outside 09:15-15:30 window.
    fixed = datetime(2026, 1, 1, 5, 0, 0)
    eng = RiskEngine(
        RiskConfig(trading_session_start="09:15", trading_session_end="15:30",
                   trading_session_timezone="UTC"),
        now_fn=lambda: fixed,
    )
    d = eng.check(OrderIntent("A", "buy", 1, price=100), _snap())
    assert not d.allowed
    assert "trading session" in (d.reason or "").lower()


def test_position_sizer_hook_can_reduce_qty():
    eng = RiskEngine(
        RiskConfig(max_risk_per_trade_pct=100),
        position_sizer=lambda i, s, c: min(i.quantity, 3),
    )
    d = eng.check(OrderIntent("A", "buy", 10, price=100), _snap())
    assert d.allowed
    assert d.adjusted_quantity == 3
