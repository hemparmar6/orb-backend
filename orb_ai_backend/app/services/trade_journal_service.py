"""Trade Journal service — generates AI reviews for closed manual trades."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.registry import LATEST
from app.ai.service import AIRequestContext, AIService
from app.models import AITradeReview, Trade, TradeStatus


PROMPT_NAME = "trade_review"
PROMPT_VERSION = LATEST[PROMPT_NAME]


class TradeJournalService:
    def __init__(self, session: AsyncSession, ai: AIService) -> None:
        self.session = session
        self.ai = ai

    async def get_existing(self, trade_id: str, user_id: str) -> Optional[AITradeReview]:
        stmt = (
            select(AITradeReview)
            .where(
                AITradeReview.trade_id == trade_id,
                AITradeReview.user_id == user_id,
                AITradeReview.prompt_version == PROMPT_VERSION,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_trade(self, trade_id: str, user_id: str) -> Optional[Trade]:
        stmt = select(Trade).where(Trade.id == trade_id, Trade.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def review_trade(self, user_id: str, trade: Trade) -> AITradeReview:
        # Idempotent per (trade_id, prompt_version).
        existing = await self.get_existing(trade.id, user_id)
        if existing is not None:
            return existing

        payload = {"trade": self._trade_to_payload(trade)}
        ctx = AIRequestContext(
            user_id=user_id,
            request_type="trade_review",
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            payload=payload,
        )
        resp = await self.ai.analyse(ctx)
        c = resp.content or {}

        row = AITradeReview(
            trade_id=trade.id,
            user_id=user_id,
            trade_quality_score=_num(c.get("trade_quality_score")),
            entry_quality=_num(c.get("entry_quality")),
            exit_quality=_num(c.get("exit_quality")),
            risk_management_score=_num(c.get("risk_management_score")),
            rule_compliance=_num(c.get("rule_compliance")),
            emotional_flags=list(c.get("emotional_flags") or []),
            improvements=list(c.get("improvements") or []),
            summary=c.get("summary"),
            provider=resp.provider,
            model=resp.model,
            prompt_version=PROMPT_VERSION,
            source="fallback" if resp.provider == "rule_based" else "primary",
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    @staticmethod
    def _trade_to_payload(t: Trade) -> Dict[str, Any]:
        return {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side.value if hasattr(t.side, "value") else str(t.side),
            "quantity": float(t.quantity or 0),
            "entry_price": float(t.entry_price) if t.entry_price is not None else None,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "stop_loss": float(t.stop_loss) if t.stop_loss is not None else None,
            "take_profit": float(t.take_profit) if t.take_profit is not None else None,
            "pnl": float(t.pnl) if t.pnl is not None else None,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        }


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
