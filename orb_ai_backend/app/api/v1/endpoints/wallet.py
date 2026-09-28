"""Wallet REST endpoints (v1.1.0 Phase 2).

Base path: /api/v1/wallet
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.commerce import WalletTxnDirection, WalletTxnReason
from app.services.audit_service import AuditService
from app.services.commerce.wallet_service import (
    InsufficientFundsError,
    WalletFrozenError,
    WalletService,
)

router = APIRouter()


class WalletOut(BaseModel):
    id: str
    user_id: str
    balance_cents: int
    currency: str
    lifetime_earned_cents: int
    lifetime_spent_cents: int
    is_frozen: bool


class WalletTxnOut(BaseModel):
    id: str
    direction: str
    reason: str
    amount_cents: int
    balance_after_cents: int
    description: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    created_at: datetime


class AdminMutateIn(BaseModel):
    user_id: str
    amount_cents: int = Field(..., gt=0)
    reason: str = Field(..., description="Any WalletTxnReason value")
    description: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


# ---------------------------------------------------------------- self
@router.get("/me", response_model=WalletOut, summary="Get current user's wallet")
async def get_my_wallet(user: CurrentUser, session: DBSession) -> WalletOut:
    svc = WalletService(session)
    snap = await svc.snapshot(user.id)
    await session.commit()
    return WalletOut(**snap.__dict__)


@router.get(
    "/me/transactions",
    response_model=list[WalletTxnOut],
    summary="Current user's wallet transactions",
)
async def get_my_transactions(
    user: CurrentUser, session: DBSession, limit: int = 50,
) -> list[WalletTxnOut]:
    svc = WalletService(session)
    rows = await svc.history(user.id, limit=limit)
    return [
        WalletTxnOut(
            id=t.id,
            direction=t.direction.value,
            reason=t.reason.value,
            amount_cents=t.amount_cents,
            balance_after_cents=t.balance_after_cents,
            description=t.description,
            reference_type=t.reference_type,
            reference_id=t.reference_id,
            created_at=t.created_at,
        )
        for t in rows
    ]


# ---------------------------------------------------------------- admin
def _parse_reason(reason: str) -> WalletTxnReason:
    try:
        return WalletTxnReason(reason)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"reason must be one of {[r.value for r in WalletTxnReason]}",
        )


@router.post(
    "/admin/credit",
    response_model=WalletOut,
    summary="[admin] Credit a user's wallet",
)
async def admin_credit(
    payload: AdminMutateIn, admin: AdminUser, session: DBSession,
) -> WalletOut:
    reason = _parse_reason(payload.reason)
    svc = WalletService(session)
    try:
        await svc.credit(
            payload.user_id, payload.amount_cents,
            reason=reason, description=payload.description,
            reference_type=payload.reference_type,
            reference_id=payload.reference_id,
        )
    except WalletFrozenError:
        raise HTTPException(status_code=400, detail="wallet is frozen")
    await AuditService(session).record(
        action="wallet.credit", target_type="wallet",
        target_id=payload.user_id, actor=admin,
        details={"amount_cents": payload.amount_cents, "reason": reason.value},
    )
    snap = await svc.snapshot(payload.user_id)
    await session.commit()
    return WalletOut(**snap.__dict__)


@router.post(
    "/admin/debit",
    response_model=WalletOut,
    summary="[admin] Debit a user's wallet",
)
async def admin_debit(
    payload: AdminMutateIn, admin: AdminUser, session: DBSession,
) -> WalletOut:
    reason = _parse_reason(payload.reason)
    svc = WalletService(session)
    try:
        await svc.debit(
            payload.user_id, payload.amount_cents,
            reason=reason, description=payload.description,
            reference_type=payload.reference_type,
            reference_id=payload.reference_id,
            allow_partial=False,
        )
    except InsufficientFundsError:
        raise HTTPException(status_code=400, detail="insufficient balance")
    except WalletFrozenError:
        raise HTTPException(status_code=400, detail="wallet is frozen")
    await AuditService(session).record(
        action="wallet.debit", target_type="wallet",
        target_id=payload.user_id, actor=admin,
        details={"amount_cents": payload.amount_cents, "reason": reason.value},
    )
    snap = await svc.snapshot(payload.user_id)
    await session.commit()
    return WalletOut(**snap.__dict__)
