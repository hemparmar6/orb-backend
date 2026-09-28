"""Dependency-free decoder for the Upstox Market Data Feed **V3** protobuf.

Upstox streams market data as binary Protobuf ``FeedResponse`` messages over its
V3 WebSocket. Rather than pull in the ``protobuf`` runtime (whose generated
bindings pin a specific runtime version), this module parses the small subset of
the schema ORB actually needs, straight off the wire.

Schema (see ``upstox_proto/MarketDataFeed.proto`` for the verbatim source)::

    FeedResponse { Type type=1; map<string,Feed> feeds=2; int64 currentTs=3; ... }
    Feed { LTPC ltpc=1; FullFeed fullFeed=2; FirstLevelWithGreeks flwg=3; RequestMode requestMode=4; }
    FullFeed { oneof { MarketFullFeed marketFF=1; IndexFullFeed indexFF=2; } }
    MarketFullFeed { LTPC ltpc=1; ...; MarketOHLC marketOHLC=4; int64 vtt=6; ... }
    IndexFullFeed { LTPC ltpc=1; MarketOHLC marketOHLC=2; }
    FirstLevelWithGreeks { LTPC ltpc=1; ...; int64 vtt=4; ... }
    MarketOHLC { repeated OHLC ohlc=1; }
    OHLC { string interval=1; double open=2; high=3; low=4; close=5; int64 vol=6; int64 ts=7; }
    LTPC { double ltp=1; int64 ltt=2; int64 ltq=3; double cp=4; }

The public surface:

- :func:`decode_feed_response` — bytes → ``list[UpstoxTick]`` (normalised).
- :func:`build_feed_response` and the ``ltpc_feed`` / ``market_full_feed`` /
  ``index_full_feed`` helpers — used by tests to synthesise deterministic
  frames without hitting Upstox (no credentials required).

The decoder is defensive: any structurally invalid buffer raises
:class:`UpstoxProtobufError`, which callers translate into a counted decode
error (never a crash, never a silent fake tick).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

# Protobuf wire types we support.
_WT_VARINT = 0
_WT_FIXED64 = 1
_WT_LEN = 2
_WT_FIXED32 = 5


class UpstoxProtobufError(ValueError):
    """Raised when a buffer cannot be parsed as the expected schema."""


@dataclass(frozen=True, slots=True)
class UpstoxOHLC:
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    ts: int  # epoch ms


@dataclass(frozen=True, slots=True)
class UpstoxTick:
    """One normalised feed entry for a single instrument key."""

    instrument_key: str
    ltp: float
    ltt_ms: int              # last-traded time (epoch ms); 0 if absent
    ltq: int                 # last-traded quantity
    cp: float                # previous close price
    volume: int              # daily cumulative volume where available, else ltq
    ohlc: dict[str, UpstoxOHLC] = field(default_factory=dict)
    feed_ts_ms: int = 0      # FeedResponse.currentTs (epoch ms); 0 if absent
    mode: str = ""           # "ltpc" | "full" | "firstLevelWithGreeks"


# ---------------------------------------------------------------------------
# Low-level varint / field reader
# ---------------------------------------------------------------------------


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    n = len(buf)
    while True:
        if pos >= n:
            raise UpstoxProtobufError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise UpstoxProtobufError("varint too long")


def _parse_fields(buf: bytes) -> dict[int, list[tuple[int, object]]]:
    """Parse a protobuf message body into ``{field_no: [(wire_type, value)]}``.

    ``value`` is an ``int`` for varints, raw ``bytes`` for fixed64/fixed32 and
    length-delimited fields.
    """
    fields: dict[int, list[tuple[int, object]]] = {}
    pos = 0
    n = len(buf)
    while pos < n:
        tag, pos = _read_varint(buf, pos)
        field_no = tag >> 3
        wt = tag & 0x07
        if wt == _WT_VARINT:
            val, pos = _read_varint(buf, pos)
        elif wt == _WT_FIXED64:
            if pos + 8 > n:
                raise UpstoxProtobufError("truncated fixed64")
            val = buf[pos:pos + 8]
            pos += 8
        elif wt == _WT_LEN:
            ln, pos = _read_varint(buf, pos)
            if pos + ln > n:
                raise UpstoxProtobufError("truncated length-delimited field")
            val = buf[pos:pos + ln]
            pos += ln
        elif wt == _WT_FIXED32:
            if pos + 4 > n:
                raise UpstoxProtobufError("truncated fixed32")
            val = buf[pos:pos + 4]
            pos += 4
        else:
            raise UpstoxProtobufError(f"unsupported wire type {wt}")
        fields.setdefault(field_no, []).append((wt, val))
    return fields


def _first(fields: dict[int, list[tuple[int, object]]], no: int):
    lst = fields.get(no)
    return lst[0][1] if lst else None


def _double(fields: dict[int, list[tuple[int, object]]], no: int) -> Optional[float]:
    raw = _first(fields, no)
    if raw is None:
        return None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 8:
        raise UpstoxProtobufError(f"field {no} is not a valid double")
    return struct.unpack("<d", raw)[0]


def _varint(fields: dict[int, list[tuple[int, object]]], no: int) -> Optional[int]:
    raw = _first(fields, no)
    if raw is None:
        return None
    if not isinstance(raw, int):
        raise UpstoxProtobufError(f"field {no} is not a varint")
    return raw


def _submsg(fields: dict[int, list[tuple[int, object]]], no: int):
    raw = _first(fields, no)
    if raw is None:
        return None
    if not isinstance(raw, (bytes, bytearray)):
        raise UpstoxProtobufError(f"field {no} is not a message")
    return _parse_fields(bytes(raw))


def _string(fields: dict[int, list[tuple[int, object]]], no: int) -> str:
    raw = _first(fields, no)
    if raw is None:
        return ""
    if not isinstance(raw, (bytes, bytearray)):
        raise UpstoxProtobufError(f"field {no} is not a string")
    return bytes(raw).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Schema-aware decoders
# ---------------------------------------------------------------------------


def _decode_ltpc(msg: dict) -> tuple[float, int, int, float]:
    ltp = _double(msg, 1) or 0.0
    ltt = _varint(msg, 2) or 0
    ltq = _varint(msg, 3) or 0
    cp = _double(msg, 4) or 0.0
    return ltp, ltt, ltq, cp


def _decode_ohlc_list(market_ohlc: Optional[dict]) -> dict[str, UpstoxOHLC]:
    out: dict[str, UpstoxOHLC] = {}
    if not market_ohlc:
        return out
    for wt, raw in market_ohlc.get(1, []):
        if wt != _WT_LEN or not isinstance(raw, (bytes, bytearray)):
            continue
        o = _parse_fields(bytes(raw))
        candle = UpstoxOHLC(
            interval=_string(o, 1),
            open=_double(o, 2) or 0.0,
            high=_double(o, 3) or 0.0,
            low=_double(o, 4) or 0.0,
            close=_double(o, 5) or 0.0,
            volume=_varint(o, 6) or 0,
            ts=_varint(o, 7) or 0,
        )
        out[candle.interval or f"idx{len(out)}"] = candle
    return out


def _resolve_volume(daily_volume: Optional[int], ohlc: dict[str, UpstoxOHLC], ltq: int) -> int:
    if daily_volume:
        return int(daily_volume)
    # Prefer a daily candle's cumulative volume when present.
    day = ohlc.get("1d") or ohlc.get("I1")
    if day and day.volume:
        return int(day.volume)
    for candle in ohlc.values():
        if candle.volume:
            return int(candle.volume)
    return int(ltq or 0)


def _decode_feed(instrument_key: str, feed: dict, feed_ts_ms: int) -> UpstoxTick:
    ltpc_msg = _submsg(feed, 1)
    full_feed = _submsg(feed, 2)
    flwg = _submsg(feed, 3)

    ohlc: dict[str, UpstoxOHLC] = {}
    daily_volume: Optional[int] = None
    mode = "ltpc"

    if ltpc_msg is not None:
        mode = "ltpc"
    elif full_feed is not None:
        mode = "full"
        market_ff = _submsg(full_feed, 1)
        index_ff = _submsg(full_feed, 2)
        if market_ff is not None:
            ltpc_msg = _submsg(market_ff, 1)
            ohlc = _decode_ohlc_list(_submsg(market_ff, 4))
            daily_volume = _varint(market_ff, 6)  # vtt: volume traded today
        elif index_ff is not None:
            ltpc_msg = _submsg(index_ff, 1)
            ohlc = _decode_ohlc_list(_submsg(index_ff, 2))
    elif flwg is not None:
        mode = "firstLevelWithGreeks"
        ltpc_msg = _submsg(flwg, 1)
        daily_volume = _varint(flwg, 4)  # vtt

    if ltpc_msg is None:
        # A feed with no LTPC (e.g. pure market-info) carries no price.
        return UpstoxTick(
            instrument_key=instrument_key,
            ltp=0.0, ltt_ms=0, ltq=0, cp=0.0, volume=0,
            ohlc=ohlc, feed_ts_ms=feed_ts_ms, mode=mode,
        )

    ltp, ltt, ltq, cp = _decode_ltpc(ltpc_msg)
    volume = _resolve_volume(daily_volume, ohlc, ltq)
    return UpstoxTick(
        instrument_key=instrument_key,
        ltp=ltp, ltt_ms=ltt, ltq=ltq, cp=cp, volume=volume,
        ohlc=ohlc, feed_ts_ms=feed_ts_ms, mode=mode,
    )


def decode_feed_response(raw: bytes) -> list[UpstoxTick]:
    """Decode a raw Upstox V3 ``FeedResponse`` into normalised ticks.

    Returns an empty list for control/heartbeat frames that carry no feeds.
    Raises :class:`UpstoxProtobufError` for structurally invalid buffers.
    """
    if raw is None:
        raise UpstoxProtobufError("empty frame")
    if not isinstance(raw, (bytes, bytearray)):
        raise UpstoxProtobufError("Upstox frames must be binary protobuf")
    top = _parse_fields(bytes(raw))
    feed_ts_ms = _varint(top, 3) or 0

    ticks: list[UpstoxTick] = []
    for wt, entry_raw in top.get(2, []):  # map<string,Feed> feeds
        if wt != _WT_LEN or not isinstance(entry_raw, (bytes, bytearray)):
            continue
        entry = _parse_fields(bytes(entry_raw))
        instrument_key = _string(entry, 1)
        feed_msg = _submsg(entry, 2)
        if not instrument_key or feed_msg is None:
            continue
        ticks.append(_decode_feed(instrument_key, feed_msg, feed_ts_ms))
    return ticks


# ---------------------------------------------------------------------------
# Encoders — used by tests to synthesise deterministic frames (no network).
# ---------------------------------------------------------------------------


def _write_varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field_no: int, wt: int) -> bytes:
    return _write_varint((field_no << 3) | wt)


def _enc_varint(no: int, val: int) -> bytes:
    return _tag(no, _WT_VARINT) + _write_varint(val)


def _enc_double(no: int, val: float) -> bytes:
    return _tag(no, _WT_FIXED64) + struct.pack("<d", val)


def _enc_len(no: int, data: bytes) -> bytes:
    return _tag(no, _WT_LEN) + _write_varint(len(data)) + data


def _enc_string(no: int, s: str) -> bytes:
    return _enc_len(no, s.encode("utf-8"))


def _enc_ltpc(ltp: float, ltt_ms: int, ltq: int, cp: float) -> bytes:
    return (
        _enc_double(1, ltp)
        + _enc_varint(2, ltt_ms)
        + _enc_varint(3, ltq)
        + _enc_double(4, cp)
    )


def _enc_ohlc(candle: UpstoxOHLC) -> bytes:
    return (
        _enc_string(1, candle.interval)
        + _enc_double(2, candle.open)
        + _enc_double(3, candle.high)
        + _enc_double(4, candle.low)
        + _enc_double(5, candle.close)
        + _enc_varint(6, candle.volume)
        + _enc_varint(7, candle.ts)
    )


def _enc_market_ohlc(candles: list[UpstoxOHLC]) -> bytes:
    return b"".join(_enc_len(1, _enc_ohlc(c)) for c in candles)


def ltpc_feed(ltp: float, ltt_ms: int, ltq: int, cp: float = 0.0) -> bytes:
    """Bytes for a ``Feed`` carrying just an LTPC (mode=ltpc)."""
    return _enc_len(1, _enc_ltpc(ltp, ltt_ms, ltq, cp))


def market_full_feed(
    ltp: float, ltt_ms: int, ltq: int, *, cp: float = 0.0,
    vtt: int = 0, ohlc: Optional[list[UpstoxOHLC]] = None,
) -> bytes:
    """Bytes for a ``Feed`` carrying a MarketFullFeed (equity/F&O, mode=full)."""
    market_ff = _enc_len(1, _enc_ltpc(ltp, ltt_ms, ltq, cp))
    if ohlc:
        market_ff += _enc_len(4, _enc_market_ohlc(ohlc))
    if vtt:
        market_ff += _enc_varint(6, vtt)
    full_feed = _enc_len(1, market_ff)  # FullFeed.marketFF = field 1
    return _enc_len(2, full_feed)       # Feed.fullFeed = field 2


def index_full_feed(
    ltp: float, ltt_ms: int, ltq: int, *, cp: float = 0.0,
    ohlc: Optional[list[UpstoxOHLC]] = None,
) -> bytes:
    """Bytes for a ``Feed`` carrying an IndexFullFeed (NIFTY/BANKNIFTY, mode=full)."""
    index_ff = _enc_len(1, _enc_ltpc(ltp, ltt_ms, ltq, cp))
    if ohlc:
        index_ff += _enc_len(2, _enc_market_ohlc(ohlc))
    full_feed = _enc_len(2, index_ff)  # FullFeed.indexFF = field 2
    return _enc_len(2, full_feed)


def build_feed_response(
    feeds: dict[str, bytes], *, feed_ts_ms: int = 0, feed_type: int = 1,
) -> bytes:
    """Assemble a full ``FeedResponse`` from ``{instrument_key: feed_bytes}``.

    ``feed_bytes`` come from :func:`ltpc_feed` / :func:`market_full_feed` /
    :func:`index_full_feed`. ``feed_type`` defaults to 1 (live_feed).
    """
    out = bytearray()
    out += _enc_varint(1, feed_type)
    for instrument_key, feed_bytes in feeds.items():
        entry = _enc_string(1, instrument_key) + _enc_len(2, feed_bytes)
        out += _enc_len(2, entry)  # FeedResponse.feeds map entry
    if feed_ts_ms:
        out += _enc_varint(3, feed_ts_ms)
    return bytes(out)
