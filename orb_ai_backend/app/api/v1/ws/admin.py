"""WS /ws/v1/admin — real-time admin dashboard snapshots.

Requires an admin JWT via ``?token=``. On connect the socket sends an
initial snapshot and then a fresh snapshot every ``interval`` seconds
(default 3s, minimum 1s, maximum 15s — controlled by the ``interval``
query param).

Frames follow the same protocol as ``/ws/quotes`` and ``/ws/orders``:

    → client:  ``{"action": "ping"}``
    ← server:  ``{"type": "pong"}``
    ← server:  ``{"type": "snapshot", "data": {...}}``
    ← server:  ``{"type": "error",    "data": {"code": "...", "message": "..."}}``

Unlike ``/ws/orders`` this socket does NOT depend on Redis — the payload is
computed directly from the DB, so it works in preview pods where Redis is
disabled and still delivers sub-second latency compared to the old 15s
REST poll.

Non-admin JWTs are rejected with close code 4403 (Forbidden).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

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
from app.services.admin_dashboard_service import compute_admin_snapshot
from app.ws.protocol import error_frame, frame, parse_client_message
from app.ws.rate_limit import default_bucket

logger = get_logger(__name__)
router = APIRouter()

CLOSE_AUTH_FAILED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_RATE_LIMITED = 4429
CLOSE_INTERNAL = 4500

# Server-side clamps — any client query param is bounded by these.
MIN_INTERVAL_SEC = 1.0
MAX_INTERVAL_SEC = 15.0
DEFAULT_INTERVAL_SEC = 3.0


def _default(o):  # datetime → ISO string, for the snapshot dict
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _clean(payload):
    """Round-trip the snapshot through JSON so ``datetime``s become strings."""
    return json.loads(json.dumps(payload, default=_default))


def _parse_interval(ws: WebSocket) -> float:
    raw = ws.query_params.get("interval")
    if not raw:
        return DEFAULT_INTERVAL_SEC
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SEC
    return max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, v))


@router.websocket("/admin")
async def admin_ws(ws: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
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

    # ---- RBAC ---------------------------------------------------------
    if user.role != UserRole.ADMIN:
        await ws.send_json(error_frame("forbidden", "Admin privileges required"))
        await ws.close(code=CLOSE_FORBIDDEN)
        return

    interval = _parse_interval(ws)
    bucket = default_bucket()
    logger.info(
        "ws_admin_connect",
        extra={"user_id": user.id, "interval_sec": interval},
    )

    async def _send_snapshot() -> None:
        payload = await compute_admin_snapshot(db)
        await ws.send_json(frame("snapshot", _clean(payload)))

    # Send one snapshot immediately so the UI never shows an empty state.
    try:
        await _send_snapshot()
    except Exception:  # pragma: no cover
        logger.exception("ws_admin_initial_snapshot_failed")
        await ws.close(code=CLOSE_INTERNAL)
        return

    async def _pusher() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await _send_snapshot()
            except WebSocketDisconnect:
                return
            except Exception:
                logger.exception("ws_admin_snapshot_error")
                # Non-fatal — don't kill the socket over one flaky query.
                continue

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
            # This socket has no subscribe/unsubscribe surface — echo back.
            await ws.send_json(error_frame(
                "unsupported_action", f"Admin socket does not support action '{parsed.action}'"
            ))

    pusher_task = asyncio.create_task(_pusher(), name=f"ws-admin-push-{user.id}")
    recv_task = asyncio.create_task(_receiver(), name=f"ws-admin-recv-{user.id}")

    try:
        done, pending = await asyncio.wait(
            {pusher_task, recv_task}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.warning("ws_admin_task_error", extra={"error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("ws_admin_disconnect", extra={"user_id": user.id})
