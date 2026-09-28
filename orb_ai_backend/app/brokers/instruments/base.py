"""Instrument DTO + immutable in-memory master.

The master is populated by an :class:`InstrumentLoader` from broker CSV/JSON
downloads, then queried by strategies + engine wiring code. Two convenience
methods emit exactly the ``symbol_map`` tuple format the WS providers and
the historical fetchers expect, so no manual translation is needed.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional


class InstrumentKind(str, enum.Enum):
    EQUITY = "EQUITY"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    CURRENCY = "CURRENCY"
    COMMODITY = "COMMODITY"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class Instrument:
    """One tradable instrument line from a broker's master."""

    symbol: str
    """Trading symbol (broker's ``tradingsymbol`` / ``trading_symbol``)."""

    token: str
    """Broker-specific numeric identifier (``security_id`` / scrip token)."""

    exchange_segment: str
    """Broker-specific segment string (``NSE_EQ``, ``nse_cm``, …)."""

    exchange: str = "NSE"
    """Canonical exchange (``NSE``, ``BSE``, ``MCX``, …)."""

    kind: InstrumentKind = InstrumentKind.EQUITY
    instrument_type: Optional[str] = None
    """Broker-native instrument type (for example ``FUTIDX`` or ``OPTSTK``)."""
    lot_size: int = 1
    tick_size: float = 0.05
    isin: Optional[str] = None
    underlying: Optional[str] = None
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None  # "CE" | "PE" | None


@dataclass(slots=True)
class InstrumentMaster:
    """Immutable-ish snapshot of a broker's instrument universe.

    Not thread-safe for mutation; callers that need atomic swaps should
    construct a new master and swap the reference.
    """

    broker: str
    instruments: list[Instrument] = field(default_factory=list)
    loaded_at: float = field(default_factory=time.time)

    # Populated by ``_rebuild_indexes``.
    _by_symbol: dict[tuple[str, str], Instrument] = field(default_factory=dict, init=False)
    _by_token: dict[tuple[str, str], Instrument] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._rebuild_indexes()

    # ------------------------------------------------------------ lookups

    def lookup(
        self,
        symbol: str,
        *,
        exchange: Optional[str] = None,
    ) -> Optional[Instrument]:
        """Find by symbol, optionally scoped to an exchange.

        When ``exchange`` is None, returns the first match across any
        exchange (deterministic — insertion order is preserved).
        """
        if exchange:
            return self._by_symbol.get((symbol, exchange))
        for (sym, _exch), inst in self._by_symbol.items():
            if sym == symbol:
                return inst
        return None

    def lookup_by_token(self, token: str, segment: str) -> Optional[Instrument]:
        return self._by_token.get((str(token), segment))

    def filter_by_kind(self, kind: InstrumentKind) -> list[Instrument]:
        return [i for i in self.instruments if i.kind == kind]

    def filter_by_segment(self, segment: str) -> list[Instrument]:
        return [i for i in self.instruments if i.exchange_segment == segment]

    # ---------------------------------------------------- symbol_map exports

    def to_ws_symbol_map(
        self,
        symbols: Optional[Iterable[str]] = None,
        *,
        exchange: Optional[str] = None,
    ) -> dict[str, tuple[str, str]]:
        """Emit ``{user_symbol: (token, exchange_segment)}`` for the WS + REST
        market-data providers.

        When ``symbols`` is ``None``, every instrument in the master is
        returned (may be very large — prefer to pass an explicit list for
        production use).
        """
        wanted = set(symbols) if symbols is not None else None
        out: dict[str, tuple[str, str]] = {}
        for inst in self.instruments:
            if wanted is not None and inst.symbol not in wanted:
                continue
            if exchange and inst.exchange != exchange:
                continue
            # First occurrence wins so a follow-up "wanted" symbol keeps its
            # earliest listing (equity before F&O for the same underlying).
            out.setdefault(inst.symbol, (inst.token, inst.exchange_segment))
        return out

    # Historical fetchers use the exact same shape — kept as an alias for
    # readability at call sites.
    to_historical_symbol_map = to_ws_symbol_map

    # --------------------------------------------------------------- stats

    def stats(self) -> dict[str, int | float | str]:
        by_kind: dict[str, int] = {}
        by_segment: dict[str, int] = {}
        for i in self.instruments:
            by_kind[i.kind.value] = by_kind.get(i.kind.value, 0) + 1
            by_segment[i.exchange_segment] = by_segment.get(i.exchange_segment, 0) + 1
        return {
            "broker": self.broker,
            "loaded_at": self.loaded_at,
            "total": len(self.instruments),
            **{f"by_kind:{k}": v for k, v in by_kind.items()},
            **{f"by_segment:{k}": v for k, v in by_segment.items()},
        }

    # -------------------------------------------------------------- private

    def _rebuild_indexes(self) -> None:
        by_sym: dict[tuple[str, str], Instrument] = {}
        by_tok: dict[tuple[str, str], Instrument] = {}
        for i in self.instruments:
            by_sym.setdefault((i.symbol, i.exchange), i)
            by_tok.setdefault((str(i.token), i.exchange_segment), i)
        self._by_symbol = by_sym
        self._by_token = by_tok
