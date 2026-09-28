"""Paper executor — wraps the pure ``PaperExecutor`` behind the ``Executor`` interface."""
from __future__ import annotations

from typing import Optional

from app.engine.market_data.base import Quote
from app.engine.orders.executor import Fill, PaperExecutor
from app.engine.orders.executor_interface import Executor
from app.models.engine import PaperOrder


class PaperExecutorAdapter(Executor):
    execution_mode = "paper"

    def __init__(self, executor: PaperExecutor | None = None) -> None:
        self._executor = executor or PaperExecutor()

    async def on_place(self, order: PaperOrder) -> None:
        # Paper mode: nothing to do — fills happen in try_fill_from_quote.
        return None

    async def on_modify(self, order: PaperOrder, changes: dict) -> None:
        return None

    async def on_cancel(self, order: PaperOrder) -> None:
        return None

    def try_fill_from_quote(self, order: PaperOrder, quote: Quote) -> Optional[Fill]:
        return self._executor.try_fill(order, quote)
