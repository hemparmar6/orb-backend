"""Trend strength score (0-100)."""
from __future__ import annotations
from typing import Any, Dict, List


def score(bars: List[Dict[str, Any]]) -> float:
    if len(bars) < 14:
        return 0.0
    closes = [float(b["close"]) for b in bars[-30:]]
    ups = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    downs = len(closes) - 1 - ups
    directional = abs(ups - downs) / (len(closes) - 1)
    slope = abs(closes[-1] - closes[0]) / (closes[0] or 1e-9)
    raw = 100 * (0.6 * directional + 0.4 * min(1.0, slope * 5))
    return round(max(0.0, min(100.0, raw)), 2)
