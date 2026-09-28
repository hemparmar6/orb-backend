"""BrokerService — connect / list / snapshot broker accounts.

Trading orders never come through this service — they always go via the
Trading Engine. This module is only responsible for account management +
read-only snapshots (funds / positions / orders as reported by the broker).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerAdapter
from app.brokers.registry import (
    get_broker_adapter,
    get_broker_adapter_class,
)
from app.core.crypto import decrypt_json, encrypt_json
from app.core.exceptions import (
    BrokerAlreadyConnectedError,
    BrokerNotFoundError,
)
from app.models.broker import BrokerAccount
from app.repositories.broker_repository import BrokerAccountRepository
from app.schemas.broker import BrokerConnectRequest


class BrokerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BrokerAccountRepository(session)

    # ---- account lifecycle -------------------------------------------

    async def connect(
        self, user_id: str, payload: BrokerConnectRequest
    ) -> BrokerAccount:
        # Validate broker exists + credentials shape BEFORE encrypting.
        adapter_cls = get_broker_adapter_class(payload.broker_type.value)
        adapter_cls.validate_credentials(payload.credentials)

        existing = await self.repo.find_duplicate(
            user_id, payload.broker_type, payload.alias
        )
        if existing is not None:
            raise BrokerAlreadyConnectedError(
                f"A {payload.broker_type.value} account with alias '{payload.alias}' already exists"
            )

        account = BrokerAccount(
            user_id=user_id,
            broker_type=payload.broker_type,
            alias=payload.alias or payload.broker_type.value,
            credentials_ciphertext=encrypt_json(payload.credentials),
            is_active=True,
        )
        await self.repo.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def list_for_user(self, user_id: str) -> Sequence[BrokerAccount]:
        return await self.repo.list_for_user(user_id)

    async def get_for_user(self, user_id: str, broker_account_id: str) -> BrokerAccount:
        acc = await self.repo.get_for_user(broker_account_id, user_id)
        if acc is None:
            raise BrokerNotFoundError()
        return acc

    async def delete(self, user_id: str, broker_account_id: str) -> None:
        acc = await self.get_for_user(user_id, broker_account_id)
        await self.repo.delete(acc)
        await self.session.commit()

    # ---- adapter instantiation ---------------------------------------

    async def build_adapter(self, account: BrokerAccount) -> BrokerAdapter:
        """Decrypt credentials and instantiate the broker adapter."""
        creds = decrypt_json(account.credentials_ciphertext)
        adapter = get_broker_adapter(
            account.broker_type.value,
            creds,
            alias=account.alias,
        )
        account.last_used_at = datetime.now(timezone.utc)
        await self.session.flush()
        return adapter
