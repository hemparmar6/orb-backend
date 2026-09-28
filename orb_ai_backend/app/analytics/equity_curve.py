"""Equity curve helper."""

from __future__ import annotations

from typing import Any, Dict, List


def equity_curve(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    curve: List[Dict[str, Any]] = []
    running = 0.0
    peak = 0.0
    for t in sorted(trades, key=lambda x: x.get("closed_at") or x.get("id") or ""):
        running += float(t.get("pnl") or 0.0)
        peak = max(peak, running)
        curve.append({
            "t": str(t.get("closed_at") or t.get("id")),
            "equity": round(running, 4),
            "drawdown": round(peak - running, 4),
        })
    return curve
