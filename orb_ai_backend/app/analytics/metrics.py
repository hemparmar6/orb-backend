"""Module 9 — portfolio & analytics metrics.

Pure functions over trade dicts. The trade dict shape used everywhere in
Module 9 is::

    {"id": str, "pnl": float, "entry_price": float, "exit_price": float,
     "quantity": float, "side": "buy"|"sell", "opened_at": datetime|None,
     "closed_at": datetime|None, ...}

The service layer (:mod:`app.services.ai_analytics_service`) is
responsible for converting ``PaperTrade`` rows into this shape.
"""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any, Dict, List


def compute_portfolio_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return _empty()

    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = len(wins) / len(pnls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) or 1e-9
    profit_factor = gross_win / gross_loss

    return {
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "expected_value": round(mean(pnls), 4),
        "sharpe": round(_sharpe(pnls), 4),
        "sortino": round(_sortino(pnls), 4),
        "max_drawdown_pct": round(_max_dd_pct(pnls), 4),
        "total_trades": len(pnls),
        "total_pnl": round(sum(pnls), 4),
    }


def win_probability(trades: List[Dict[str, Any]], last_n: int = 100) -> float:
    if not trades:
        return 0.0
    tail = trades[-last_n:]
    wins = sum(1 for t in tail if float(t.get("pnl") or 0) > 0)
    return round(wins / len(tail), 4)


def expected_value(trades: List[Dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    return round(mean(float(t.get("pnl") or 0) for t in trades), 4)


def _empty() -> Dict[str, Any]:
    return {
        "win_rate": 0.0, "profit_factor": 0.0, "expected_value": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
        "total_trades": 0, "total_pnl": 0.0,
    }


def _sharpe(pnls: List[float]) -> float:
    if len(pnls) < 2:
        return 0.0
    sd = pstdev(pnls)
    return (mean(pnls) / sd) * math.sqrt(252) if sd else 0.0


def _sortino(pnls: List[float]) -> float:
    if len(pnls) < 2:
        return 0.0
    downside = [p for p in pnls if p < 0]
    if not downside:
        return _sharpe(pnls)
    dd = pstdev(downside) or 1e-9
    return (mean(pnls) / dd) * math.sqrt(252)


def _max_dd_pct(pnls: List[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return max_dd
