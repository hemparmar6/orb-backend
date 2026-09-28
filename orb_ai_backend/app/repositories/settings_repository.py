"""UserSettings repository."""
from __future__ import annotations

from sqlalchemy import select

from app.models.settings import UserSettings
from app.repositories.base import BaseRepository


class UserSettingsRepository(BaseRepository[UserSettings]):
    model = UserSettings

    async def get_by_user_id(self, user_id: str) -> UserSettings | None:
        stmt = select(UserSettings).where(UserSettings.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
