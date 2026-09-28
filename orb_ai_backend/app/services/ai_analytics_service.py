"""Module 9 — AI Analytics service.

Bridges the existing ``PaperTrade`` data (Module 2) into the trade-dict
shape used by the AI subsystem, then delegates to pure functions in
:mod:`app.analytics`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.equity_curve import equity_curve
from app.analytics.metrics import compute_portfolio_metrics
from app.models import AIAnalyticsSnapshot, PaperTrade, Trade, TradeStatus


class AIAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- data loaders (adapt PaperTrade + Trade into a common dict) ----

    async def load_trades(self, user_id: str) -> List[Dict[str, Any]]:
        """Load engine trades (realized PnL only) for AI analytics."""
        stmt = (
            select(PaperTrade)
            .where(PaperTrade.user_id == user_id)
            .order_by(PaperTrade.executed_at.asc())
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        out: List[Dict[str, Any]] = []
        for r in rows:
            pnl = float(r.realized_pnl_delta or 0)
            if pnl == 0:
                continue  # skip open-only fills
            out.append({
                "id": r.id,
                "pnl": pnl,
                "entry_price": float(getattr(r, "price", 0) or 0),
                "exit_price": float(getattr(r, "price", 0) or 0),
                "quantity": float(getattr(r, "quantity", 0) or 0),
                "side": str(getattr(r, "side", "buy")),
                "symbol": getattr(r, "symbol", None),
                "closed_at": getattr(r, "executed_at", None),
            })
        return out

    async def load_manual_trades(self, user_id: str) -> List[Dict[str, Any]]:
        """Load Module-1 closed manual trades. Used for trade review only."""
        stmt = (
            select(Trade)
            .where(Trade.user_id == user_id, Trade.status == TradeStatus.CLOSED)
            .order_by(Trade.closed_at.desc().nulls_last(), Trade.created_at.desc())
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [self._trade_to_dict(t) for t in rows]

    @staticmethod
    def _trade_to_dict(t: Trade) -> Dict[str, Any]:
        return {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side.value if hasattr(t.side, "value") else str(t.side),
            "quantity": float(t.quantity or 0),
            "entry_price": float(t.entry_price or 0) if t.entry_price is not None else None,
            "exit_price": float(t.exit_price or 0) if t.exit_price is not None else None,
            "stop_loss": float(t.stop_loss) if t.stop_loss is not None else None,
            "take_profit": float(t.take_profit) if t.take_profit is not None else None,
            "pnl": float(t.pnl or 0) if t.pnl is not None else None,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
        }

    # ---- snapshots ----

    async def snapshot_portfolio(self, user_id: str) -> AIAnalyticsSnapshot:
        trades = await self.load_trades(user_id)
        metrics = compute_portfolio_metrics(trades)
        curve = equity_curve(trades)
        snap = AIAnalyticsSnapshot(
            user_id=user_id,
            scope="portfolio",
            scope_ref_id=None,
            metrics=metrics,
            equity_curve=curve,
            generated_at=datetime.now(timezone.utc),
        )
        self.session.add(snap)
        await self.session.commit()
        await self.session.refresh(snap)
        return snap
