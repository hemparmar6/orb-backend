"""WS /ws/v1/orders — live order updates for the authed user's engine sessions.

Consumes the Redis ``orders:{session_id}`` channel that Module 3's
``BrokerOrderStream`` publishes to. Auto-subscribes the client to every
engine session that belongs to the authed user at connect time; the client
can then narrow via ``{action: "subscribe|unsubscribe", channel: "orders", session_ids: [...]}``.

If Redis is not available, the connection is closed with 4501.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, WebSocket
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_deps import authenticate_ws
from app.core.exceptions import (
    AppError,
    EngineError,
    InactiveUserError,
    InvalidTokenError,
    UnauthorizedError,
)
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.engine import EngineSession
from app.ws.order_broadcaster import order_broadcaster
from app.ws.protocol import ack_frame, error_frame, frame, parse_client_message
from app.ws.rate_limit import default_bucket

logger = get_logger(__name__)
router = APIRouter()

CLOSE_AUTH_FAILED = 4401
CLOSE_RATE_LIMITED = 4429
CLOSE_DEPENDENCY_UNAVAILABLE = 4501
CLOSE_INTERNAL = 4500


async def _list_user_session_ids(db: AsyncSession, user_id: str) -> list[str]:
    stmt = select(EngineSession.id).where(EngineSession.user_id == user_id)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.websocket("/orders")
async def orders_ws(ws: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
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

    # ---- Ensure Redis is available ------------------------------------
    try:
        await order_broadcaster.ensure_started()
    except EngineError as e:
        await ws.send_json(error_frame(e.code, e.message))
        await ws.close(code=CLOSE_DEPENDENCY_UNAVAILABLE)
        return

    # ---- Register + auto-subscribe to the user's existing sessions ----
    client_id = id(ws)
    queue = await order_broadcaster.connect(client_id)
    initial_sessions = await _list_user_session_ids(db, user.id)
    if initial_sessions:
        await order_broadcaster.subscribe(client_id, initial_sessions)
    bucket = default_bucket()
    logger.info(
        "ws_orders_connect",
        extra={"user_id": user.id, "client_id": client_id, "sessions": initial_sessions},
    )

    async def _sender() -> None:
        while True:
            payload = await queue.get()
            await ws.send_json(frame("order_update", payload))

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
            if parsed.channel and parsed.channel != "orders":
                await ws.send_json(error_frame("wrong_channel", "This socket only serves 'orders'"))
                continue
            # Only allow sessions this user owns.
            allowed = set(await _list_user_session_ids(db, user.id))
            targeted = [sid for sid in parsed.session_ids if sid in allowed]
            if parsed.action == "subscribe":
                current = await order_broadcaster.subscribe(client_id, targeted)
                await ws.send_json(ack_frame("orders", "subscribe", session_ids=current))
            elif parsed.action == "unsubscribe":
                current = await order_broadcaster.unsubscribe(client_id, targeted)
                await ws.send_json(ack_frame("orders", "unsubscribe", session_ids=current))

    sender_task = asyncio.create_task(_sender(), name=f"ws-orders-send-{client_id}")
    receiver_task = asyncio.create_task(_receiver(), name=f"ws-orders-recv-{client_id}")

    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.warning("ws_orders_task_error", extra={"error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await order_broadcaster.disconnect(client_id)
        logger.info("ws_orders_disconnect", extra={"user_id": user.id, "client_id": client_id})
