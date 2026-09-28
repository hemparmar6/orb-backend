"""Market regime detector."""
from __future__ import annotations
from statistics import pstdev
from typing import Any, Dict, List


def detect(bars: List[Dict[str, Any]]) -> str:
    if len(bars) < 20:
        return "quiet"
    closes = [float(b["close"]) for b in bars[-50:]]
    rets = [(closes[i] - closes[i - 1]) / (closes[i - 1] or 1e-9) for i in range(1, len(closes))]
    vol = pstdev(rets) if len(rets) > 1 else 0.0
    slope = (closes[-1] - closes[0]) / (closes[0] or 1e-9)
    if vol > 0.03:
        return "volatile"
    if abs(slope) > 0.05:
        return "trending"
    if vol < 0.005:
        return "quiet"
    return "ranging"
