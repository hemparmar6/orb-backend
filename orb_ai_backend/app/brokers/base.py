"""BrokerAdapter — the sole surface the trading engine sees for every broker.

Design rules the engine relies on:
- All methods are async.
- Every method translates its raw broker response into the normalised DTOs
  in this file. The engine never sees raw broker payloads.
- ``client_order_id`` is our internal ``PaperOrder.id``; adapters MUST echo it
  back on ``BrokerOrderResult`` so we can correlate updates from WS streams.
- ``stream_order_updates()`` yields the SAME DTO shape as ``get_order()``.

Adding a new broker:
1. Subclass ``BrokerAdapter``.
2. Decorate the class with ``@register_broker("some_name")``.
3. Import it from ``app/brokers/__init__.py`` so the decorator runs.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


# ---- Enums ---------------------------------------------------------------


class BrokerOrderStatus(str, enum.Enum):
    """Normalised order status across brokers."""

    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ---- DTOs ----------------------------------------------------------------


@dataclass(slots=True)
class BrokerOrderRequest:
    """What the engine hands to ``BrokerAdapter.place_order``."""

    client_order_id: str            # our PaperOrder.id — for idempotency + correlation
    symbol: str
    exchange: str
    side: str                       # "buy" | "sell"
    order_type: str                 # "market" | "limit" | "sl" | "sl_m"
    product: str                    # "mis" | "cnc" | "nrml"
    quantity: float
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    tag: Optional[str] = None


@dataclass(slots=True)
class BrokerOrderResult:
    """Normalised broker order status. All adapters MUST return this shape."""

    broker_order_id: str
    status: BrokerOrderStatus
    filled_quantity: float = 0.0
    average_fill_price: Optional[float] = None
    rejection_reason: Optional[str] = None
    client_order_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrokerFunds:
    available: float
    used: float = 0.0
    total: float = 0.0
    currency: str = "INR"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrokerPositionSnapshot:
    symbol: str
    exchange: str
    product: str
    net_quantity: float
    average_price: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    last_price: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---- BrokerAdapter -------------------------------------------------------


class BrokerAdapter(ABC):
    """Abstract broker adapter."""

    # Set by @register_broker at class definition time.
    broker_type: str = "abstract"

    def __init__(self, credentials: dict[str, Any], *, alias: str = "") -> None:
        self.credentials = credentials
        self.alias = alias

    # ---- Static / class-level introspection ---------------------------

    @classmethod
    @abstractmethod
    def required_credentials(cls) -> list[str]:
        """Ordered list of credential keys this adapter needs.

        Used by ``POST /brokers/connect`` to validate the client payload
        before we encrypt & persist it.
        """

    @classmethod
    def validate_credentials(cls, creds: dict[str, Any]) -> None:
        """Raise ``BrokerCredentialsInvalidError`` if ``creds`` is malformed.

        Default implementation just checks that ``required_credentials()`` are
        present and non-empty. Subclasses may override for deeper checks.
        """
        from app.core.exceptions import BrokerCredentialsInvalidError

        missing = [k for k in cls.required_credentials() if not creds.get(k)]
        if missing:
            raise BrokerCredentialsInvalidError(
                f"Missing credentials for {cls.broker_type}: {missing}",
                details={"missing": missing},
            )

    # ---- Lifecycle ----------------------------------------------------

    async def start(self) -> None:  # pragma: no cover - override if needed
        """Open sockets, warm caches, etc. Default no-op."""

    async def stop(self) -> None:  # pragma: no cover
        """Release resources. Default no-op."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the broker responded to a lightweight ping."""

    # ---- Order lifecycle ---------------------------------------------

    @abstractmethod
    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderResult: ...

    @abstractmethod
    async def modify_order(
        self,
        broker_order_id: str,
        *,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> BrokerOrderResult: ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult: ...

    @abstractmethod
    async def get_order(self, broker_order_id: str) -> BrokerOrderResult: ...

    @abstractmethod
    async def list_orders(self) -> list[BrokerOrderResult]: ...

    # ---- Account state -----------------------------------------------

    @abstractmethod
    async def get_funds(self) -> BrokerFunds: ...

    @abstractmethod
    async def list_positions(self) -> list[BrokerPositionSnapshot]: ...

    # ---- Real-time order updates -------------------------------------

    def stream_order_updates(self) -> AsyncIterator[BrokerOrderResult]:  # pragma: no cover
        """Async iterator of order-status events.

        Adapters that do not support WebSocket updates SHOULD NOT override
        this — the ``BrokerOrderStream`` orchestrator will fall back to
        polling ``get_order()`` for every open order.
        """
        raise NotImplementedError("This adapter does not support WebSocket order streaming")
