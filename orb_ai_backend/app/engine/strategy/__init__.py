"""Strategy package."""

from app.engine.strategy.base import BaseStrategy, StrategyContext
from app.engine.strategy.manager import StrategyManager
from app.engine.strategy.registry import (
    get_strategy_class,
    list_strategies,
    register_strategy,
)

# Import samples so their @register_strategy decorators run.
from app.engine.strategy.samples import demo_ma  # noqa: F401
from app.engine.strategy.samples import orb  # noqa: F401

__all__ = [
    "BaseStrategy",
    "StrategyContext",
    "StrategyManager",
    "get_strategy_class",
    "list_strategies",
    "register_strategy",
]
