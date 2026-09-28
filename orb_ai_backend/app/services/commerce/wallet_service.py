"""WalletService — user credit ledger.

Every mutation records a WalletTransaction so we have an immutable
audit trail. Balance is never mutated in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import (
    Wallet,
    WalletTransaction,
    WalletTxnDirection,
    WalletTxnReason,
)


class WalletError(Exception):
    """Base class for wallet errors."""


class InsufficientFundsError(WalletError):
    pass


class WalletFrozenError(WalletError):
    pass


@dataclass
class WalletSnapshot:
    id: str
    user_id: str
    balance_cents: int
    currency: str
    lifetime_earned_cents: int
    lifetime_spent_cents: int
    is_frozen: bool


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, user_id: str, *, currency: str = "INR") -> Wallet:
        w = await self.session.scalar(select(Wallet).where(Wallet.user_id == user_id))
        if w is None:
            w = Wallet(user_id=user_id, currency=currency, balance_cents=0)
            self.session.add(w)
            await self.session.flush()
        return w

    async def snapshot(self, user_id: str) -> WalletSnapshot:
        w = await self.get_or_create(user_id)
        return WalletSnapshot(
            id=w.id, user_id=w.user_id, balance_cents=w.balance_cents,
            currency=w.currency,
            lifetime_earned_cents=w.lifetime_earned_cents,
            lifetime_spent_cents=w.lifetime_spent_cents,
            is_frozen=w.is_frozen,
        )

    async def credit(
        self,
        user_id: str,
        amount_cents: int,
        *,
        reason: WalletTxnReason,
        description: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> WalletTransaction:
        if amount_cents <= 0:
            raise WalletError("credit amount must be positive")
        w = await self.get_or_create(user_id)
        if w.is_frozen:
            raise WalletFrozenError("wallet is frozen")
        w.balance_cents += amount_cents
        w.lifetime_earned_cents += amount_cents
        txn = WalletTransaction(
            wallet_id=w.id,
            direction=WalletTxnDirection.CREDIT,
            reason=reason,
            amount_cents=amount_cents,
            balance_after_cents=w.balance_cents,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        self.session.add(txn)
        await self.session.flush()
        return txn

    async def debit(
        self,
        user_id: str,
        amount_cents: int,
        *,
        reason: WalletTxnReason,
        description: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        allow_partial: bool = False,
    ) -> tuple[WalletTransaction, int]:
        """Debit up to ``amount_cents`` from the wallet.

        Returns (transaction, actual_debited_cents). When ``allow_partial``
        is True and balance is insufficient, we debit what's available;
        otherwise raises InsufficientFundsError.
        """
        if amount_cents <= 0:
            raise WalletError("debit amount must be positive")
        w = await self.get_or_create(user_id)
        if w.is_frozen:
            raise WalletFrozenError("wallet is frozen")
        take = min(amount_cents, w.balance_cents) if allow_partial else amount_cents
        if not allow_partial and w.balance_cents < amount_cents:
            raise InsufficientFundsError(
                f"balance {w.balance_cents} < required {amount_cents}"
            )
        if take <= 0:
            # Nothing to debit — return a zero-value txn for auditability.
            txn = WalletTransaction(
                wallet_id=w.id,
                direction=WalletTxnDirection.DEBIT,
                reason=reason,
                amount_cents=0,
                balance_after_cents=w.balance_cents,
                description=(description or "") + " (no-op; empty wallet)",
                reference_type=reference_type,
                reference_id=reference_id,
            )
            self.session.add(txn)
            await self.session.flush()
            return txn, 0
        w.balance_cents -= take
        w.lifetime_spent_cents += take
        txn = WalletTransaction(
            wallet_id=w.id,
            direction=WalletTxnDirection.DEBIT,
            reason=reason,
            amount_cents=take,
            balance_after_cents=w.balance_cents,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        self.session.add(txn)
        await self.session.flush()
        return txn, take

    async def history(self, user_id: str, *, limit: int = 50) -> list[WalletTransaction]:
        w = await self.get_or_create(user_id)
        rows = await self.session.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.wallet_id == w.id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(limit)
        )
        return list(rows)
