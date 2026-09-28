"""Notification endpoints (Module 8)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DBSession
from app.models.notification import NotificationEvent, NotificationSeverity
from app.schemas.analytics import (
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    NotificationTestRequest,
)
from app.schemas.common import Message, PaginatedResponse
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[NotificationRead], summary="List notifications")
async def list_notifications(
    current_user: CurrentUser,
    session: DBSession,
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[NotificationRead]:
    offset = (page - 1) * page_size
    items, total = await NotificationService(session).list_for_user(
        current_user.id, unread_only=unread_only, offset=offset, limit=page_size
    )
    return PaginatedResponse[NotificationRead](
        items=[NotificationRead.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{notification_id}/read", response_model=NotificationRead, summary="Mark one as read")
async def mark_read(
    notification_id: str, current_user: CurrentUser, session: DBSession
) -> NotificationRead:
    notif = await NotificationService(session).mark_read(current_user.id, notification_id)
    if notif is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    await session.commit()
    return NotificationRead.model_validate(notif)


@router.post("/read-all", response_model=Message, summary="Mark all as read")
async def mark_all_read(current_user: CurrentUser, session: DBSession) -> Message:
    n = await NotificationService(session).mark_all_read(current_user.id)
    await session.commit()
    return Message(message=f"Marked {n} notification(s) as read")


@router.get(
    "/preferences",
    response_model=NotificationPreferenceRead,
    summary="Get notification preferences",
)
async def get_prefs(current_user: CurrentUser, session: DBSession) -> NotificationPreferenceRead:
    pref = await NotificationService(session).get_preference(current_user.id)
    await session.commit()
    return NotificationPreferenceRead.model_validate(pref)


@router.put(
    "/preferences",
    response_model=NotificationPreferenceRead,
    summary="Update notification preferences",
)
async def update_prefs(
    payload: NotificationPreferenceUpdate,
    current_user: CurrentUser,
    session: DBSession,
) -> NotificationPreferenceRead:
    pref = await NotificationService(session).update_preference(
        current_user.id, **payload.model_dump(exclude_unset=True)
    )
    await AuditService(session).record(
        action="update", target_type="notification_preference",
        target_id=pref.id, actor=current_user,
    )
    await session.commit()
    return NotificationPreferenceRead.model_validate(pref)


@router.post("/test", response_model=NotificationRead, summary="Send a test notification to yourself")
async def test_notification(
    payload: NotificationTestRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> NotificationRead:
    try:
        event = NotificationEvent(payload.event)
    except ValueError:
        event = NotificationEvent.SYSTEM_ALERT
    try:
        severity = NotificationSeverity(payload.severity)
    except ValueError:
        severity = NotificationSeverity.INFO
    notif = await NotificationService(session).notify(
        user=current_user,
        event=event,
        title=payload.title,
        body=payload.body,
        severity=severity,
    )
    await session.commit()
    return NotificationRead.model_validate(notif)
