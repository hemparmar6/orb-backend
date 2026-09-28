"""Market data package — abstract provider + in-memory mock + historical candles.

Real-time broker providers (``dhan``, ``kotak_neo``) live alongside the mock
and register themselves via ``@register_provider`` on import.

Historical candle fetchers (``synthetic``, ``dhan``, ``kotak_neo``) are
registered via ``@register_historical`` in ``historical_base.py`` and its
broker-specific siblings.
"""

from app.engine.market_data.base import (
    Candle,
    Interval,
    MarketDataProvider,
    Quote,
)
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider
from app.engine.market_data.historical import HistoricalCandleProvider
from app.engine.market_data.historical_base import (
    HistoricalCandleFetcher,
    SyntheticHistoricalFetcher,
    get_historical,
    list_historical,
    register_historical,
)
from app.engine.market_data.mock import MockMarketDataProvider
from app.engine.market_data.registry import get_provider, register_provider

# Import the concrete broker providers so their @register_provider /
# @register_historical side-effects populate the registries. Kept at the
# bottom to avoid circular imports.
from app.engine.market_data.dhan import DhanMarketDataProvider  # noqa: E402,F401
from app.engine.market_data.dhan_historical import DhanHistoricalFetcher  # noqa: E402,F401
from app.engine.market_data.kotak_neo import KotakNeoMarketDataProvider  # noqa: E402,F401
from app.engine.market_data.kotak_neo_historical import (  # noqa: E402,F401
    KotakNeoHistoricalFetcher,
)
from app.engine.market_data.upstox import UpstoxMarketDataProvider  # noqa: E402,F401
from app.engine.market_data.upstox_historical import (  # noqa: E402,F401
    UpstoxHistoricalFetcher,
)

__all__ = [
    "Quote",
    "Candle",
    "Interval",
    "MarketDataProvider",
    "MockMarketDataProvider",
    "HistoricalCandleProvider",
    "BrokerWSMarketDataProvider",
    "DhanMarketDataProvider",
    "KotakNeoMarketDataProvider",
    "UpstoxMarketDataProvider",
    "HistoricalCandleFetcher",
    "SyntheticHistoricalFetcher",
    "DhanHistoricalFetcher",
    "KotakNeoHistoricalFetcher",
    "UpstoxHistoricalFetcher",
    "get_provider",
    "register_provider",
    "get_historical",
    "list_historical",
    "register_historical",
]
