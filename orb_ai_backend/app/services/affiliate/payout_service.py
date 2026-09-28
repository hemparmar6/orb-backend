"""PayoutService — affiliate withdrawal workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import (
    Affiliate,
    Payout,
    PayoutMethod,
    PayoutStatus,
)
from app.models.commerce import WalletTxnReason
from app.services.affiliate.program_service import ProgramService
from app.services.commerce.wallet_service import WalletService


class PayoutError(Exception):
    pass


class InsufficientBalance(PayoutError):
    pass


class BelowMinimumPayout(PayoutError):
    pass


class PayoutService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def request(
        self,
        affiliate: Affiliate,
        *,
        amount_cents: int,
        method: Optional[PayoutMethod] = None,
        method_details: Optional[dict] = None,
        notes: Optional[str] = None,
    ) -> Payout:
        program = await ProgramService(self.session).get()
        if amount_cents < program.min_payout_cents:
            raise BelowMinimumPayout(
                f"Minimum payout is {program.min_payout_cents} cents"
            )
        # Verify wallet has enough.
        wallets = WalletService(self.session)
        snap = await wallets.snapshot(affiliate.user_id)
        if snap.balance_cents < amount_cents:
            raise InsufficientBalance(
                f"Wallet balance {snap.balance_cents} < requested {amount_cents}"
            )
        # Debit immediately (funds are held pending payout resolution).
        await wallets.debit(
            affiliate.user_id, amount_cents,
            reason=WalletTxnReason.ADJUSTMENT,
            description=f"Payout request (hold)",
            reference_type="payout_request",
            reference_id=affiliate.id,
        )
        payout = Payout(
            affiliate_id=affiliate.id,
            amount_cents=amount_cents,
            currency=program.currency,
            method=(method or affiliate.payout_method),
            method_details=(method_details or affiliate.payout_details),
            status=PayoutStatus.REQUESTED,
            requested_at=datetime.now(timezone.utc),
            notes=notes,
        )
        self.session.add(payout)
        await self.session.flush()
        return payout

    async def approve(self, payout: Payout, *, admin_id: str) -> Payout:
        payout.status = PayoutStatus.APPROVED
        payout.admin_id = admin_id
        await self.session.flush()
        return payout

    async def mark_processing(self, payout: Payout, *, admin_id: str) -> Payout:
        payout.status = PayoutStatus.PROCESSING
        payout.admin_id = admin_id
        await self.session.flush()
        return payout

    async def mark_paid(
        self, payout: Payout, *, admin_id: str,
        transaction_ref: Optional[str] = None,
    ) -> Payout:
        payout.status = PayoutStatus.PAID
        payout.admin_id = admin_id
        payout.processed_at = datetime.now(timezone.utc)
        payout.transaction_ref = transaction_ref
        affiliate = await self.session.get(Affiliate, payout.affiliate_id)
        if affiliate is not None:
            affiliate.total_commission_paid_cents = (
                affiliate.total_commission_paid_cents or 0
            ) + payout.amount_cents
        await self.session.flush()
        return payout

    async def reject(
        self, payout: Payout, *, admin_id: str, reason: str,
    ) -> Payout:
        """Reject a payout — refund the held amount back to the wallet."""
        if payout.status in {PayoutStatus.PAID, PayoutStatus.REJECTED}:
            return payout
        affiliate = await self.session.get(Affiliate, payout.affiliate_id)
        if affiliate is not None:
            await WalletService(self.session).credit(
                affiliate.user_id, payout.amount_cents,
                reason=WalletTxnReason.REFUND,
                description=f"Payout rejected: {reason}",
                reference_type="payout_rejected",
                reference_id=payout.id,
            )
        payout.status = PayoutStatus.REJECTED
        payout.admin_id = admin_id
        payout.notes = ((payout.notes or "") + f"\nrejected: {reason}").strip()
        await self.session.flush()
        return payout

    async def list_for_affiliate(self, affiliate_id: str) -> list[Payout]:
        rows = await self.session.scalars(
            select(Payout).where(Payout.affiliate_id == affiliate_id)
            .order_by(Payout.created_at.desc())
        )
        return list(rows)

    async def list_all(
        self, *, status: Optional[PayoutStatus] = None, limit: int = 100,
    ) -> list[Payout]:
        stmt = select(Payout)
        if status is not None:
            stmt = stmt.where(Payout.status == status)
        stmt = stmt.order_by(Payout.created_at.desc()).limit(limit)
        return list(await self.session.scalars(stmt))
