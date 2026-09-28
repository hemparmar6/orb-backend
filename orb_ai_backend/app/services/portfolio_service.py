"""Portfolio service (Module 8).

Aggregates the paper-engine state (EngineSession, PaperPosition, PaperTrade)
into portfolio-level summaries.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engine import (
    EngineSession,
    OrderSide,
    PaperPosition,
    PaperTrade,
)
from app.models.portfolio import PortfolioSnapshot


class PortfolioService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _user_sessions(self, user_id: str) -> list[EngineSession]:
        stmt = select(EngineSession).where(EngineSession.user_id == user_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def summary(self, user_id: str) -> dict[str, Any]:
        sessions = await self._user_sessions(user_id)
        initial_capital = sum(float(s.initial_capital) for s in sessions)

        # Positions
        pos_stmt = select(PaperPosition).where(PaperPosition.user_id == user_id)
        positions = list((await self.session.execute(pos_stmt)).scalars().all())
        realized = sum(float(p.realized_pnl) for p in positions)
        unrealized = 0.0
        exposure = 0.0
        open_positions = 0
        for p in positions:
            qty = float(p.net_quantity)
            if qty == 0:
                continue
            open_positions += 1
            ltp = float(p.last_price) if p.last_price is not None else float(p.average_price)
            unrealized += (ltp - float(p.average_price)) * qty
            exposure += abs(ltp * qty)

        total_pnl = realized + unrealized
        equity = initial_capital + total_pnl

        return {
            "initial_capital": round(initial_capital, 4),
            "equity": round(equity, 4),
            "realized_pnl": round(realized, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_pnl": round(total_pnl, 4),
            "exposure": round(exposure, 4),
            "open_positions": open_positions,
            "num_sessions": len(sessions),
        }

    async def holdings(self, user_id: str) -> list[dict[str, Any]]:
        pos_stmt = select(PaperPosition).where(
            PaperPosition.user_id == user_id, PaperPosition.net_quantity != 0
        )
        positions = (await self.session.execute(pos_stmt)).scalars().all()
        out: list[dict[str, Any]] = []
        for p in positions:
            qty = float(p.net_quantity)
            avg = float(p.average_price)
            ltp = float(p.last_price) if p.last_price is not None else avg
            unrealized = (ltp - avg) * qty
            out.append({
                "symbol": p.symbol,
                "exchange": p.exchange,
                "product": p.product.value if hasattr(p.product, "value") else str(p.product),
                "quantity": qty,
                "average_price": avg,
                "last_price": ltp,
                "market_value": abs(ltp * qty),
                "unrealized_pnl": round(unrealized, 4),
                "realized_pnl": float(p.realized_pnl),
                "side": "long" if qty > 0 else "short",
            })
        return out

    async def allocation(self, user_id: str) -> list[dict[str, Any]]:
        holdings = await self.holdings(user_id)
        total_value = sum(h["market_value"] for h in holdings) or 1.0
        return [
            {
                "symbol": h["symbol"],
                "market_value": h["market_value"],
                "weight": round(h["market_value"] / total_value, 6),
            }
            for h in holdings
        ]

    async def daily_performance(
        self, user_id: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """Aggregate PaperTrade.realized_pnl_delta by execution date."""
        start = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                func.date(PaperTrade.executed_at).label("d"),
                func.sum(PaperTrade.realized_pnl_delta).label("pnl"),
                func.count().label("n"),
            )
            .where(PaperTrade.user_id == user_id, PaperTrade.executed_at >= start)
            .group_by(func.date(PaperTrade.executed_at))
            .order_by(func.date(PaperTrade.executed_at))
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "date": str(r.d),
                "pnl": round(float(r.pnl or 0), 4),
                "trades": int(r.n or 0),
            }
            for r in rows
        ]

    async def monthly_performance(
        self, user_id: str, months: int = 12
    ) -> list[dict[str, Any]]:
        stmt = select(PaperTrade).where(PaperTrade.user_id == user_id)
        trades = (await self.session.execute(stmt)).scalars().all()
        # Aggregate in Python for DB portability (sqlite lacks strftime cross-format nicely).
        buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
        for t in trades:
            key = t.executed_at.strftime("%Y-%m")
            buckets[key]["pnl"] += float(t.realized_pnl_delta or 0)
            buckets[key]["trades"] += 1
        items = sorted(buckets.items())[-months:]
        return [
            {"month": k, "pnl": round(v["pnl"], 4), "trades": int(v["trades"])}
            for k, v in items
        ]

    # ---- Snapshot creation (background job) ----
    async def create_daily_snapshot(self, user_id: str) -> PortfolioSnapshot:
        today = date.today()
        summary = await self.summary(user_id)
        holdings = await self.holdings(user_id)
        # Upsert-like: fetch existing
        stmt = select(PortfolioSnapshot).where(
            PortfolioSnapshot.user_id == user_id,
            PortfolioSnapshot.snapshot_date == today,
        )
        snap = (await self.session.execute(stmt)).scalar_one_or_none()
        if snap is None:
            snap = PortfolioSnapshot(user_id=user_id, snapshot_date=today)
            self.session.add(snap)
        snap.equity = summary["equity"]
        snap.realized_pnl = summary["realized_pnl"]
        snap.unrealized_pnl = summary["unrealized_pnl"]
        snap.day_pnl = summary["total_pnl"]
        snap.exposure = summary["exposure"]
        snap.open_positions = summary["open_positions"]
        snap.holdings = holdings
        await self.session.flush()
        return snap
