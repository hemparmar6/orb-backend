"""Backtest metrics.

Given a list of closed trades and an equity curve, produce every KPI the
Module 5 spec requires:

- total_return_pct, net_profit
- gross_profit, gross_loss, profit_factor
- win_rate, avg_profit, avg_loss
- max_drawdown, max_drawdown_pct
- sharpe_ratio (daily-return based, annualised at 252 trading days)
- number_of_trades
- long / short split
- monthly_performance

Trades are dicts (JSON-friendly for direct persistence) with at least:
    { "side": "long"|"short", "entry_price": .., "exit_price": ..,
      "quantity": .., "pnl": .., "entry_time": iso, "exit_time": iso }
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev
from typing import Any, Iterable


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unexpected time value: {value!r}")


def _side_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "avg_pnl": 0.0,
        }
    pnls = [float(t["pnl"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "count": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls) * 100.0, 4),
        "net_pnl": round(sum(pnls), 4),
        "avg_pnl": round(sum(pnls) / len(pnls), 4),
    }


def compute_metrics(
    trades: Iterable[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    initial_capital: float,
) -> dict[str, Any]:
    trades = list(trades)
    n = len(trades)
    pnls = [float(t["pnl"]) for t in trades]

    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    gross_profit = sum(winners) if winners else 0.0
    gross_loss = abs(sum(losers)) if losers else 0.0
    net_profit = sum(pnls) if pnls else 0.0

    win_rate = (len(winners) / n * 100.0) if n else 0.0
    avg_profit = (gross_profit / len(winners)) if winners else 0.0
    avg_loss = (-gross_loss / len(losers)) if losers else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss else (
        float("inf") if gross_profit > 0 else 0.0
    )

    # ---- Equity-curve based metrics ------------------------------------
    equity_vals = [float(p["equity"]) for p in equity_curve] if equity_curve else [initial_capital]
    peak = equity_vals[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for v in equity_vals:
        if v > peak:
            peak = v
        drawdown = peak - v
        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_pct = (drawdown / peak * 100.0) if peak else 0.0

    final_equity = equity_vals[-1] if equity_vals else initial_capital
    total_return_pct = ((final_equity - initial_capital) / initial_capital * 100.0) \
        if initial_capital else 0.0

    # ---- Sharpe (daily returns → annualise by √252) --------------------
    sharpe = _sharpe_from_equity(equity_curve, initial_capital)

    # ---- Long / short split --------------------------------------------
    longs = [t for t in trades if str(t.get("side", "")).lower() == "long"]
    shorts = [t for t in trades if str(t.get("side", "")).lower() == "short"]

    # ---- Monthly performance -------------------------------------------
    monthly: dict[str, float] = defaultdict(float)
    monthly_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        et = _to_dt(t.get("exit_time") or t.get("entry_time"))
        key = et.strftime("%Y-%m")
        monthly[key] += float(t["pnl"])
        monthly_counts[key] += 1
    monthly_performance = [
        {"month": k, "net_pnl": round(monthly[k], 4), "trades": monthly_counts[k]}
        for k in sorted(monthly.keys())
    ]

    return {
        "total_return_pct": round(total_return_pct, 4),
        "net_profit": round(net_profit, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "win_rate_pct": round(win_rate, 4),
        "avg_profit": round(avg_profit, 4),
        "avg_loss": round(avg_loss, 4),
        "profit_factor": (
            "inf" if math.isinf(profit_factor) else round(profit_factor, 4)
        ),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "sharpe_ratio": round(sharpe, 4) if not math.isinf(sharpe) else "inf",
        "number_of_trades": n,
        "long_stats": _side_stats(longs),
        "short_stats": _side_stats(shorts),
        "monthly_performance": monthly_performance,
        "final_equity": round(final_equity, 4),
        "initial_capital": round(float(initial_capital), 4),
    }


def _sharpe_from_equity(
    equity_curve: list[dict[str, Any]], initial_capital: float
) -> float:
    """Simple Sharpe: mean(daily return) / stdev(daily return) * √252.

    Requires ≥ 2 daily equity points. When there are fewer, returns 0.
    """
    if not equity_curve:
        return 0.0

    # Bucket last equity per calendar day.
    per_day: dict[str, float] = {}
    for p in equity_curve:
        ts = _to_dt(p["ts"])
        per_day[ts.date().isoformat()] = float(p["equity"])

    days = sorted(per_day.keys())
    if len(days) < 2:
        return 0.0

    equities = [initial_capital] + [per_day[d] for d in days]
    returns: list[float] = []
    for i in range(1, len(equities)):
        prev, curr = equities[i - 1], equities[i]
        if prev <= 0:
            continue
        returns.append((curr - prev) / prev)

    if len(returns) < 2:
        return 0.0

    mu = mean(returns)
    sd = pstdev(returns)
    if sd == 0:
        return float("inf") if mu > 0 else 0.0
    return (mu / sd) * math.sqrt(252)
