"""Pre-order risk checks.

The engine calls ``RiskEngine.check(order_intent, session_snapshot)`` before
every order. Each check returns a ``RiskDecision``. First failure short-circuits.

Rules covered:
- max_position_size (absolute qty per symbol, both sides combined)
- max_daily_loss (session.day_pnl >= -limit rejects new entries; exits still allowed)
- max_risk_per_trade (percent of current capital allocated by notional value)
- trading_session_valid (within configured HH:MM window in TRADING_SESSION_TIMEZONE)
- position_sizing hook (a Callable, defaults to "use requested quantity as-is")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---- DTOs -----------------------------------------------------------------


@dataclass(slots=True)
class RiskConfig:
    """Risk parameters — persisted on ``EngineSession.risk_config`` (JSON)."""

    max_position_size: Optional[float] = None       # absolute qty per symbol
    max_daily_loss: Optional[float] = None          # positive number, e.g. 5000
    max_risk_per_trade_pct: Optional[float] = None  # % of capital, e.g. 1.5 = 1.5%
    trading_session_start: Optional[str] = None     # "HH:MM"
    trading_session_end: Optional[str] = None       # "HH:MM"
    trading_session_timezone: Optional[str] = None  # e.g. "Asia/Kolkata"

    @classmethod
    def from_dict(cls, data: dict) -> "RiskConfig":
        return cls(
            max_position_size=_as_float(data.get("max_position_size")),
            max_daily_loss=_as_float(data.get("max_daily_loss")),
            max_risk_per_trade_pct=_as_float(data.get("max_risk_per_trade_pct")),
            trading_session_start=data.get("trading_session_start"),
            trading_session_end=data.get("trading_session_end"),
            trading_session_timezone=data.get("trading_session_timezone"),
        )


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: Optional[str] = None
    adjusted_quantity: Optional[float] = None


@dataclass(slots=True)
class OrderIntent:
    """Everything the risk engine needs to evaluate an order."""

    symbol: str
    side: str          # "buy" | "sell"
    quantity: float
    price: float       # limit price OR expected market fill price
    is_exit: bool = False  # exits (closing / reducing) are always allowed by daily-loss gate


@dataclass(slots=True)
class SessionSnapshot:
    """Live session state passed to the risk engine."""

    initial_capital: float
    current_capital: float
    day_pnl: float
    positions: dict[str, float] = field(default_factory=dict)  # symbol -> net qty


# ---- Engine ---------------------------------------------------------------


PositionSizer = Callable[[OrderIntent, SessionSnapshot, RiskConfig], float]


def default_position_sizer(
    intent: OrderIntent, snap: SessionSnapshot, cfg: RiskConfig
) -> float:
    """Return quantity as-requested."""
    return intent.quantity


class RiskEngine:
    def __init__(
        self,
        config: RiskConfig,
        *,
        position_sizer: PositionSizer = default_position_sizer,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.position_sizer = position_sizer
        self._now_fn = now_fn or (lambda: datetime.now(_tz(config.trading_session_timezone or settings.TRADING_SESSION_TIMEZONE)))

    def check(self, intent: OrderIntent, snap: SessionSnapshot) -> RiskDecision:
        # 1. Trading window
        if not self._within_trading_window():
            return RiskDecision(allowed=False, reason="Outside trading session window")

        # 2. Daily loss gate — only blocks NEW entries, not exits
        if (
            self.config.max_daily_loss is not None
            and not intent.is_exit
            and snap.day_pnl <= -abs(self.config.max_daily_loss)
        ):
            return RiskDecision(
                allowed=False,
                reason=f"Max daily loss reached ({snap.day_pnl:.2f})",
            )

        # 3. Position sizing hook may reduce qty
        sized_qty = float(self.position_sizer(intent, snap, self.config))
        if sized_qty <= 0:
            return RiskDecision(
                allowed=False, reason="Position sizer returned zero/negative quantity"
            )

        # 4. Max position size per symbol
        if self.config.max_position_size is not None:
            existing = abs(snap.positions.get(intent.symbol, 0.0))
            projected = existing + sized_qty
            if projected > self.config.max_position_size:
                return RiskDecision(
                    allowed=False,
                    reason=(
                        f"Position size {projected} exceeds max "
                        f"{self.config.max_position_size} for {intent.symbol}"
                    ),
                )

        # 5. Max risk per trade — notional as fraction of current capital
        if (
            self.config.max_risk_per_trade_pct is not None
            and snap.current_capital > 0
            and intent.price > 0
        ):
            notional = sized_qty * intent.price
            pct = (notional / snap.current_capital) * 100.0
            if pct > self.config.max_risk_per_trade_pct:
                return RiskDecision(
                    allowed=False,
                    reason=(
                        f"Trade risk {pct:.2f}% exceeds max {self.config.max_risk_per_trade_pct}% "
                        f"of capital (notional {notional:.2f}, capital {snap.current_capital:.2f})"
                    ),
                )

        return RiskDecision(
            allowed=True,
            adjusted_quantity=sized_qty if sized_qty != intent.quantity else None,
        )

    # ---- internals --------------------------------------------------------

    def _within_trading_window(self) -> bool:
        start_s = self.config.trading_session_start or settings.TRADING_SESSION_START
        end_s = self.config.trading_session_end or settings.TRADING_SESSION_END
        if not start_s or not end_s:
            return True
        try:
            start_t = _parse_hhmm(start_s)
            end_t = _parse_hhmm(end_s)
        except ValueError:
            logger.warning("bad_trading_window", extra={"start": start_s, "end": end_s})
            return True
        now = self._now_fn().time()
        if start_t <= end_t:
            return start_t <= now <= end_t
        # wraps midnight
        return now >= start_t or now <= end_t


# ---- helpers --------------------------------------------------------------


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":", 1)
    return time(hour=int(hh), minute=int(mm))


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _as_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)
