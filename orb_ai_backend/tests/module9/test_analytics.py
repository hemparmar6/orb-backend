"""Module 9 — analytics helpers unit tests (pure functions)."""

from __future__ import annotations

from app.analytics.advanced_backtest import (
    apply_trading_costs,
    monte_carlo_pnl,
    portfolio_backtest,
    walk_forward_windows,
)
from app.analytics.equity_curve import equity_curve
from app.analytics.metrics import (
    compute_portfolio_metrics,
    expected_value,
    win_probability,
)


def _t(pnl, i, qty=10, ep=100, xp=101):
    return {
        "id": str(i), "pnl": pnl, "quantity": qty,
        "entry_price": ep, "exit_price": xp, "closed_at": i,
    }


def test_metrics_basic():
    trades = [_t(10, 1), _t(-5, 2), _t(20, 3), _t(-8, 4)]
    m = compute_portfolio_metrics(trades)
    assert m["total_trades"] == 4
    assert m["win_rate"] == 0.5
    assert m["total_pnl"] == 17.0
    assert m["profit_factor"] > 1.0


def test_metrics_empty():
    m = compute_portfolio_metrics([])
    assert m["total_trades"] == 0
    assert m["total_pnl"] == 0.0


def test_equity_curve_ordered():
    trades = [_t(1, 1), _t(-2, 2), _t(3, 3)]
    curve = equity_curve(trades)
    assert [pt["equity"] for pt in curve] == [1.0, -1.0, 2.0]


def test_win_probability_last_n():
    trades = [_t(-1, i) for i in range(50)] + [_t(1, i) for i in range(50, 100)]
    assert win_probability(trades, last_n=50) == 1.0


def test_expected_value():
    assert expected_value([_t(2, 1), _t(-1, 2)]) == 0.5


def test_monte_carlo_percentiles():
    out = monte_carlo_pnl([1.0, -0.5, 0.8, -0.2, 1.5], iterations=200, seed=1)
    assert out["iterations"] == 200
    assert "p5" in out["final_pnl"] and "p95" in out["final_pnl"]


def test_walk_forward_windows_count():
    from datetime import datetime
    w = walk_forward_windows(datetime(2024, 1, 1), datetime(2024, 6, 1), 30, 30)
    assert len(w) >= 2


def test_trading_costs_reduce_pnl():
    trades = [{
        "quantity": 10, "entry_price": 100, "exit_price": 101,
        "gross_pnl": 10.0, "pnl": 10.0,
    }]
    out = apply_trading_costs(trades, commission_bps=5, slippage_bps=2, spread_bps=3)
    assert out[0]["pnl"] < 10.0
    assert out[0]["commission_cost"] > 0
    assert out[0]["slippage_cost"] > 0
    assert out[0]["spread_cost"] > 0


def test_portfolio_backtest_combines_streams():
    def s1():
        return [{"pnl": 2.0, "closed_at": "1"}]

    def s2():
        return [{"pnl": 4.0, "closed_at": "2"}]

    out = portfolio_backtest([s1, s2], weights=[0.5, 0.5])
    assert out["total_pnl"] == 3.0
    assert len(out["trades"]) == 2
