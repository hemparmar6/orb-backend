"""JWT auth for WebSocket connections.

WebSockets cannot send an ``Authorization`` header from JS reliably, so we
accept the access token via query param: ``ws://.../ws/v1/quotes?token=<JWT>``.

On failure the caller SHOULD close the socket with code 4401
(app-defined; standard 1008 is also acceptable) and NOT return partial data.
"""
from __future__ import annotations

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InactiveUserError, InvalidTokenError, UnauthorizedError
from app.core.security import decode_token
from app.models.user import User
from app.repositories.user_repository import UserRepository


async def authenticate_ws(ws: WebSocket, db: AsyncSession) -> User:
    """Resolve ``?token=`` on the WebSocket URL to a User, or raise.

    ``db`` MUST come from FastAPI's ``Depends(get_db)`` so it honors the
    test suite's dependency overrides.
    """
    token = ws.query_params.get("token")
    if not token:
        raise UnauthorizedError("Missing 'token' query param on WebSocket connection")

    payload = decode_token(token, expected_type="access")  # raises InvalidTokenError
    user_id = payload.get("sub")
    if not user_id:
        raise InvalidTokenError("Token missing subject")

    user = await UserRepository(db).get(user_id)
    if user is None:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise InactiveUserError()
    return user
