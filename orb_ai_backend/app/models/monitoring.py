"""Module 10 monitoring models.

Persisted so history survives restarts:
* ``BackupRecord`` — every automated / manual PostgreSQL backup
* ``LoginActivity`` — successful + failed logins (security dashboard)
* ``RateLimitEvent`` — 429s recorded for the security dashboard
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BackupRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "backup_records"

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    # "scheduled" | "manual" | "test"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # "pending" | "running" | "success" | "failed" | "verified"

    filename: Mapped[Optional[str]] = mapped_column(String(255))
    local_path: Mapped[Optional[str]] = mapped_column(Text)
    s3_key: Mapped[Optional[str]] = mapped_column(String(512))
    s3_bucket: Mapped[Optional[str]] = mapped_column(String(255))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64))

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_to_s3: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    error_message: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class RestoreRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "restore_records"

    backup_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("backup_records.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    # "local" | "s3"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    initiated_by: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LoginActivity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "login_activity"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[Optional[str]] = mapped_column(String(64))
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))


class RateLimitEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rate_limit_events"

    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="GET")
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    retry_after_seconds: Mapped[Optional[float]] = mapped_column(Float)
