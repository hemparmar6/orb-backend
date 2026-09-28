"""Trade analytics service (Module 8).

Metrics: win rate, avg R:R, profit factor, expectancy, drawdown,
equity curve, monthly performance, trade journal.

Reads PaperTrade + PaperPosition. All computations are pure Python
over query results to keep DB layer engine-agnostic.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engine import EngineSession, PaperTrade


@dataclass
class AnalyticsSummary:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    loss_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    recovery_factor: float
    expectancy: float
    average_win: float
    average_loss: float
    average_rr: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int
    max_drawdown: float
    max_drawdown_pct: float
    active_bots: int
    active_strategies: int


def _round(v: float, n: int = 4) -> float:
    return round(float(v), n)


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load_trades(self, user_id: str) -> list[PaperTrade]:
        stmt = (
            select(PaperTrade)
            .where(PaperTrade.user_id == user_id)
            .order_by(PaperTrade.executed_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def summary(self, user_id: str) -> AnalyticsSummary:
        trades = await self._load_trades(user_id)
        closed = [t for t in trades if float(t.realized_pnl_delta or 0) != 0]

        wins = [float(t.realized_pnl_delta) for t in closed if float(t.realized_pnl_delta) > 0]
        losses = [float(t.realized_pnl_delta) for t in closed if float(t.realized_pnl_delta) < 0]

        gross_profit = sum(wins)
        gross_loss = sum(losses)
        net_pnl = gross_profit + gross_loss
        total_trades = len(closed)
        winning = len(wins)
        losing = len(losses)
        win_rate = (winning / total_trades) if total_trades else 0.0
        loss_rate = (losing / total_trades) if total_trades else 0.0

        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        avg_rr = (avg_win / abs(avg_loss)) if avg_loss else 0.0
        profit_factor = (gross_profit / abs(gross_loss)) if gross_loss else (
            float("inf") if gross_profit > 0 else 0.0
        )
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        largest_win = max(wins) if wins else 0.0
        largest_loss = min(losses) if losses else 0.0

        # Consecutive wins / losses
        max_c_wins = 0
        max_c_losses = 0
        cur_c_wins = 0
        cur_c_losses = 0
        for t in closed:
            pnl = float(t.realized_pnl_delta or 0)
            if pnl > 0:
                cur_c_wins += 1
                cur_c_losses = 0
                max_c_wins = max(max_c_wins, cur_c_wins)
            elif pnl < 0:
                cur_c_losses += 1
                cur_c_wins = 0
                max_c_losses = max(max_c_losses, cur_c_losses)

        curve = self._equity_curve_from(closed)
        max_dd, max_dd_pct = _drawdown(curve)

        # Advanced ratios (Sharpe, Sortino, Calmar, Recovery Factor)
        import math
        returns = [float(t.realized_pnl_delta or 0) for t in closed]
        mean_ret = (sum(returns) / len(returns)) if returns else 0.0
        variance = (sum((r - mean_ret) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0.0
        std_dev = math.sqrt(variance) if variance > 0 else 1.0
        sharpe = (mean_ret / std_dev) * math.sqrt(252) if std_dev else 0.0

        negative_returns = [r for r in returns if r < 0]
        downside_var = (sum((r ** 2) for r in negative_returns) / len(negative_returns)) if negative_returns else 0.0
        downside_dev = math.sqrt(downside_var) if downside_var > 0 else 1.0
        sortino = (mean_ret / downside_dev) * math.sqrt(252) if downside_dev else 0.0

        ann_return = mean_ret * 252
        calmar = (ann_return / abs(max_dd_pct * 100)) if max_dd_pct != 0 else 0.0
        recovery_factor = (net_pnl / abs(max_dd)) if max_dd != 0 else (float("inf") if net_pnl > 0 else 0.0)

        # Active bots & strategies count
        from app.models.bot import Bot
        from app.models.strategy import Strategy
        from sqlalchemy import func
        active_bots_count = (await self.session.execute(
            select(func.count(Bot.id)).where(Bot.user_id == user_id, Bot.status == "running")
        )).scalar() or 0
        active_strategies_count = (await self.session.execute(
            select(func.count(Strategy.id)).where(Strategy.user_id == user_id, Strategy.status == "active")
        )).scalar() or 0

        return AnalyticsSummary(
            total_trades=total_trades,
            winning_trades=winning,
            losing_trades=losing,
            win_rate=_round(win_rate, 6),
            loss_rate=_round(loss_rate, 6),
            gross_profit=_round(gross_profit),
            gross_loss=_round(gross_loss),
            net_pnl=_round(net_pnl),
            profit_factor=_round(profit_factor, 6) if profit_factor != float("inf") else 0.0,
            sharpe_ratio=_round(sharpe),
            sortino_ratio=_round(sortino),
            calmar_ratio=_round(calmar),
            recovery_factor=_round(recovery_factor, 6) if recovery_factor != float("inf") else 0.0,
            expectancy=_round(expectancy),
            average_win=_round(avg_win),
            average_loss=_round(avg_loss),
            average_rr=_round(avg_rr, 6),
            largest_win=_round(largest_win),
            largest_loss=_round(largest_loss),
            consecutive_wins=max_c_wins,
            consecutive_losses=max_c_losses,
            max_drawdown=_round(max_dd),
            max_drawdown_pct=_round(max_dd_pct, 6),
            active_bots=int(active_bots_count),
            active_strategies=int(active_strategies_count),
        )

    def _equity_curve_from(self, trades: list[PaperTrade]) -> list[tuple[datetime, float]]:
        eq = 0.0
        pts: list[tuple[datetime, float]] = []
        for t in trades:
            eq += float(t.realized_pnl_delta or 0)
            pts.append((t.executed_at, eq))
        return pts

    async def equity_curve(self, user_id: str) -> list[dict[str, Any]]:
        trades = await self._load_trades(user_id)
        eq = 0.0
        out: list[dict[str, Any]] = []
        for t in trades:
            eq += float(t.realized_pnl_delta or 0)
            out.append({
                "ts": t.executed_at.isoformat() if t.executed_at else None,
                "equity": _round(eq),
                "trade_pnl": _round(float(t.realized_pnl_delta or 0)),
            })
        return out

    async def drawdown_series(self, user_id: str) -> list[dict[str, Any]]:
        pts = self._equity_curve_from(await self._load_trades(user_id))
        peak = 0.0
        out: list[dict[str, Any]] = []
        for ts, eq in pts:
            peak = max(peak, eq)
            dd = eq - peak
            out.append({
                "ts": ts.isoformat() if ts else None,
                "equity": _round(eq),
                "peak": _round(peak),
                "drawdown": _round(dd),
            })
        return out

    async def monthly_performance(self, user_id: str) -> list[dict[str, Any]]:
        trades = await self._load_trades(user_id)
        buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "wins": 0, "losses": 0, "n": 0})
        for t in trades:
            key = t.executed_at.strftime("%Y-%m")
            pnl = float(t.realized_pnl_delta or 0)
            b = buckets[key]
            b["pnl"] += pnl
            b["n"] += 1
            if pnl > 0:
                b["wins"] += 1
            elif pnl < 0:
                b["losses"] += 1
        return [
            {
                "month": k,
                "pnl": _round(v["pnl"]),
                "trades": int(v["n"]),
                "wins": int(v["wins"]),
                "losses": int(v["losses"]),
                "win_rate": _round((v["wins"] / v["n"]) if v["n"] else 0.0, 6),
            }
            for k, v in sorted(buckets.items())
        ]

    async def journal(
        self, user_id: str, *, offset: int = 0, limit: int = 100, symbol: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        stmt = select(PaperTrade).where(PaperTrade.user_id == user_id)
        if symbol:
            stmt = stmt.where(PaperTrade.symbol == symbol)
        all_rows = (await self.session.execute(stmt.order_by(PaperTrade.executed_at.desc()))).scalars().all()
        total = len(all_rows)
        items = all_rows[offset:offset + limit]
        return [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                "quantity": float(t.quantity),
                "price": float(t.price),
                "realized_pnl_delta": float(t.realized_pnl_delta or 0),
                "strategy_name": t.strategy_name,
                "executed_at": t.executed_at.isoformat() if t.executed_at else None,
            }
            for t in items
        ], total


def _drawdown(curve: list[tuple[datetime, float]]) -> tuple[float, float]:
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        dd = eq - peak  # <= 0
        if dd < max_dd:
            max_dd = dd
            if peak > 0:
                max_dd_pct = dd / peak
    return max_dd, max_dd_pct
