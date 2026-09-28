"""Per-user settings ORM model."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User


class UserSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Preferences and risk-management defaults per user.

    Kept intentionally broad — the trading engine (future module) will read
    from here at runtime, and `preferences` is a JSON bag for anything the UI
    wants to remember without a schema change.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ---- Locale / display ----
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    theme: Mapped[str] = mapped_column(String(16), default="system", nullable=False)  # light | dark | system

    # ---- Notifications ----
    notifications_email: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notifications_push: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ---- Trading defaults ----
    default_broker: Mapped[str | None] = mapped_column(String(64))
    max_daily_loss: Mapped[float | None] = mapped_column(Numeric(18, 4))
    max_position_size: Mapped[float | None] = mapped_column(Numeric(18, 4))
    risk_per_trade_pct: Mapped[float | None] = mapped_column(Numeric(6, 3))  # e.g. 1.500 = 1.5%

    # ---- Free-form UI preferences ----
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped["User"] = relationship(back_populates="settings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserSettings user_id={self.user_id}>"
