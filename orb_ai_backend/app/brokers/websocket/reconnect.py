"""Reconnecting broker WebSocket client.

``ReconnectingWSClient`` connects to a broker WS endpoint and yields decoded
frames as an async iterator. It handles:

- Exponential-backoff auto-reconnect on transport failure or clean close
- Optional ``on_connect`` hook to (re)send subscriptions after each reconnect
- Ping / heartbeat scheduling — the websockets library's built-in ``ping_interval``
- Graceful shutdown via ``close()``

The client is *transport-only*. Frame decoding (JSON / binary) is delegated to
the adapter via a ``decode`` callable.

Typical usage::

    async def on_connect(ws):
        await ws.send(json.dumps({"action": "subscribe", "symbols": ["NIFTY"]}))

    client = ReconnectingWSClient(
        url_provider=lambda: f"wss://feed?token={token}",
        broker="dhan",
        on_connect=on_connect,
        decode=lambda raw: json.loads(raw),
    )
    async for frame in client:
        ...  # frame is whatever decode() returned
    await client.close()
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    from websockets.asyncio.client import ClientConnection
except Exception:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[assignment,misc]
    ClientConnection = Any  # type: ignore[misc,assignment]

from app.core.logging import get_logger

logger = get_logger(__name__)


URLProvider = Callable[[], Awaitable[str] | str]
OnConnect = Callable[[Any], Awaitable[None]]
Decoder = Callable[[Any], Any]


class ReconnectingWSClient:
    """Yield decoded frames from a broker WS, reconnecting on failure."""

    def __init__(
        self,
        *,
        url_provider: URLProvider,
        broker: str,
        on_connect: Optional[OnConnect] = None,
        decode: Optional[Decoder] = None,
        ping_interval_s: Optional[float] = 20.0,
        ping_timeout_s: Optional[float] = 10.0,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 30.0,
        max_consecutive_failures: Optional[int] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        self._url_provider = url_provider
        self._broker = broker
        self._on_connect = on_connect
        self._decode = decode or (lambda raw: raw)
        self._ping_interval_s = ping_interval_s
        self._ping_timeout_s = ping_timeout_s
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._max_consecutive_failures = max_consecutive_failures
        self._extra_headers = extra_headers or {}

        self._ws: Any = None
        self._closed = asyncio.Event()
        self._consecutive_failures = 0

    async def _resolve_url(self) -> str:
        v = self._url_provider()
        if asyncio.iscoroutine(v):
            v = await v
        return str(v)

    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    async def close(self) -> None:
        self._closed.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # pragma: no cover
                pass

    async def send(self, data: Any) -> None:
        """Send a frame on the current connection (best-effort; raises if closed)."""
        if self._ws is None:
            raise RuntimeError(f"{self._broker} WS not connected")
        await self._ws.send(data)

    async def __aiter__(self) -> AsyncIterator[Any]:
        if websockets is None:  # pragma: no cover
            raise RuntimeError("websockets library not installed")
        async for frame in self._iterate():
            yield frame

    async def _iterate(self) -> AsyncIterator[Any]:
        while not self._closed.is_set():
            try:
                url = await self._resolve_url()
                logger.info(
                    "broker_ws_connecting",
                    extra={"broker": self._broker, "url": _strip_query(url)},
                )
                connect_kwargs: dict[str, Any] = {}
                if self._ping_interval_s is not None:
                    connect_kwargs["ping_interval"] = self._ping_interval_s
                if self._ping_timeout_s is not None:
                    connect_kwargs["ping_timeout"] = self._ping_timeout_s
                if self._extra_headers:
                    # websockets >= 12 uses `additional_headers`.
                    connect_kwargs["additional_headers"] = list(
                        self._extra_headers.items()
                    )

                async with websockets.connect(url, **connect_kwargs) as ws:
                    self._ws = ws
                    self._consecutive_failures = 0
                    logger.info(
                        "broker_ws_connected",
                        extra={"broker": self._broker},
                    )
                    try:
                        from app.services.broker_health import tracker as _bh
                        await _bh.on_connect(self._broker)
                    except Exception:  # pragma: no cover
                        pass
                    if self._on_connect:
                        try:
                            await self._on_connect(ws)
                        except Exception:  # pragma: no cover
                            logger.exception(
                                "broker_ws_on_connect_error",
                                extra={"broker": self._broker},
                            )
                    async for raw in ws:
                        if self._closed.is_set():
                            break
                        try:
                            from app.services.broker_health import tracker as _bh
                            await _bh.on_heartbeat(self._broker)
                        except Exception:  # pragma: no cover
                            pass
                        try:
                            decoded = self._decode(raw)
                        except Exception:  # pragma: no cover
                            logger.exception(
                                "broker_ws_decode_error",
                                extra={"broker": self._broker},
                            )
                            continue
                        if decoded is None:
                            continue
                        yield decoded
                # Normal close — loop and reconnect unless close() was called.
            except ConnectionClosed as exc:
                logger.info(
                    "broker_ws_connection_closed",
                    extra={"broker": self._broker, "code": getattr(exc, "code", None)},
                )
                try:
                    from app.services.broker_health import tracker as _bh
                    await _bh.on_disconnect(
                        self._broker,
                        reason=f"connection_closed code={getattr(exc, 'code', None)}",
                        unexpected=True,
                    )
                except Exception:  # pragma: no cover
                    pass
            except Exception as exc:
                logger.warning(
                    "broker_ws_error",
                    extra={"broker": self._broker, "error": str(exc)},
                )
                try:
                    from app.services.broker_health import tracker as _bh
                    await _bh.on_disconnect(
                        self._broker, reason=str(exc), unexpected=True,
                    )
                except Exception:  # pragma: no cover
                    pass
            finally:
                self._ws = None

            if self._closed.is_set():
                return

            self._consecutive_failures += 1
            if (
                self._max_consecutive_failures is not None
                and self._consecutive_failures >= self._max_consecutive_failures
            ):
                logger.error(
                    "broker_ws_giving_up",
                    extra={
                        "broker": self._broker,
                        "failures": self._consecutive_failures,
                    },
                )
                return

            delay = min(
                self._backoff_base_s * (2 ** (self._consecutive_failures - 1)),
                self._backoff_max_s,
            )
            await asyncio.sleep(random.uniform(0, delay))


def _strip_query(url: str) -> str:
    """Remove query string from URL so we don't log tokens."""
    q = url.find("?")
    return url[:q] if q >= 0 else url
