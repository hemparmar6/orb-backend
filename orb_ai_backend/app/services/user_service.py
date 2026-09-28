"""User profile service."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidCredentialsError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import ChangePasswordRequest, UserUpdate


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def update_profile(self, user: User, payload: UserUpdate) -> User:
        data = payload.model_dump(exclude_unset=True)
        if data:
            for key, value in data.items():
                setattr(user, key, value)
            await self.session.commit()
            await self.session.refresh(user)
        return user

    async def change_password(self, user: User, payload: ChangePasswordRequest) -> None:
        if not verify_password(payload.current_password, user.hashed_password):
            raise InvalidCredentialsError("Current password is incorrect")
        user.hashed_password = hash_password(payload.new_password)
        # Force re-login on all other sessions.
        user.current_refresh_jti = None
        user.refresh_token_expires_at = None
        await self.session.commit()
