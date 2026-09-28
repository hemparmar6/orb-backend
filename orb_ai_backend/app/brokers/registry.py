"""Broker registry.

Adapters register themselves at import time with ``@register_broker("name")``.
The trading engine only ever calls ``get_broker_adapter(broker_type, creds)``
so it never imports concrete adapter modules directly.
"""
from __future__ import annotations

from typing import Any, Callable, Type

from app.brokers.base import BrokerAdapter
from app.core.exceptions import UnsupportedBrokerError

_registry: dict[str, Type[BrokerAdapter]] = {}


def register_broker(name: str) -> Callable[[Type[BrokerAdapter]], Type[BrokerAdapter]]:
    key = name.lower()

    def _decorator(cls: Type[BrokerAdapter]) -> Type[BrokerAdapter]:
        cls.broker_type = name
        _registry[key] = cls
        return cls

    return _decorator


def get_broker_adapter_class(broker_type: str) -> Type[BrokerAdapter]:
    key = broker_type.lower()
    if key not in _registry:
        raise UnsupportedBrokerError(
            f"Unsupported broker '{broker_type}'. Available: {sorted(_registry)}",
            details={"available": sorted(_registry)},
        )
    return _registry[key]


def get_broker_adapter(
    broker_type: str, credentials: dict[str, Any], *, alias: str = ""
) -> BrokerAdapter:
    cls = get_broker_adapter_class(broker_type)
    cls.validate_credentials(credentials)
    return cls(credentials, alias=alias)


def list_brokers() -> list[str]:
    return sorted(_registry.keys())
