"""WS /ws/v1/quotes — live market-data ticks via the shared QuoteBroadcaster.

Flow per connection:
    1. Accept the WebSocket.
    2. Authenticate via ?token=<JWT>. On failure, close 4401.
    3. Register a client queue with the broadcaster.
    4. Concurrently:
       - Read inbound commands (subscribe / unsubscribe / ping) — rate-limited.
       - Push queue items outbound as {"type": "quote", "data": {...}}.
    5. Clean up on disconnect.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_deps import authenticate_ws
from app.core.config import settings
from app.core.exceptions import (
    AppError,
    InactiveUserError,
    InvalidTokenError,
    UnauthorizedError,
)
from app.core.logging import get_logger
from app.db.session import get_db
from app.engine.market_data.base import Quote
from app.ws.protocol import ack_frame, error_frame, frame, parse_client_message
from app.ws.quote_broadcaster import quote_broadcaster
from app.ws.rate_limit import default_bucket

logger = get_logger(__name__)
router = APIRouter()

# App-defined close codes (RFC 6455 4000-4999 range).
CLOSE_AUTH_FAILED = 4401
CLOSE_RATE_LIMITED = 4429
CLOSE_INTERNAL = 4500
# Live market-data safety: the socket is refused (never serves mock ticks)
# whenever a real market-data provider is selected. See _provider_is_mock().
CLOSE_PROVIDER_UNAVAILABLE = 4503


def _provider_is_mock() -> bool:
    """True only when MARKET_DATA_PROVIDER is the deterministic mock provider.

    The quote broadcaster is backed exclusively by ``MockMarketDataProvider``.
    Fanning those ticks out while a REAL provider (e.g. ``upstox``) is selected
    would serve simulated prices as if they were live market data. This helper
    is the single gate that decides whether the mock-backed stream may run.
    """
    return (settings.MARKET_DATA_PROVIDER or "").strip().lower() == "mock"


@router.websocket("/quotes")
async def quotes_ws(ws: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    await ws.accept()

    # ---- Auth ---------------------------------------------------------
    try:
        user = await authenticate_ws(ws, db)
    except (UnauthorizedError, InvalidTokenError, InactiveUserError) as e:
        await ws.send_json(error_frame(e.code, e.message))
        await ws.close(code=CLOSE_AUTH_FAILED)
        return
    except AppError as e:
        await ws.send_json(error_frame(e.code, e.message))
        await ws.close(code=CLOSE_INTERNAL)
        return

    # ---- Live market-data safety gate (fail closed) -------------------
    # The broadcaster only ever emits deterministic MOCK ticks. When a real
    # provider is configured we MUST NOT fan those out as if they were live —
    # refuse the stream with a clear error and application close code 4503
    # instead of degrading to mock. Mock streaming stays available only when
    # MARKET_DATA_PROVIDER=mock (dev preview / testing).
    if not _provider_is_mock():
        await ws.send_json(
            error_frame(
                "market_data_provider_unavailable",
                "Live market-data streaming is unavailable for the configured "
                "provider; refusing to emit mock ticks.",
            )
        )
        await ws.close(code=CLOSE_PROVIDER_UNAVAILABLE)
        logger.info(
            "ws_quotes_provider_unavailable",
            extra={"user_id": user.id, "provider": settings.MARKET_DATA_PROVIDER},
        )
        return

    # ---- Register with the broadcaster --------------------------------
    await quote_broadcaster.start()
    client_id = id(ws)
    queue = await quote_broadcaster.connect(client_id)
    bucket = default_bucket()
    logger.info("ws_quotes_connect", extra={"user_id": user.id, "client_id": client_id})

    async def _sender() -> None:
        while True:
            quote: Quote = await queue.get()
            await ws.send_json(
                frame(
                    "quote",
                    {
                        "symbol": quote.symbol,
                        "exchange": quote.exchange,
                        "price": float(quote.price),
                        "volume": float(quote.volume),
                        "ts": quote.ts.isoformat() if isinstance(quote.ts, datetime) else quote.ts,
                    },
                )
            )

    async def _receiver() -> None:
        while True:
            payload = await ws.receive_json()
            if not bucket.consume():
                await ws.send_json(error_frame("rate_limited", "Too many messages"))
                await ws.close(code=CLOSE_RATE_LIMITED)
                return
            parsed = parse_client_message(payload)
            if not hasattr(parsed, "action"):
                await ws.send_json(error_frame("invalid_message", "Malformed client message"))
                continue
            if parsed.action == "ping":
                await ws.send_json(frame("pong"))
                continue
            if parsed.channel and parsed.channel != "quotes":
                await ws.send_json(error_frame("wrong_channel", "This socket only serves 'quotes'"))
                continue
            if parsed.action == "subscribe":
                current = await quote_broadcaster.subscribe(client_id, parsed.symbols)
                await ws.send_json(ack_frame("quotes", "subscribe", symbols=current))
            elif parsed.action == "unsubscribe":
                current = await quote_broadcaster.unsubscribe(client_id, parsed.symbols)
                await ws.send_json(ack_frame("quotes", "unsubscribe", symbols=current))

    sender_task = asyncio.create_task(_sender(), name=f"ws-quotes-send-{client_id}")
    receiver_task = asyncio.create_task(_receiver(), name=f"ws-quotes-recv-{client_id}")

    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in pending:
            t.cancel()
        # Surface the terminating exception if any (for logging).
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.warning("ws_quotes_task_error", extra={"error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await quote_broadcaster.disconnect(client_id)
        logger.info("ws_quotes_disconnect", extra={"user_id": user.id, "client_id": client_id})
