"""TradeLogger — one PaperTrade row per simulated fill."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.engine import OrderSide, PaperTrade

logger = get_logger(__name__)


class TradeLogger:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self,
        *,
        user_id: str,
        engine_session_id: str,
        paper_order_id: str,
        symbol: str,
        exchange: str,
        side: OrderSide,
        quantity: float,
        price: float,
        realized_pnl_delta: float,
        strategy_name: str,
        executed_at: datetime | None = None,
    ) -> PaperTrade:
        trade = PaperTrade(
            user_id=user_id,
            engine_session_id=engine_session_id,
            paper_order_id=paper_order_id,
            symbol=symbol,
            exchange=exchange,
            side=side,
            quantity=quantity,
            price=price,
            realized_pnl_delta=realized_pnl_delta,
            strategy_name=strategy_name,
            executed_at=executed_at or datetime.now(timezone.utc),
        )
        self.session.add(trade)
        await self.session.flush()
        logger.info(
            "paper_trade_logged",
            extra={
                "symbol": symbol,
                "side": side.value,
                "qty": quantity,
                "price": price,
                "pnl_delta": realized_pnl_delta,
                "strategy": strategy_name,
                "engine_session_id": engine_session_id,
            },
        )
        return trade

    async def list_trades(
        self,
        engine_session_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[PaperTrade]:
        stmt = (
            select(PaperTrade)
            .where(PaperTrade.engine_session_id == engine_session_id)
            .order_by(PaperTrade.executed_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
