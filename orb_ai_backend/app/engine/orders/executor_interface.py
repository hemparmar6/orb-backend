"""Executor interface — the seam between the engine's OrderManager and either
the in-process paper simulator (Module 2) or a real broker (Module 3+).

The OrderManager holds ONE ``Executor``. It calls:

- ``on_place(order)``    — right after a new PaperOrder row is persisted
- ``on_modify(order, changes)``, ``on_cancel(order)`` — for lifecycle mutations
- ``try_fill_from_quote(order, quote)`` — every market tick (paper mode only)

Live executors ignore ``try_fill_from_quote`` (fills come from the broker's
WS/poll stream — see ``BrokerOrderStream``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.engine.market_data.base import Quote
from app.engine.orders.executor import Fill
from app.models.engine import PaperOrder


class Executor(ABC):
    """Common interface for paper (in-process) and live (broker-backed) execution."""

    execution_mode: str  # "paper" | "live"

    @abstractmethod
    async def on_place(self, order: PaperOrder) -> None:
        """Called after the OrderManager persists a new PaperOrder row.

        - Paper: no-op (fills are simulated in ``try_fill_from_quote``).
        - Live:  submit to broker, store broker_order_id + initial status.
        """

    @abstractmethod
    async def on_modify(self, order: PaperOrder, changes: dict) -> None: ...

    @abstractmethod
    async def on_cancel(self, order: PaperOrder) -> None: ...

    @abstractmethod
    def try_fill_from_quote(self, order: PaperOrder, quote: Quote) -> Optional[Fill]:
        """Return a Fill if this quote should fill the order, else None.

        Paper: delegates to ``PaperExecutor.try_fill``.
        Live:  ALWAYS returns None — real fills come from the broker.
        """
