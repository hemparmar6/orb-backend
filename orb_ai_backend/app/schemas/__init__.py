"""Pydantic schemas (request / response DTOs)."""

from app.schemas.auth import (
    LoginRequest,
    LogoutResponse,
    RefreshRequest,
    TokenPair,
)
from app.schemas.common import Message, PaginatedResponse
from app.schemas.settings import UserSettingsRead, UserSettingsUpsert
from app.schemas.strategy import (
    StrategyCreate,
    StrategyRead,
    StrategyUpdate,
)
from app.schemas.trade import TradeCreate, TradeRead, TradeUpdate
from app.schemas.user import (
    ChangePasswordRequest,
    UserCreate,
    UserRead,
    UserUpdate,
)

__all__ = [
    "Message",
    "PaginatedResponse",
    "LoginRequest",
    "LogoutResponse",
    "RefreshRequest",
    "TokenPair",
    "UserCreate",
    "UserRead",
    "UserUpdate",
    "ChangePasswordRequest",
    "StrategyCreate",
    "StrategyRead",
    "StrategyUpdate",
    "TradeCreate",
    "TradeRead",
    "TradeUpdate",
    "UserSettingsRead",
    "UserSettingsUpsert",
]
