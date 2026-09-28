"""ORB AI 2.0 — Chart / market-data persistence models (Milestone 5).

Small, self-contained additions. All rows are keyed by ``user_id`` and
carry a single JSON ``data`` column so we can evolve the shape without
Alembic migrations for every new field.

Tables:
  chart_watchlists          – named symbol lists
  chart_favorites           – flat list of favorite symbols per user
  chart_layouts             – saved multi-chart layouts (1 / 2 / 4 panes)
  chart_indicator_presets   – reusable indicator config bundles
  chart_drawing_objects     – persisted drawings per symbol / timeframe
  chart_offline_data        – blob of downloaded historical bars for offline mode
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ChartWatchlist(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chart_watchlists"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_chart_watchlist_user_name"),
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    symbols: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class ChartFavorite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chart_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "exchange", name="uq_chart_fav_user_symbol"),
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="MOCK")


class ChartLayout(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A saved multi-chart layout (1 / 2 / 4 panes).

    ``data`` shape (persisted JSON):
      {
        "panes": [{"symbol": str, "exchange": str, "timeframe": str,
                   "chart_type": "candle"|"ohlc"|"line"|"area",
                   "indicators": [{...}], "drawings": [{...}]}],
        "grid": "1" | "2h" | "2v" | "4",
        "theme": "dark" | "light"
      }
    """
    __tablename__ = "chart_layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_chart_layout_user_name"),
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    grid: Mapped[str] = mapped_column(String(8), nullable=False, default="1")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_default: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class ChartIndicatorPreset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reusable bundle of indicator configurations (e.g. "My scalping set")."""
    __tablename__ = "chart_indicator_presets"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_chart_ind_preset_user_name"),
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    indicators: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class ChartDrawingObject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A drawing (trendline / rect / fib / …) persisted per symbol+timeframe."""
    __tablename__ = "chart_drawing_objects"
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="MOCK")
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="15m")
    tool: Mapped[str] = mapped_column(String(48), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ChartOfflineData(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Downloaded historical bars for a symbol / timeframe (offline mode).

    ``candles`` is a compact list of arrays: [[ts_ms, o, h, l, c, v], …]
    """
    __tablename__ = "chart_offline_data"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "exchange", "timeframe",
                         name="uq_chart_offline_user_symbol_tf"),
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="MOCK")
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    range_label: Mapped[str] = mapped_column(String(16), nullable=False, default="1M")
    candles: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False, default=list)
    from_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    to_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
