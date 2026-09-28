"""Strategy registry."""
from __future__ import annotations

from typing import Callable, Type

from app.core.exceptions import StrategyNotFoundError
from app.engine.strategy.base import BaseStrategy

_registry: dict[str, Type[BaseStrategy]] = {}


def register_strategy(name: str) -> Callable[[Type[BaseStrategy]], Type[BaseStrategy]]:
    key = name.lower()

    def _decorator(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
        cls.name = name
        _registry[key] = cls
        return cls
    return _decorator


def get_strategy_class(name: str) -> Type[BaseStrategy]:
    key = name.lower()
    if key not in _registry:
        raise StrategyNotFoundError(
            f"Strategy '{name}' is not registered. "
            f"Available: {sorted(_registry.keys())}"
        )
    return _registry[key]


def list_strategies() -> list[str]:
    return sorted(_registry.keys())
