"""Advanced backtest helpers — walk-forward, Monte Carlo, cost wrapping,
portfolio-level and multi-strategy backtests.

These are pure functions the existing Module-5 backtester can call.
They do not replace the existing engine.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List

from app.analytics.costs import apply_commission, apply_slippage, apply_spread


# -------- walk-forward --------

@dataclass
class WalkForwardWindow:
    in_sample: tuple
    out_sample: tuple


def walk_forward_windows(
    start: datetime, end: datetime, in_sample_days: int, out_sample_days: int,
) -> List[WalkForwardWindow]:
    windows: List[WalkForwardWindow] = []
    cursor = start
    while cursor + timedelta(days=in_sample_days + out_sample_days) <= end:
        is_end = cursor + timedelta(days=in_sample_days)
        oos_end = is_end + timedelta(days=out_sample_days)
        windows.append(WalkForwardWindow((cursor, is_end), (is_end, oos_end)))
        cursor = oos_end
    return windows


# -------- Monte Carlo --------

def monte_carlo_pnl(
    trade_pnls: List[float], iterations: int = 1000, seed: int | None = None,
) -> Dict[str, Any]:
    if not trade_pnls:
        return {"iterations": 0, "final_pnl": {}, "max_drawdown_pct": {}}
    rng = random.Random(seed)
    finals: List[float] = []
    max_dds: List[float] = []
    n = len(trade_pnls)
    for _ in range(iterations):
        sample = [trade_pnls[rng.randrange(n)] for _ in range(n)]
        eq = 0.0
        peak = 0.0
        dd = 0.0
        for p in sample:
            eq += p
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
        finals.append(eq)
        max_dds.append(abs(dd))
    finals.sort()
    max_dds.sort()

    def _pct(a: List[float], q: float) -> float:
        return a[min(len(a) - 1, max(0, int(q * len(a))))]

    return {
        "iterations": iterations,
        "final_pnl": {"p5": _pct(finals, 0.05), "p50": _pct(finals, 0.5), "p95": _pct(finals, 0.95)},
        "max_drawdown_pct": {"p5": _pct(max_dds, 0.05), "p50": _pct(max_dds, 0.5), "p95": _pct(max_dds, 0.95)},
    }


# -------- cost model wrapper --------

def apply_trading_costs(
    trades: List[Dict[str, Any]],
    commission_bps: float = 2.0,
    slippage_bps: float = 1.0,
    spread_bps: float = 1.5,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in trades:
        t2 = dict(t)
        gross = float(t2.get("gross_pnl", t2.get("pnl", 0)))
        t2 = apply_commission(t2, commission_bps)
        t2 = apply_slippage(t2, slippage_bps)
        t2 = apply_spread(t2, spread_bps)
        t2["pnl"] = round(
            gross - t2.get("commission_cost", 0)
            - t2.get("slippage_cost", 0)
            - t2.get("spread_cost", 0),
            6,
        )
        out.append(t2)
    return out


# -------- portfolio & multi-strategy --------

def portfolio_backtest(
    strategies: List[Callable[[], List[Dict[str, Any]]]],
    weights: List[float] | None = None,
) -> Dict[str, Any]:
    if not strategies:
        return {"trades": [], "total_pnl": 0.0}
    weights = weights or [1.0 / len(strategies)] * len(strategies)
    all_trades: List[Dict[str, Any]] = []
    for w, run in zip(weights, strategies):
        for t in run():
            t2 = dict(t)
            t2["pnl"] = float(t2.get("pnl", 0)) * w
            all_trades.append(t2)
    all_trades.sort(key=lambda t: t.get("closed_at") or t.get("id") or "")
    return {"trades": all_trades, "total_pnl": round(sum(t["pnl"] for t in all_trades), 6)}
