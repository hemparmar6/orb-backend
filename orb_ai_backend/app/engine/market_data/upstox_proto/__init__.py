"""Vendored Upstox Market Data Feed V3 protobuf reference.

The authoritative wire schema lives in ``MarketDataFeed.proto`` (copied verbatim
from ``https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto``).

We intentionally do **not** ship a generated ``*_pb2.py`` module: the generated
code pins a specific ``protobuf`` runtime version and would add a heavyweight,
version-fragile dependency to the backend. Instead
:mod:`app.engine.market_data.upstox_protobuf` implements a small, dependency-free
decoder/encoder for exactly the fields ORB consumes (LTPC, OHLC, timestamps,
volume). The ``.proto`` file is kept here purely as documentation and as the
source of truth should we ever want to regenerate bindings.
"""
