"""Authentication service.

Owns: registration, login, refresh-token rotation, logout.
Depends only on the UserRepository and the security helpers.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.settings import UserSettings
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.auth import TokenPair
from app.schemas.user import UserCreate

logger = get_logger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    # ------------------------------------------------------------------
    # Registration / Login
    # ------------------------------------------------------------------

    async def register(self, payload: UserCreate) -> User:
        email = payload.email.lower().strip()
        if await self.users.email_exists(email):
            raise EmailAlreadyRegisteredError()

        user = User(
            email=email,
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            phone=payload.phone,
            role=UserRole.USER,
            is_active=True,
            is_verified=False,
        )
        # Provision default settings alongside the user (single transaction).
        user.settings = UserSettings()

        await self.users.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info("user_registered", extra={"user_id": user.id, "email": user.email})
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.users.get_by_email(email.lower().strip())
        if user is None or not verify_password(password, user.hashed_password):
            # Same message either way — do not leak which one was wrong.
            raise InvalidCredentialsError()
        if not user.is_active:
            raise InactiveUserError()
        return user

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self.authenticate(email, password)
        pair = await self._issue_token_pair(user)
        user.last_login_at = datetime.now(timezone.utc)
        await self.session.commit()
        logger.info("user_login", extra={"user_id": user.id})
        return pair

    # ------------------------------------------------------------------
    # Refresh / Logout
    # ------------------------------------------------------------------

    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = payload["sub"]
        jti = payload.get("jti")

        user = await self.users.get(user_id)
        if user is None or not user.is_active:
            raise InvalidTokenError("Token subject not found")
        # Reject if the refresh token isn't the currently-active one for this user.
        if user.current_refresh_jti != jti:
            logger.warning(
                "refresh_token_reuse_or_stale",
                extra={"user_id": user_id, "presented_jti": jti},
            )
            # Belt-and-braces: revoke everything on reuse.
            user.current_refresh_jti = None
            user.refresh_token_expires_at = None
            await self.session.commit()
            raise InvalidTokenError("Refresh token is no longer valid")

        pair = await self._issue_token_pair(user)
        await self.session.commit()
        return pair

    async def logout(self, user: User) -> None:
        user.current_refresh_jti = None
        user.refresh_token_expires_at = None
        await self.session.commit()
        logger.info("user_logout", extra={"user_id": user.id})

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _issue_token_pair(self, user: User) -> TokenPair:
        access = create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.value},
        )
        refresh, jti, expires_at = create_refresh_token(subject=user.id)
        user.current_refresh_jti = jti
        user.refresh_token_expires_at = expires_at
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
