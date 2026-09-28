"""Affiliate program config (singleton) + code generation."""
from __future__ import annotations

import base64
import io
import secrets
import string
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import AffiliateProgram, AttributionModel


_CODE_ALPHABET = string.ascii_uppercase + string.digits


def generate_code(length: int = 8) -> str:
    """Return an unambiguous alphanumeric code (uppercase, no I/O/1/0 confusion)."""
    ambiguous = set("IO01")
    allowed = [c for c in _CODE_ALPHABET if c not in ambiguous]
    return "".join(secrets.choice(allowed) for _ in range(length))


def render_qr_data_url(payload: str, box_size: int = 6) -> str:
    """Render a QR code as a data:image/png;base64 URL.

    Uses the pure-python ``qrcode`` package if installed, otherwise
    falls back to a plain-text data URL so the API never fails.
    """
    try:
        import qrcode  # type: ignore
    except ImportError:
        return f"data:text/plain;base64,{base64.b64encode(payload.encode()).decode()}"
    qr = qrcode.QRCode(box_size=box_size, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class ProgramService:
    """Read/write the singleton AffiliateProgram row."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> AffiliateProgram:
        row = await self.session.scalar(select(AffiliateProgram).limit(1))
        if row is None:
            row = AffiliateProgram()
            self.session.add(row)
            await self.session.flush()
        return row

    async def update(self, **fields) -> AffiliateProgram:
        p = await self.get()
        if "attribution_model" in fields and fields["attribution_model"] is not None:
            fields["attribution_model"] = AttributionModel(fields["attribution_model"])
        for k, v in fields.items():
            if v is not None:
                setattr(p, k, v)
        await self.session.flush()
        return p
