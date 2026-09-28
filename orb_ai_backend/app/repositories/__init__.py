"""Repository layer.

Repositories encapsulate all direct DB access (SQLAlchemy queries). Services
depend on repository interfaces, never on the ORM directly.
"""

from app.repositories.settings_repository import UserSettingsRepository
from app.repositories.strategy_repository import StrategyRepository
from app.repositories.trade_repository import TradeRepository
from app.repositories.user_repository import UserRepository

__all__ = [
    "UserRepository",
    "TradeRepository",
    "StrategyRepository",
    "UserSettingsRepository",
]
