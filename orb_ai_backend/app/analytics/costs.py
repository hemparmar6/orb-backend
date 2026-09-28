"""Cost-model helpers used by the advanced backtest service."""

from __future__ import annotations

from typing import Any, Dict


def apply_commission(trade: Dict[str, Any], bps: float) -> Dict[str, Any]:
    notional = abs(float(trade.get("quantity") or 0) * float(trade.get("entry_price") or 0))
    trade["commission_cost"] = round(notional * bps / 10_000.0, 6)
    return trade


def apply_slippage(trade: Dict[str, Any], bps: float) -> Dict[str, Any]:
    qty = float(trade.get("quantity") or 0)
    entry = float(trade.get("entry_price") or 0)
    exit_ = float(trade.get("exit_price") or 0)
    trade["slippage_cost"] = round((abs(qty * entry) + abs(qty * exit_)) * bps / 10_000.0, 6)
    return trade


def apply_spread(trade: Dict[str, Any], bps: float) -> Dict[str, Any]:
    mult = float(trade.get("spread_multiplier", 1.0))
    notional = abs(float(trade.get("quantity") or 0) * float(trade.get("entry_price") or 0))
    trade["spread_cost"] = round(notional * bps * mult / 10_000.0, 6)
    return trade
