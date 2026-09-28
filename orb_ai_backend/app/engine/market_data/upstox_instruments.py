"""Upstox instrument-key resolution for ORB symbols.

Upstox identifies every instrument by a single opaque **instrument key** of the
form ``"<SEGMENT>|<identifier>"`` — for example:

- ``NSE_INDEX|Nifty 50``      (NIFTY spot index)
- ``NSE_INDEX|Nifty Bank``    (BANKNIFTY spot index)
- ``NSE_EQ|INE467B01029``     (TCS equity, keyed by ISIN)
- ``NSE_FO|<token>``          (F&O contracts)

ORB's user-facing symbol space (``NIFTY``, ``BANKNIFTY``, equities, F&O) must be
translated to these keys before subscribing. This module provides two layers:

1. A small **curated static map** for the index symbols ORB always trades. These
   keys are stable and published by Upstox, so we hard-map them rather than
   depending on a multi-megabyte instrument-master download being present.
2. :func:`resolve_instrument_key`, which also accepts an *already-formatted*
   Upstox instrument key (``"NSE_EQ|INE..."``) and passes it through untouched —
   so callers who resolved a key from the Upstox instrument master (see
   :class:`app.brokers.instruments.upstox.UpstoxInstrumentLoader`) can hand it
   straight to the provider.

No instrument keys are *guessed*: unknown, non-key symbols return ``None`` and
the caller must resolve them from the instrument master.
"""
from __future__ import annotations

from typing import Optional

# Curated, verified spot-index keys used across ORB (NIFTY / BANKNIFTY first).
# Keys are the exact strings Upstox expects (case + spacing significant).
_INDEX_KEYS: dict[str, str] = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "NIFTY50": "NSE_INDEX|Nifty 50",
    "NIFTY 50": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "NIFTYBANK": "NSE_INDEX|Nifty Bank",
    "NIFTY BANK": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|Nifty Midcap Select",
    "NIFTYNXT50": "NSE_INDEX|Nifty Next 50",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX",
    "INDIAVIX": "NSE_INDEX|India VIX",
}

# Exchange strings ORB uses -> whether they map onto an Upstox segment prefix.
# (Used only for validation / documentation; resolution is key-based.)
_KNOWN_SEGMENT_PREFIXES = (
    "NSE_INDEX", "BSE_INDEX", "NSE_EQ", "BSE_EQ", "NSE_FO", "BSE_FO",
    "MCX_FO", "NCD_FO", "BCD_FO",
)


def _normalise_symbol(value: str) -> str:
    """Case-fold and collapse internal whitespace for tolerant matching.

    ``"  Nifty   50 "`` and ``"NIFTY 50"`` and ``"nifty50"`` all normalise to a
    comparable form. Used only to *look up* verified keys — it never fabricates
    a key, so an unknown symbol still returns ``None``.
    """
    return "".join((value or "").split()).upper()


# Normalised-alias -> verified instrument key. Built once from the curated map
# above so resolution tolerates the exact display strings the mobile app / feed
# send (e.g. Upstox's own ``"Nifty 50"`` / ``"Nifty Bank"`` names), regardless
# of case or spacing. Every value here is a key already present in
# ``_INDEX_KEYS`` (verified against the current Upstox instrument master); no
# new/guessed keys are introduced.
def _build_normalised_index_lookup() -> dict[str, str]:
    out: dict[str, str] = {}
    for alias, key in _INDEX_KEYS.items():
        out.setdefault(_normalise_symbol(alias), key)
        # Also index by the authoritative Upstox display name — the suffix of
        # the verified instrument key (``"NSE_INDEX|Nifty 50"`` -> ``"Nifty 50"``).
        if "|" in key:
            out.setdefault(_normalise_symbol(key.split("|", 1)[1]), key)
    return out


_NORMALISED_INDEX_LOOKUP: dict[str, str] = _build_normalised_index_lookup()


def looks_like_instrument_key(value: str) -> bool:
    """True when ``value`` already looks like an Upstox instrument key."""
    if not value or "|" not in value:
        return False
    prefix = value.split("|", 1)[0].strip().upper()
    return prefix in _KNOWN_SEGMENT_PREFIXES


def resolve_instrument_key(symbol: str) -> Optional[str]:
    """Resolve an ORB symbol to an Upstox instrument key.

    Order of resolution:

    1. Exact / normalised match against the curated index map.
    2. Pass-through when ``symbol`` is already a valid Upstox instrument key.
    3. Case/whitespace-insensitive match against the curated index names
       (including Upstox's authoritative display names, e.g. ``"Nifty 50"`` /
       ``"Nifty Bank"``) — fixes ``upstox_instrument_unresolved`` when the
       client sends the display string rather than the compact ORB alias.
    4. ``None`` otherwise (caller must resolve from the instrument master).
    """
    if not symbol:
        return None
    raw = symbol.strip()
    key = _INDEX_KEYS.get(raw) or _INDEX_KEYS.get(raw.upper())
    if key:
        return key
    if looks_like_instrument_key(raw):
        return raw
    # Tolerant fallback: only ever returns a key already verified in the
    # curated map; unknown symbols still fall through to ``None``.
    normalised = _NORMALISED_INDEX_LOOKUP.get(_normalise_symbol(raw))
    if normalised:
        return normalised
    return None


def default_index_symbol_map() -> dict[str, str]:
    """``{ORB symbol: instrument_key}`` for the curated index universe.

    Keys use the canonical ORB names (``NIFTY``, ``BANKNIFTY``, …) so it can be
    fed straight into ``UpstoxMarketDataProvider(symbol_map=...)``.
    """
    canonical = {
        "NIFTY": _INDEX_KEYS["NIFTY"],
        "BANKNIFTY": _INDEX_KEYS["BANKNIFTY"],
        "FINNIFTY": _INDEX_KEYS["FINNIFTY"],
        "MIDCPNIFTY": _INDEX_KEYS["MIDCPNIFTY"],
        "NIFTYNXT50": _INDEX_KEYS["NIFTYNXT50"],
        "SENSEX": _INDEX_KEYS["SENSEX"],
        "BANKEX": _INDEX_KEYS["BANKEX"],
        "INDIAVIX": _INDEX_KEYS["INDIAVIX"],
    }
    return canonical


def build_symbol_map(symbols: dict[str, str] | None = None) -> dict[str, str]:
    """Merge the curated index map with any caller-supplied ``{symbol: key}``.

    Caller-supplied entries (typically resolved from the instrument master for
    equities / F&O) win over the curated defaults.
    """
    out = default_index_symbol_map()
    if symbols:
        for sym, key in symbols.items():
            resolved = resolve_instrument_key(key) or (
                key if looks_like_instrument_key(key) else None
            )
            if resolved:
                out[sym] = resolved
    return out
