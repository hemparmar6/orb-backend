"""Shared / generic Pydantic schemas."""
from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Message(BaseModel):
    """Simple text response, e.g. for logout."""

    message: str


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated envelope."""

    items: List[T]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=200)
