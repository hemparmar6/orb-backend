"""Session statistics summariser."""
from __future__ import annotations
from statistics import mean
from typing import Any, Dict, List


def summarise(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not bars:
        return {}
    last = bars[-1]
    ranges = [float(b["high"]) - float(b["low"]) for b in bars[-20:]]
    return {
        "last_open": float(last["open"]),
        "last_high": float(last["high"]),
        "last_low": float(last["low"]),
        "last_close": float(last["close"]),
        "avg_range_20": round(mean(ranges), 6) if ranges else 0.0,
    }
