"""User profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.common import Message
from app.schemas.user import ChangePasswordRequest, UserRead, UserUpdate
from app.services.user_service import UserService

router = APIRouter()


@router.get("/me", response_model=UserRead, summary="Get current user profile")
async def read_me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead, summary="Update current user profile")
async def update_me(
    payload: UserUpdate,
    current_user: CurrentUser,
    session: DBSession,
) -> UserRead:
    updated = await UserService(session).update_profile(current_user, payload)
    return UserRead.model_validate(updated)


@router.post(
    "/me/change-password",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    summary="Change current user's password",
)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> Message:
    await UserService(session).change_password(current_user, payload)
    return Message(message="Password updated. Please log in again.")
