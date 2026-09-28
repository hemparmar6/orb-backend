"""BrokerAccount ORM model — per-user, Fernet-encrypted broker credentials.

The `credentials_ciphertext` column stores a Fernet token whose plaintext is a
JSON dict of broker-specific fields (client_id, access_token, api_key, etc.).
We never store, log, or return the plaintext. Decryption is on-demand inside
the broker adapter.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User


class BrokerType(str, enum.Enum):
    MOCK_LIVE = "mock_live"
    DHAN = "dhan"
    KOTAK_NEO = "kotak_neo"


class BrokerAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "broker_accounts"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_type: Mapped[BrokerType] = mapped_column(
        Enum(BrokerType, name="broker_type", native_enum=False, length=32),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # Fernet-encrypted JSON blob of broker credentials.
    credentials_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BrokerAccount id={self.id} broker={self.broker_type} user={self.user_id}>"
