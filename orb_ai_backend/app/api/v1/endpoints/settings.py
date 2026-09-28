"""User settings endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.schemas.settings import UserSettingsRead, UserSettingsUpsert
from app.services.settings_service import UserSettingsService

router = APIRouter()


@router.get("/me", response_model=UserSettingsRead, summary="Get current user's settings")
async def read_settings(
    current_user: CurrentUser, session: DBSession
) -> UserSettingsRead:
    entity = await UserSettingsService(session).get_or_create(current_user)
    return UserSettingsRead.model_validate(entity)


@router.put(
    "/me",
    response_model=UserSettingsRead,
    summary="Upsert (partial merge) current user's settings",
)
async def upsert_settings(
    payload: UserSettingsUpsert,
    current_user: CurrentUser,
    session: DBSession,
) -> UserSettingsRead:
    entity = await UserSettingsService(session).upsert(current_user, payload)
    return UserSettingsRead.model_validate(entity)
