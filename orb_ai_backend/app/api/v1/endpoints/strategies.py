"""Strategy endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.common import PaginatedResponse
from app.schemas.strategy import StrategyCreate, StrategyRead, StrategyUpdate
from app.services.strategy_service import StrategyService

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[StrategyRead],
    summary="List current user's strategies",
)
async def list_strategies(
    current_user: CurrentUser,
    session: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[StrategyRead]:
    offset = (page - 1) * page_size
    items, total = await StrategyService(session).list_for_user(
        current_user, offset=offset, limit=page_size
    )
    return PaginatedResponse[StrategyRead](
        items=[StrategyRead.model_validate(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=StrategyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a strategy",
)
async def create_strategy(
    payload: StrategyCreate,
    current_user: CurrentUser,
    session: DBSession,
) -> StrategyRead:
    entity = await StrategyService(session).create(current_user, payload)
    return StrategyRead.model_validate(entity)


@router.get("/{strategy_id}", response_model=StrategyRead, summary="Get a strategy")
async def get_strategy(
    strategy_id: str, current_user: CurrentUser, session: DBSession
) -> StrategyRead:
    entity = await StrategyService(session).get_for_user(current_user, strategy_id)
    return StrategyRead.model_validate(entity)


@router.patch("/{strategy_id}", response_model=StrategyRead, summary="Update a strategy")
async def update_strategy(
    strategy_id: str,
    payload: StrategyUpdate,
    current_user: CurrentUser,
    session: DBSession,
) -> StrategyRead:
    entity = await StrategyService(session).update(current_user, strategy_id, payload)
    return StrategyRead.model_validate(entity)


@router.delete(
    "/{strategy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a strategy",
)
async def delete_strategy(
    strategy_id: str, current_user: CurrentUser, session: DBSession
) -> Response:
    await StrategyService(session).delete(current_user, strategy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
