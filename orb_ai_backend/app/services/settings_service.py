"""User settings service."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import UserSettings
from app.models.user import User
from app.repositories.settings_repository import UserSettingsRepository
from app.schemas.settings import UserSettingsUpsert


class UserSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserSettingsRepository(session)

    async def get_or_create(self, user: User) -> UserSettings:
        existing = await self.repo.get_by_user_id(user.id)
        if existing is not None:
            return existing
        created = UserSettings(user_id=user.id)
        await self.repo.add(created)
        await self.session.commit()
        await self.session.refresh(created)
        return created

    async def upsert(self, user: User, payload: UserSettingsUpsert) -> UserSettings:
        entity = await self.get_or_create(user)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(entity, key, value)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity
