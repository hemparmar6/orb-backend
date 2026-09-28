"""Shared ORB (Opening Range Breakout) parameter schema + helpers.

The same ``OrbParams`` are used by both:
- ``OrbStrategy`` (live/paper via the engine + ``StrategyContext``)
- ``BacktestEngine`` (offline candle replay)

Design goal: strategy stays broker-independent. It only talks to
``StrategyContext`` (for orders) and its own ``params``. Nothing in this
module imports from any broker adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any, Optional


def _parse_hhmm(value: str | time | None, default: time) -> time:
    if value is None:
        return default
    if isinstance(value, time):
        return value
    parts = str(value).split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


@dataclass(slots=True)
class OrbParams:
    """Everything the ORB strategy / backtester needs.

    Any missing value falls back to a sensible default so ``OrbParams.from_dict({})``
    works for smoke tests.
    """

    # ---- Universe -------------------------------------------------------
    symbols: list[str] = field(default_factory=lambda: ["NIFTY"])
    exchange: str = "NSE"

    # ---- Opening range -------------------------------------------------
    opening_range_minutes: int = 15

    # ---- Session --------------------------------------------------------
    session_start: time = time(9, 15)   # HH:MM in the exchange's local tz
    session_end: time = time(15, 15)    # ORB square-off — 15 min before close
    timezone: str = "Asia/Kolkata"

    # ---- Direction ------------------------------------------------------
    enable_long: bool = True
    enable_short: bool = True

    # ---- Exit rules -----------------------------------------------------
    stop_loss_pct: float = 0.5          # % from entry
    target_pct: float = 1.5             # % from entry
    trailing_stop_pct: Optional[float] = None  # None disables trailing

    # ---- Re-entry / caps ------------------------------------------------
    re_entry_enabled: bool = False
    max_re_entries_per_day: int = 0     # only used when re_entry_enabled
    max_trades_per_day: int = 4
    daily_loss_limit: float = 5000.0    # absolute currency
    risk_per_trade_pct: float = 1.0     # % of capital risked per trade

    # ---- Sizing ---------------------------------------------------------
    quantity: float = 1.0               # fallback lot size when risk sizing off
    use_risk_based_sizing: bool = False # if True, qty = risk_per_trade / SL_dist

    # ---- Fees / slippage (backtest only) --------------------------------
    fee_per_trade: float = 0.0          # flat fee per fill
    slippage_pct: float = 0.0           # % slippage on entry & exit

    # ---- Parser ---------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "OrbParams":
        d = d or {}

        def _f(key: str, default: Any) -> Any:
            v = d.get(key)
            return default if v is None else v

        obj = cls()
        obj.symbols = list(_f("symbols", obj.symbols))
        obj.exchange = str(_f("exchange", obj.exchange))
        obj.opening_range_minutes = int(_f("opening_range_minutes", obj.opening_range_minutes))
        obj.session_start = _parse_hhmm(d.get("session_start"), obj.session_start)
        obj.session_end = _parse_hhmm(d.get("session_end"), obj.session_end)
        obj.timezone = str(_f("timezone", obj.timezone))
        obj.enable_long = bool(_f("enable_long", obj.enable_long))
        obj.enable_short = bool(_f("enable_short", obj.enable_short))
        obj.stop_loss_pct = float(_f("stop_loss_pct", obj.stop_loss_pct))
        obj.target_pct = float(_f("target_pct", obj.target_pct))
        ts = d.get("trailing_stop_pct")
        obj.trailing_stop_pct = float(ts) if ts is not None else None
        obj.re_entry_enabled = bool(_f("re_entry_enabled", obj.re_entry_enabled))
        obj.max_re_entries_per_day = int(_f("max_re_entries_per_day", obj.max_re_entries_per_day))
        obj.max_trades_per_day = int(_f("max_trades_per_day", obj.max_trades_per_day))
        obj.daily_loss_limit = float(_f("daily_loss_limit", obj.daily_loss_limit))
        obj.risk_per_trade_pct = float(_f("risk_per_trade_pct", obj.risk_per_trade_pct))
        obj.quantity = float(_f("quantity", obj.quantity))
        obj.use_risk_based_sizing = bool(_f("use_risk_based_sizing", obj.use_risk_based_sizing))
        obj.fee_per_trade = float(_f("fee_per_trade", obj.fee_per_trade))
        obj.slippage_pct = float(_f("slippage_pct", obj.slippage_pct))
        return obj

    def size_for(self, entry_price: float, sl_distance: float, capital: float) -> float:
        """Compute a position size.

        Falls back to ``quantity`` if risk-based sizing is disabled or the SL
        distance is zero.
        """
        if not self.use_risk_based_sizing or sl_distance <= 0 or capital <= 0:
            return self.quantity
        risk_amount = capital * (self.risk_per_trade_pct / 100.0)
        qty = risk_amount / sl_distance
        # Round DOWN to keep risk within bounds; guarantee at least 1 unit if
        # the calculated size is fractional but positive.
        floored = int(qty)
        return float(max(1, floored))
