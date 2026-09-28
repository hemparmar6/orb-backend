"""Broker abstraction layer (Module 3 + Module 6 backend integration).

Public API:
- ``BrokerAdapter``       — abstract interface every broker (mock/live/real) implements
- ``BrokerOrderRequest``  — normalised order request DTO
- ``BrokerOrderResult``   — normalised order status DTO
- ``BrokerFunds``, ``BrokerPositionSnapshot``
- ``register_broker`` / ``get_broker_adapter`` — registry (adapters register themselves on import)

Ships three adapters (registered on import of this package):
- ``mock_live``  — fully working simulated broker for tests
- ``dhan``       — Dhan v2 REST + WS order-update stream (production)
- ``kotak_neo``  — Kotak Neo trade API REST + WS order feed (production)
"""

from app.brokers.base import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from app.brokers.registry import (
    get_broker_adapter,
    list_brokers,
    register_broker,
)

# Ship adapters — importing them triggers @register_broker.
from app.brokers import dhan, kotak_neo, mock_live  # noqa: F401,E402

__all__ = [
    "BrokerAdapter",
    "BrokerOrderRequest",
    "BrokerOrderResult",
    "BrokerOrderStatus",
    "BrokerFunds",
    "BrokerPositionSnapshot",
    "register_broker",
    "get_broker_adapter",
    "list_brokers",
]
