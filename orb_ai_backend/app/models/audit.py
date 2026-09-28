"""AuditLog ORM model — records privileged actions taken by admins.

Every mutation an admin performs from the dashboard is logged here so
operators have a tamper-evident trail of who did what and when.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per admin action.

    ``actor_user_id`` may be null for system-generated events.
    ``target_type`` / ``target_id`` reference the object the action affected
    (``user``, ``broker_account``, ``engine_session``, ``strategy`` …).
    """

    __tablename__ = "audit_logs"

    actor_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_audit_logs_target_type_target_id", "target_type", "target_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuditLog id={self.id} actor={self.actor_user_id} "
            f"action={self.action} target={self.target_type}:{self.target_id}>"
        )
