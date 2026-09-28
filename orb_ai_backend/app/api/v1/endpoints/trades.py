"""Trade endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DBSession
from app.models.trade import TradeStatus
from app.schemas.common import PaginatedResponse
from app.schemas.trade import TradeCreate, TradeRead, TradeUpdate
from app.services.trade_service import TradeService

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[TradeRead],
    summary="List current user's trades",
)
async def list_trades(
    current_user: CurrentUser,
    session: DBSession,
    strategy_id: str | None = Query(None),
    status_filter: TradeStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[TradeRead]:
    offset = (page - 1) * page_size
    items, total = await TradeService(session).list_for_user(
        current_user,
        strategy_id=strategy_id,
        status=status_filter,
        offset=offset,
        limit=page_size,
    )
    return PaginatedResponse[TradeRead](
        items=[TradeRead.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=TradeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a trade record",
)
async def create_trade(
    payload: TradeCreate,
    current_user: CurrentUser,
    session: DBSession,
) -> TradeRead:
    entity = await TradeService(session).create(current_user, payload)
    return TradeRead.model_validate(entity)


@router.get("/{trade_id}", response_model=TradeRead, summary="Get a trade")
async def get_trade(
    trade_id: str, current_user: CurrentUser, session: DBSession
) -> TradeRead:
    entity = await TradeService(session).get_for_user(current_user, trade_id)
    return TradeRead.model_validate(entity)


@router.patch("/{trade_id}", response_model=TradeRead, summary="Update a trade")
async def update_trade(
    trade_id: str,
    payload: TradeUpdate,
    current_user: CurrentUser,
    session: DBSession,
) -> TradeRead:
    entity = await TradeService(session).update(current_user, trade_id, payload)
    return TradeRead.model_validate(entity)


@router.delete(
    "/{trade_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a trade",
)
async def delete_trade(
    trade_id: str, current_user: CurrentUser, session: DBSession
) -> Response:
    await TradeService(session).delete(current_user, trade_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
