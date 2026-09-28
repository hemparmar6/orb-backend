"""Broker instrument masters — DTO + registry + background refresh.

An **instrument master** is the daily list of tradable symbols a broker
publishes: security_id/token per tradable symbol, exchange segment, lot
size, tick size, expiry (for F&O), strike + option type (for options),
etc.

Both real-time and historical market-data providers accept a
``symbol_map`` mapping ``user_symbol -> (token, segment)``. Building this
map by hand for every trading day is fragile; this package makes it a
one-liner:

    loader = get_loader("dhan", credentials={...})
    master = await loader.fetch()
    provider.add_symbols(master.to_ws_symbol_map())        # WS provider
    fetcher.add_symbols(master.to_historical_symbol_map()) # historical

For long-lived processes, wrap the loader in a ``RefreshScheduler`` to
reload the master daily in the background.

The Kotak Neo access session (``sid``) expires daily. The related
:class:`app.brokers.auth_refresh.KotakSidRefreshScheduler` re-runs the
OAuth+login flow before market-open so both providers keep working
without operator intervention.
"""

from app.brokers.instruments.base import (
    Instrument,
    InstrumentKind,
    InstrumentMaster,
)
from app.brokers.instruments.loader_base import (
    CachedInstrumentLoader,
    InstrumentLoader,
    RefreshScheduler,
    get_loader,
    list_loaders,
    register_loader,
)

# Concrete loaders register themselves on import.
from app.brokers.instruments.dhan import DhanInstrumentLoader  # noqa: E402,F401
from app.brokers.instruments.kotak_neo import KotakInstrumentLoader  # noqa: E402,F401
from app.brokers.instruments.upstox import (  # noqa: E402,F401
    UpstoxInstrumentLoader,
    upstox_symbol_map,
)

__all__ = [
    "Instrument",
    "InstrumentKind",
    "InstrumentMaster",
    "InstrumentLoader",
    "CachedInstrumentLoader",
    "RefreshScheduler",
    "get_loader",
    "list_loaders",
    "register_loader",
    "DhanInstrumentLoader",
    "KotakInstrumentLoader",
    "UpstoxInstrumentLoader",
    "upstox_symbol_map",
]
