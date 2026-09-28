"""WS /ws/v1/risk — live risk & execution-safety event stream (M9 follow-up).

Broadcasts:
    * Risk breaches (any :class:`RiskEventType`)
    * Bot auto-pause events
    * Daily-loss / max-trades / consecutive-loss limits
    * Execution-safety events (rate limits, duplicates, kill switch)
    * Broker disconnected
    * Emergency kill switch

Two connection modes based on the caller's role:

    * **User** — receives only events for their own account.
    * **Admin** — receives every event platform-wide.

Uses ``RiskBroadcaster`` (in-process singleton) same as other WS
broadcasters. No new Redis dependency; multi-worker scale-out is a
follow-up (interface is designed for it).
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
from app.models.user import UserRole
from app.ws.protocol import error_frame, frame
from app.ws.risk_broadcaster import risk_broadcaster

logger = get_logger(__name__)
router = APIRouter()

CLOSE_AUTH_FAILED = 4401
CLOSE_INTERNAL = 4500


@router.websocket("/risk")
async def risk_ws(ws: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    """Live risk events for the authenticated user (or every user if admin)."""
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

    is_admin = user.role == UserRole.ADMIN
    if is_admin:
        queue = await risk_broadcaster.subscribe_admin()
    else:
        queue = await risk_broadcaster.subscribe_user(user.id)

    logger.info(
        "ws_risk_connect",
        extra={"user_id": user.id, "role": user.role.value, "admin": is_admin},
    )

    async def _sender() -> None:
        while True:
            payload = await queue.get()
            await ws.send_json(frame("risk_event", payload))

    async def _receiver() -> None:
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get("action") == "ping":
                await ws.send_json(frame("pong"))

    sender_task = asyncio.create_task(_sender(), name=f"ws-risk-send-{user.id}")
    receiver_task = asyncio.create_task(_receiver(), name=f"ws-risk-recv-{user.id}")

    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.warning("ws_risk_task_error", extra={"error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        if is_admin:
            await risk_broadcaster.unsubscribe_admin(queue)
        else:
            await risk_broadcaster.unsubscribe_user(user.id, queue)
        logger.info(
            "ws_risk_disconnect",
            extra={"user_id": user.id, "admin": is_admin},
        )
