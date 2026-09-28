"""Volatility regime classifier."""
from __future__ import annotations
from statistics import pstdev
from typing import Any, Dict, List


def regime(bars: List[Dict[str, Any]]) -> str:
    if len(bars) < 20:
        return "normal"
    closes = [float(b["close"]) for b in bars[-30:]]
    rets = [(closes[i] - closes[i - 1]) / (closes[i - 1] or 1e-9) for i in range(1, len(closes))]
    sd = pstdev(rets) if len(rets) > 1 else 0.0
    if sd < 0.005:
        return "low"
    if sd < 0.02:
        return "normal"
    if sd < 0.04:
        return "high"
    return "extreme"
