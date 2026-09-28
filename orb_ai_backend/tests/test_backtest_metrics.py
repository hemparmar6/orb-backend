"""Unit tests for backtest metrics.

Uses a small hand-authored trade log so numbers are verifiable by inspection.
"""
from __future__ import annotations

from datetime import datetime, timezone

import math
import pytest

from app.engine.backtest.metrics import compute_metrics


def _trade(side: str, pnl: float, month: str = "2026-06") -> dict:
    d = datetime.fromisoformat(f"{month}-15T09:30:00+00:00")
    return {
        "symbol": "NIFTY",
        "side": side,
        "quantity": 1,
        "entry_time": d.isoformat(),
        "entry_price": 100.0,
        "exit_time": d.isoformat(),
        "exit_price": 100.0 + pnl,
        "pnl": pnl,
        "fees": 0.0,
        "exit_reason": "target",
    }


def test_metrics_all_positive_trades():
    trades = [_trade("long", 50), _trade("long", 30), _trade("long", 20)]
    equity_curve = [
        {"ts": "2026-06-15T09:30:00+00:00", "equity": 100_050},
        {"ts": "2026-06-15T09:35:00+00:00", "equity": 100_080},
        {"ts": "2026-06-15T09:40:00+00:00", "equity": 100_100},
    ]
    m = compute_metrics(trades, equity_curve, initial_capital=100_000)
    assert m["number_of_trades"] == 3
    assert m["gross_profit"] == 100.0
    assert m["gross_loss"] == 0.0
    assert m["net_profit"] == 100.0
    assert m["win_rate_pct"] == 100.0
    assert m["profit_factor"] == "inf"
    assert m["max_drawdown"] == 0.0
    assert m["long_stats"]["count"] == 3
    assert m["short_stats"]["count"] == 0


def test_metrics_with_wins_and_losses():
    trades = [
        _trade("long", 100),
        _trade("long", -60),
        _trade("short", 40),
        _trade("short", -20),
    ]
    equity_curve = [
        {"ts": "2026-06-15T09:30:00+00:00", "equity": 100_100},
        {"ts": "2026-06-15T10:30:00+00:00", "equity": 100_040},
        {"ts": "2026-06-16T09:30:00+00:00", "equity": 100_080},
        {"ts": "2026-06-16T10:30:00+00:00", "equity": 100_060},
    ]
    m = compute_metrics(trades, equity_curve, initial_capital=100_000)
    assert m["number_of_trades"] == 4
    assert m["gross_profit"] == 140.0
    assert m["gross_loss"] == 80.0
    assert m["net_profit"] == 60.0
    assert m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == 1.75
    # Max drawdown: peak 100_100 -> trough 100_040 -> drawdown 60
    assert m["max_drawdown"] == 60.0
    assert m["long_stats"]["count"] == 2
    assert m["short_stats"]["count"] == 2
    assert m["long_stats"]["wins"] == 1
    assert m["short_stats"]["wins"] == 1


def test_metrics_monthly_grouping():
    trades = [
        _trade("long", 10, "2026-04"),
        _trade("long", 20, "2026-04"),
        _trade("long", -5, "2026-05"),
    ]
    m = compute_metrics(trades, [], initial_capital=100_000)
    months = {row["month"]: row for row in m["monthly_performance"]}
    assert months["2026-04"]["net_pnl"] == 30.0
    assert months["2026-04"]["trades"] == 2
    assert months["2026-05"]["net_pnl"] == -5.0


def test_metrics_max_drawdown_pct():
    equity_curve = [
        {"ts": "2026-06-15T09:30:00+00:00", "equity": 110_000},  # peak
        {"ts": "2026-06-16T09:30:00+00:00", "equity": 99_000},   # drawdown 10 %
        {"ts": "2026-06-17T09:30:00+00:00", "equity": 100_000},
    ]
    m = compute_metrics([], equity_curve, initial_capital=100_000)
    assert m["max_drawdown"] == 11_000.0
    # 11_000 / 110_000 * 100 = 10.0
    assert m["max_drawdown_pct"] == 10.0


def test_metrics_sharpe_zero_when_flat():
    equity_curve = [
        {"ts": "2026-06-15T09:30:00+00:00", "equity": 100_000},
        {"ts": "2026-06-16T09:30:00+00:00", "equity": 100_000},
        {"ts": "2026-06-17T09:30:00+00:00", "equity": 100_000},
    ]
    m = compute_metrics([], equity_curve, initial_capital=100_000)
    assert m["sharpe_ratio"] == 0.0


def test_metrics_handles_empty_input():
    m = compute_metrics([], [], initial_capital=100_000)
    assert m["number_of_trades"] == 0
    assert m["net_profit"] == 0.0
    assert m["win_rate_pct"] == 0.0
    assert m["max_drawdown"] == 0.0
    assert m["total_return_pct"] == 0.0
