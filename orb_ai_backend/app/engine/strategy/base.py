"""BaseStrategy + StrategyContext.

A strategy is a pluggable class registered with ``@register_strategy("name")``.
It receives a ``StrategyContext`` at construction time. The context exposes
paper trading actions (``place_order``, ``cancel_order``) plus reads
(``get_positions``, ``get_last_price``) so the strategy remains fully
decoupled from persistence and market-data providers.

Lifecycle callbacks (all async, all optional except ``on_tick``):

- ``on_start()``      — one-time init, called after engine session goes RUNNING
- ``on_tick(quote)``  — REQUIRED — every market tick for a subscribed symbol
- ``on_bar(candle)``  — optional — if the engine synthesises bars (not in M2)
- ``on_order_update(order)`` — optional — every order state transition
- ``on_stop()``       — cleanup before session ends
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from app.engine.market_data.base import Candle, Quote
from app.engine.orders.manager import OrderIntentRequest
from app.models.engine import OrderProduct, OrderSide, OrderType, PaperOrder


# ---- Context provided to strategies --------------------------------------


@dataclass(slots=True)
class StrategyContext:
    """Everything a strategy is allowed to touch. Read-only from the strategy's POV
    except through the ``place_order`` / ``cancel_order`` / ``modify_order`` calls.
    """

    engine_session_id: str
    user_id: str
    strategy_name: str
    params: dict[str, Any]
    symbols: list[str]

    # Callables injected by the runner.
    place_order: Callable[[OrderIntentRequest], Awaitable[PaperOrder]]
    modify_order: Callable[..., Awaitable[PaperOrder]]
    cancel_order: Callable[[str], Awaitable[PaperOrder]]
    get_positions: Callable[[], Awaitable[dict[str, float]]]  # { symbol: net_qty }
    get_last_price: Callable[[str], Awaitable[Optional[float]]]

    # A mutable dict the strategy uses to keep its own state across ticks.
    state: dict[str, Any]

    # ---- convenience helpers ---------------------------------------------

    async def buy_market(
        self,
        symbol: str,
        quantity: float,
        *,
        product: OrderProduct = OrderProduct.MIS,
        stop_loss: Optional[float] = None,
        target_price: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> PaperOrder:
        return await self.place_order(
            OrderIntentRequest(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                order_type=OrderType.MARKET,
                product=product,
                stop_loss=stop_loss,
                target_price=target_price,
                tag=tag,
                strategy_name=self.strategy_name,
            )
        )

    async def sell_market(
        self,
        symbol: str,
        quantity: float,
        *,
        product: OrderProduct = OrderProduct.MIS,
        stop_loss: Optional[float] = None,
        target_price: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> PaperOrder:
        return await self.place_order(
            OrderIntentRequest(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                order_type=OrderType.MARKET,
                product=product,
                stop_loss=stop_loss,
                target_price=target_price,
                tag=tag,
                strategy_name=self.strategy_name,
            )
        )


# ---- Base class ----------------------------------------------------------


class BaseStrategy(ABC):
    """Base class for all strategies. Subclass and register with
    ``@register_strategy("name")``.

    Subclasses SHOULD:
    - Declare ``name`` as a class variable (used in logs and to select at start).
    - Implement ``on_tick``.
    - Read parameters from ``self.ctx.params`` — never touch env vars.
    """

    name: str = "abstract"

    def __init__(self, ctx: StrategyContext) -> None:
        self.ctx = ctx

    # ---- lifecycle -------------------------------------------------------

    async def on_start(self) -> None:  # pragma: no cover - optional
        return None

    @abstractmethod
    async def on_tick(self, quote: Quote) -> None: ...

    async def on_bar(self, candle: Candle) -> None:  # pragma: no cover - optional
        return None

    async def on_order_update(self, order: PaperOrder) -> None:  # pragma: no cover - optional
        return None

    async def on_stop(self) -> None:  # pragma: no cover - optional
        return None
