"""WS /ws/v1/notifications — real-time notification stream (Module 8).

Broadcasts newly-created notifications (via NotificationService) to the
authenticated user's connected clients.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, WebSocket
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_deps import authenticate_ws
from app.core.exceptions import (
    AppError,
    InactiveUserError,
    InvalidTokenError,
    UnauthorizedError,
)
from app.core.logging import get_logger
from app.db.session import get_db
from app.ws.notification_broadcaster import notification_broadcaster
from app.ws.protocol import error_frame, frame

logger = get_logger(__name__)
router = APIRouter()

CLOSE_AUTH_FAILED = 4401
CLOSE_INTERNAL = 4500


@router.websocket("/notifications")
async def notifications_ws(ws: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    await ws.accept()
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

    queue = await notification_broadcaster.subscribe(user.id)
    logger.info("ws_notifications_connect", extra={"user_id": user.id})

    async def _sender() -> None:
        while True:
            payload = await queue.get()
            await ws.send_json(frame("notification", payload))

    async def _receiver() -> None:
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get("action") == "ping":
                await ws.send_json(frame("pong"))

    sender_task = asyncio.create_task(_sender(), name=f"ws-notif-send-{user.id}")
    receiver_task = asyncio.create_task(_receiver(), name=f"ws-notif-recv-{user.id}")

    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.warning("ws_notifications_task_error", extra={"error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await notification_broadcaster.unsubscribe(user.id, queue)
        logger.info("ws_notifications_disconnect", extra={"user_id": user.id})
