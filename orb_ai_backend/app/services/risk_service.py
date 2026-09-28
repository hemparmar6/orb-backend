"""Risk analytics service (Module 8)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker import BrokerAccount
from app.models.engine import EngineSession, PaperPosition, PaperTrade
from app.services.analytics_service import AnalyticsService, _drawdown


class RiskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def exposure_by_symbol(self, user_id: str) -> list[dict[str, Any]]:
        stmt = select(PaperPosition).where(
            PaperPosition.user_id == user_id, PaperPosition.net_quantity != 0
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"long": 0.0, "short": 0.0, "net": 0.0})
        for p in rows:
            qty = float(p.net_quantity)
            ltp = float(p.last_price) if p.last_price is not None else float(p.average_price)
            v = abs(ltp * qty)
            b = buckets[p.symbol]
            if qty > 0:
                b["long"] += v
            else:
                b["short"] += v
            b["net"] += ltp * qty
        return [
            {"symbol": s, "long_value": round(b["long"], 4), "short_value": round(b["short"], 4),
             "net_value": round(b["net"], 4), "gross_value": round(b["long"] + b["short"], 4)}
            for s, b in sorted(buckets.items())
        ]

    async def exposure_by_broker(self, user_id: str) -> list[dict[str, Any]]:
        # Broker mapping via EngineSession.broker_account_id
        sess_stmt = select(EngineSession).where(EngineSession.user_id == user_id)
        sessions = (await self.session.execute(sess_stmt)).scalars().all()
        session_to_broker = {s.id: s.broker_account_id for s in sessions}

        broker_stmt = select(BrokerAccount).where(BrokerAccount.user_id == user_id)
        brokers = {b.id: b for b in (await self.session.execute(broker_stmt)).scalars().all()}

        pos_stmt = select(PaperPosition).where(
            PaperPosition.user_id == user_id, PaperPosition.net_quantity != 0
        )
        positions = (await self.session.execute(pos_stmt)).scalars().all()
        buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"gross": 0.0, "net": 0.0, "positions": 0})
        for p in positions:
            broker_id = session_to_broker.get(p.engine_session_id)
            broker_key = "paper" if broker_id is None else brokers.get(broker_id, None)
            key = (
                broker_key.broker_type.value
                if hasattr(broker_key, "broker_type")
                else "paper"
            )
            qty = float(p.net_quantity)
            ltp = float(p.last_price) if p.last_price is not None else float(p.average_price)
            b = buckets[key]
            b["gross"] += abs(ltp * qty)
            b["net"] += ltp * qty
            b["positions"] += 1
        return [
            {"broker": k, "gross_value": round(v["gross"], 4),
             "net_value": round(v["net"], 4), "positions": int(v["positions"])}
            for k, v in sorted(buckets.items())
        ]

    async def daily_risk(self, user_id: str) -> dict[str, Any]:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(func.sum(PaperTrade.realized_pnl_delta), func.count()).where(
            PaperTrade.user_id == user_id, PaperTrade.executed_at >= start
        )
        pnl_sum, n = (await self.session.execute(stmt)).one()
        return {
            "date": start.date().isoformat(),
            "day_pnl": round(float(pnl_sum or 0), 4),
            "trades_today": int(n or 0),
        }

    async def max_drawdown(self, user_id: str) -> dict[str, Any]:
        analytics = AnalyticsService(self.session)
        trades = await analytics._load_trades(user_id)
        curve = analytics._equity_curve_from(trades)
        dd, dd_pct = _drawdown(curve)
        return {"max_drawdown": round(dd, 4), "max_drawdown_pct": round(dd_pct, 6)}

    async def margin_utilisation(self, user_id: str) -> dict[str, Any]:
        # Approximate: total gross exposure / total initial_capital across sessions.
        sess_stmt = select(func.sum(EngineSession.initial_capital)).where(
            EngineSession.user_id == user_id
        )
        capital = float((await self.session.execute(sess_stmt)).scalar_one() or 0)
        exposures = await self.exposure_by_symbol(user_id)
        gross = sum(e["gross_value"] for e in exposures)
        util = (gross / capital) if capital > 0 else 0.0
        return {
            "capital": round(capital, 4),
            "gross_exposure": round(gross, 4),
            "utilisation": round(util, 6),
            "free_capital": round(max(capital - gross, 0), 4),
        }

    async def position_sizing(self, user_id: str) -> list[dict[str, Any]]:
        pos_stmt = select(PaperPosition).where(
            PaperPosition.user_id == user_id, PaperPosition.net_quantity != 0
        )
        positions = (await self.session.execute(pos_stmt)).scalars().all()
        sess_stmt = select(func.sum(EngineSession.initial_capital)).where(
            EngineSession.user_id == user_id
        )
        capital = float((await self.session.execute(sess_stmt)).scalar_one() or 0)
        out: list[dict[str, Any]] = []
        for p in positions:
            qty = float(p.net_quantity)
            ltp = float(p.last_price) if p.last_price is not None else float(p.average_price)
            value = abs(ltp * qty)
            out.append({
                "symbol": p.symbol,
                "quantity": qty,
                "value": round(value, 4),
                "capital_pct": round((value / capital) if capital > 0 else 0.0, 6),
            })
        return sorted(out, key=lambda x: x["value"], reverse=True)

    async def dashboard(self, user_id: str) -> dict[str, Any]:
        return {
            "exposure_by_symbol": await self.exposure_by_symbol(user_id),
            "exposure_by_broker": await self.exposure_by_broker(user_id),
            "daily_risk": await self.daily_risk(user_id),
            "max_drawdown": await self.max_drawdown(user_id),
            "margin": await self.margin_utilisation(user_id),
            "position_sizing": await self.position_sizing(user_id),
        }
