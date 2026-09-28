"""Liquidity classifier based on average volume."""
from __future__ import annotations
from statistics import mean
from typing import Any, Dict, List


def classify(bars: List[Dict[str, Any]]) -> str:
    vols = [float(b.get("volume") or 0) for b in bars[-30:]]
    if not vols:
        return "normal"
    avg = mean(vols)
    if avg < 1000:
        return "thin"
    if avg < 100_000:
        return "normal"
    return "deep"
