"""Report data layer — provides the SAME data model to PDF, CSV, XLSX, etc.

Extended paginated iterators avoid loading everything into memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest import BacktestRun
from app.models.engine import EngineSession, PaperTrade
from app.models.trade import Trade
from app.services.analytics_service import AnalyticsService
from app.services.portfolio_service import PortfolioService
from app.services.risk_service import RiskService


@dataclass
class ReportContext:
    """Bundle of everything a template needs."""

    user_id: str
    user_email: str
    report_type: str
    generated_at: datetime
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    portfolio_summary: dict[str, Any] = field(default_factory=dict)
    analytics_summary: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    holdings: list[dict[str, Any]] = field(default_factory=list)
    monthly_perf: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    backtest: Optional[dict[str, Any]] = None
    extra: dict[str, Any] = field(default_factory=dict)


class ReportDataLayer:
    """Pulls all data required for reports."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)
        self.analytics = AnalyticsService(session)
        self.risk = RiskService(session)

    async def iter_trades(
        self,
        user_id: str,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        batch: int = 500,
    ) -> AsyncIterator[list[PaperTrade]]:
        offset = 0
        while True:
            stmt = select(PaperTrade).where(PaperTrade.user_id == user_id)
            if start:
                stmt = stmt.where(PaperTrade.executed_at >= start)
            if end:
                stmt = stmt.where(PaperTrade.executed_at <= end)
            stmt = stmt.order_by(PaperTrade.executed_at.asc()).offset(offset).limit(batch)
            rows = list((await self.session.execute(stmt)).scalars().all())
            if not rows:
                return
            yield rows
            if len(rows) < batch:
                return
            offset += batch

    async def build_context(
        self,
        *,
        user_id: str,
        user_email: str,
        report_type: str,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        backtest_id: Optional[str] = None,
    ) -> ReportContext:
        ctx = ReportContext(
            user_id=user_id,
            user_email=user_email,
            report_type=report_type,
            generated_at=datetime.now(timezone.utc),
            period_start=period_start,
            period_end=period_end,
        )

        ctx.portfolio_summary = await self.portfolio.summary(user_id)
        ctx.holdings = await self.portfolio.holdings(user_id)
        summary = await self.analytics.summary(user_id)
        # Serialize dataclass
        from dataclasses import asdict
        ctx.analytics_summary = asdict(summary)
        ctx.monthly_perf = await self.analytics.monthly_performance(user_id)
        ctx.equity_curve = await self.analytics.equity_curve(user_id)
        ctx.risk = await self.risk.dashboard(user_id)

        # Populate trades (bounded for report memory)
        trades: list[dict[str, Any]] = []
        max_rows = 5000
        async for batch in self.iter_trades(user_id, start=period_start, end=period_end):
            for t in batch:
                trades.append({
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                    "quantity": float(t.quantity),
                    "price": float(t.price),
                    "pnl": float(t.realized_pnl_delta or 0),
                    "strategy": t.strategy_name,
                    "executed_at": t.executed_at.isoformat() if t.executed_at else None,
                })
                if len(trades) >= max_rows:
                    break
            if len(trades) >= max_rows:
                break
        ctx.trades = trades

        if backtest_id:
            bt = await self.session.get(BacktestRun, backtest_id)
            if bt is not None and bt.user_id == user_id:
                ctx.backtest = {
                    "id": bt.id,
                    "strategy_name": bt.strategy_name,
                    "symbols": list(bt.symbols or []),
                    "start_date": bt.start_date.isoformat(),
                    "end_date": bt.end_date.isoformat(),
                    "initial_capital": float(bt.initial_capital),
                    "status": bt.status.value if hasattr(bt.status, "value") else str(bt.status),
                    "summary": dict(bt.summary or {}),
                    "trade_count": len(bt.trades or []),
                }

        return ctx

    @staticmethod
    def period_for(report_type: str, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
        now = now or datetime.now(timezone.utc)
        if report_type == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, now
        if report_type == "weekly":
            return now - timedelta(days=7), now
        if report_type == "monthly":
            return now - timedelta(days=30), now
        return now - timedelta(days=90), now
