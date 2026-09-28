"""Broker endpoints.

Order placement is intentionally NOT exposed here — all trading flows through
the Trading Engine (`/api/v1/trading/*`). These routes only manage broker
accounts + expose read-only snapshots from the broker itself.
"""
from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession
from app.brokers.registry import get_broker_adapter_class, list_brokers
from app.schemas.broker import (
    BrokerAccountRead,
    BrokerCatalogEntry,
    BrokerCatalogResponse,
    BrokerConnectRequest,
    BrokerFundsResponse,
    BrokerOrderRead,
    BrokerPositionRead,
)
from app.schemas.common import Message
from app.services.broker_service import BrokerService

router = APIRouter()


@router.get(
    "/catalog",
    response_model=BrokerCatalogResponse,
    summary="List available brokers and their required credential keys",
)
async def catalog() -> BrokerCatalogResponse:
    entries: list[BrokerCatalogEntry] = []
    for name in list_brokers():
        cls = get_broker_adapter_class(name)
        entries.append(
            BrokerCatalogEntry(broker_type=name, required_credentials=cls.required_credentials())
        )
    return BrokerCatalogResponse(brokers=entries)


@router.post(
    "/connect",
    response_model=BrokerAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register (and encrypt) a broker account for the current user",
)
async def connect(
    payload: BrokerConnectRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> BrokerAccountRead:
    account = await BrokerService(session).connect(current_user.id, payload)
    return BrokerAccountRead.model_validate(account)


@router.get(
    "",
    response_model=list[BrokerAccountRead],
    summary="List the current user's broker accounts",
)
async def list_accounts(
    current_user: CurrentUser,
    session: DBSession,
) -> list[BrokerAccountRead]:
    accounts = await BrokerService(session).list_for_user(current_user.id)
    return [BrokerAccountRead.model_validate(a) for a in accounts]


@router.get(
    "/{broker_account_id}",
    response_model=BrokerAccountRead,
    summary="Get a broker account by id",
)
async def get_account(
    broker_account_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> BrokerAccountRead:
    acc = await BrokerService(session).get_for_user(current_user.id, broker_account_id)
    return BrokerAccountRead.model_validate(acc)


@router.delete(
    "/{broker_account_id}",
    response_model=Message,
    summary="Disconnect (delete) a broker account",
)
async def delete_account(
    broker_account_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> Message:
    await BrokerService(session).delete(current_user.id, broker_account_id)
    return Message(message="Broker account disconnected")


@router.get(
    "/{broker_account_id}/funds",
    response_model=BrokerFundsResponse,
    summary="Live funds/margin snapshot from the broker",
)
async def get_funds(
    broker_account_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> BrokerFundsResponse:
    svc = BrokerService(session)
    acc = await svc.get_for_user(current_user.id, broker_account_id)
    adapter = await svc.build_adapter(acc)
    await session.commit()
    funds = await adapter.get_funds()
    return BrokerFundsResponse(
        available=funds.available,
        used=funds.used,
        total=funds.total,
        currency=funds.currency,
    )


@router.get(
    "/{broker_account_id}/positions",
    response_model=list[BrokerPositionRead],
    summary="Live positions snapshot from the broker",
)
async def get_positions(
    broker_account_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[BrokerPositionRead]:
    svc = BrokerService(session)
    acc = await svc.get_for_user(current_user.id, broker_account_id)
    adapter = await svc.build_adapter(acc)
    await session.commit()
    positions = await adapter.list_positions()
    return [
        BrokerPositionRead(
            symbol=p.symbol,
            exchange=p.exchange,
            product=p.product,
            net_quantity=p.net_quantity,
            average_price=p.average_price,
            realized_pnl=p.realized_pnl,
            unrealized_pnl=p.unrealized_pnl,
            last_price=p.last_price,
        )
        for p in positions
    ]


@router.get(
    "/{broker_account_id}/orders",
    response_model=list[BrokerOrderRead],
    summary="Live order book from the broker",
)
async def get_orders(
    broker_account_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[BrokerOrderRead]:
    svc = BrokerService(session)
    acc = await svc.get_for_user(current_user.id, broker_account_id)
    adapter = await svc.build_adapter(acc)
    await session.commit()
    orders = await adapter.list_orders()
    return [
        BrokerOrderRead(
            broker_order_id=o.broker_order_id,
            status=o.status.value,
            filled_quantity=o.filled_quantity,
            average_fill_price=o.average_fill_price,
            rejection_reason=o.rejection_reason,
            client_order_id=o.client_order_id,
        )
        for o in orders
    ]
