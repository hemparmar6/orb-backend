"""PortfolioManager.

Reads / mutates ``PaperPosition`` rows. Applies fills to positions,
computes realized/unrealized P&L, and produces day statistics.

Semantics (net-quantity model):

- BUY of ``q`` at ``p``:
    * if net_qty >= 0 (long or flat): weighted avg on the LONG side
        new_avg = (old_avg*old_qty + p*q) / (old_qty + q)
    * if net_qty < 0 (short) and abs(net_qty) >= q: reducing short
        realized += (avg - p) * q   (short profit if avg > p)
    * if net_qty < 0 and abs(net_qty) < q: closes short, opens long remainder
- SELL is the symmetric case.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engine import (
    OrderProduct,
    OrderSide,
    PaperPosition,
    PaperTrade,
)


@dataclass(slots=True)
class PnLSnapshot:
    realized: float
    unrealized: float
    day_pnl: float
    total: float
    open_positions: int
    winning_trades: int
    losing_trades: int


class PortfolioManager:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- reads -----------------------------------------------------------

    async def list_positions(
        self, engine_session_id: str, *, only_open: bool = False
    ) -> Sequence[PaperPosition]:
        stmt = select(PaperPosition).where(
            PaperPosition.engine_session_id == engine_session_id
        )
        if only_open:
            stmt = stmt.where(PaperPosition.net_quantity != 0)
        stmt = stmt.order_by(PaperPosition.updated_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_position(
        self,
        engine_session_id: str,
        symbol: str,
        exchange: str = "MOCK",
        product: OrderProduct = OrderProduct.MIS,
    ) -> PaperPosition | None:
        stmt = select(PaperPosition).where(
            PaperPosition.engine_session_id == engine_session_id,
            PaperPosition.symbol == symbol,
            PaperPosition.exchange == exchange,
            PaperPosition.product == product,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def positions_snapshot(self, engine_session_id: str) -> dict[str, float]:
        """{ symbol: net_quantity } for the RiskEngine."""
        positions = await self.list_positions(engine_session_id, only_open=True)
        return {p.symbol: float(p.net_quantity) for p in positions}

    async def pnl_snapshot(
        self,
        engine_session_id: str,
        *,
        last_prices: dict[str, float] | None = None,
    ) -> PnLSnapshot:
        positions = await self.list_positions(engine_session_id)
        realized = sum((float(p.realized_pnl) for p in positions), 0.0)
        unrealized = 0.0
        open_count = 0
        for p in positions:
            qty = float(p.net_quantity)
            if qty == 0:
                continue
            open_count += 1
            ltp = None
            if last_prices and p.symbol in last_prices:
                ltp = last_prices[p.symbol]
            elif p.last_price is not None:
                ltp = float(p.last_price)
            if ltp is not None:
                unrealized += (ltp - float(p.average_price)) * qty

        # Winners / losers derived from paper_trades.realized_pnl_delta.
        wl_stmt = select(
            func.count().filter(PaperTrade.realized_pnl_delta > 0),
            func.count().filter(PaperTrade.realized_pnl_delta < 0),
        ).where(PaperTrade.engine_session_id == engine_session_id)
        winners, losers = (await self.session.execute(wl_stmt)).one()

        return PnLSnapshot(
            realized=round(realized, 4),
            unrealized=round(unrealized, 4),
            day_pnl=round(realized + unrealized, 4),  # single-day paper engine — same as total
            total=round(realized + unrealized, 4),
            open_positions=open_count,
            winning_trades=int(winners or 0),
            losing_trades=int(losers or 0),
        )

    # ---- mutations -------------------------------------------------------

    async def apply_fill(
        self,
        *,
        user_id: str,
        engine_session_id: str,
        symbol: str,
        exchange: str,
        product: OrderProduct,
        side: OrderSide,
        quantity: float,
        price: float,
    ) -> tuple[PaperPosition, float]:
        """Apply a fill and return (updated_position, realized_pnl_delta)."""
        assert quantity > 0, "quantity must be positive"
        pos = await self.get_position(engine_session_id, symbol, exchange, product)
        now = datetime.now(timezone.utc)

        if pos is None:
            pos = PaperPosition(
                user_id=user_id,
                engine_session_id=engine_session_id,
                symbol=symbol,
                exchange=exchange,
                product=product,
                net_quantity=0,
                average_price=0,
                realized_pnl=0,
                opened_at=now,
            )
            self.session.add(pos)
            # flush so the row is real before we mutate it
            await self.session.flush()

        old_qty = float(pos.net_quantity)
        old_avg = float(pos.average_price)
        signed_delta = quantity if side == OrderSide.BUY else -quantity
        realized_delta = 0.0

        if old_qty == 0:
            new_qty = signed_delta
            new_avg = price
            pos.opened_at = now
        elif (old_qty > 0 and signed_delta > 0) or (old_qty < 0 and signed_delta < 0):
            # Same side — weighted average.
            total_abs = abs(old_qty) + abs(signed_delta)
            new_avg = (abs(old_qty) * old_avg + abs(signed_delta) * price) / total_abs
            new_qty = old_qty + signed_delta
        else:
            # Opposite side — reducing or flipping.
            if abs(signed_delta) <= abs(old_qty):
                # Pure reduce.
                closed = abs(signed_delta)
                if old_qty > 0:
                    realized_delta = (price - old_avg) * closed
                else:
                    realized_delta = (old_avg - price) * closed
                new_qty = old_qty + signed_delta
                new_avg = old_avg if new_qty != 0 else 0.0
                if new_qty == 0:
                    pos.closed_at = now
            else:
                # Flip through zero — close old completely, open remainder at price.
                closed = abs(old_qty)
                if old_qty > 0:
                    realized_delta = (price - old_avg) * closed
                else:
                    realized_delta = (old_avg - price) * closed
                remainder = abs(signed_delta) - closed
                new_qty = remainder if signed_delta > 0 else -remainder
                new_avg = price
                pos.opened_at = now
                pos.closed_at = None

        pos.net_quantity = _round(new_qty)
        pos.average_price = _round(new_avg)
        pos.realized_pnl = _round(float(pos.realized_pnl) + realized_delta)
        pos.last_price = _round(price)
        if pos.net_quantity == 0 and pos.closed_at is None:
            pos.closed_at = now
        await self.session.flush()
        return pos, realized_delta

    async def mark_prices(
        self,
        engine_session_id: str,
        prices: Iterable[tuple[str, float]],
    ) -> None:
        """Update ``last_price`` for a batch of (symbol, price)."""
        table = {s: p for s, p in prices}
        if not table:
            return
        positions = await self.list_positions(engine_session_id)
        for p in positions:
            if p.symbol in table:
                p.last_price = _round(table[p.symbol])
        await self.session.flush()


def _round(v: float) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.0001")))
