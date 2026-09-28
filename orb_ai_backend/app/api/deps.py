"""FastAPI dependencies."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ForbiddenError,
    InactiveUserError,
    InvalidTokenError,
    UnauthorizedError,
)
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository

# tokenUrl is what Swagger UI's "Authorize" button will POST to.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login",
    auto_error=False,
)

DBSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    session: DBSession,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the bearer token to a User, or raise 401."""
    # OAuth2PasswordBearer already extracts from `Authorization: Bearer <t>`
    # but we accept a fallback in case of custom clients.
    raw_token: str | None = token
    if raw_token is None and authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()
    if not raw_token:
        raise UnauthorizedError("Missing bearer token")

    try:
        payload = decode_token(raw_token, expected_type="access")
    except InvalidTokenError:
        raise
    user_id = payload["sub"]

    user = await UserRepository(session).get(user_id)
    if user is None:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise InactiveUserError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin privileges required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
