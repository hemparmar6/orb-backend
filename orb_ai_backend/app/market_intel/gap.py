"""Gap behaviour detector."""
from __future__ import annotations
from typing import Any, Dict, List, Optional


def behaviour(bars: List[Dict[str, Any]]) -> Optional[str]:
    if len(bars) < 2:
        return None
    prev, cur = bars[-2], bars[-1]
    prev_close = float(prev["close"])
    open_ = float(cur["open"])
    close = float(cur["close"])
    gap_pct = (open_ - prev_close) / (prev_close or 1e-9)
    if abs(gap_pct) < 0.002:
        return "none"
    if (gap_pct > 0 and close <= prev_close) or (gap_pct < 0 and close >= prev_close):
        return "filled"
    return "gap_up" if gap_pct > 0 else "gap_down"
