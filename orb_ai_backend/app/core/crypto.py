"""Symmetric encryption helper (Fernet).

Used from Module 3 onwards to encrypt per-user broker credentials before
persisting them to the ``broker_accounts`` table.

Design goals:
- **Never** store secrets in plaintext.
- Keep the crypto surface tiny (``encrypt_json`` / ``decrypt_json``) so it can
  later be swapped for AWS Secrets Manager / Azure Key Vault / HashiCorp Vault
  without touching call sites.
- Load the key **only** from ``settings.ENCRYPTION_KEY`` (env var).
- Fail loudly (``EncryptionKeyMissingError``) if crypto is used before the key
  is configured, so tests / prod deploys don't accidentally proceed.
"""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import AppError


class EncryptionKeyMissingError(AppError):
    status_code = 500
    code = "encryption_key_missing"
    message = (
        "ENCRYPTION_KEY is not configured. Set a Fernet key (44-char urlsafe "
        "base64) in your .env — generate one with: "
        'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )


class DecryptionError(AppError):
    status_code = 500
    code = "decryption_failed"
    message = "Failed to decrypt stored credentials"


# ---- Fernet singleton (built lazily so tests can override the env) --------

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    key = settings.ENCRYPTION_KEY
    if not key:
        raise EncryptionKeyMissingError()
    try:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:  # pragma: no cover - bad key format
        raise EncryptionKeyMissingError(
            f"ENCRYPTION_KEY is not a valid Fernet key: {exc}"
        ) from exc
    return _fernet


def reset_cache() -> None:
    """Used by tests to force re-read of ENCRYPTION_KEY after mutation."""
    global _fernet
    _fernet = None


# ---- Public API -----------------------------------------------------------


def encrypt_str(plain: str) -> str:
    """Encrypt a UTF-8 string; return an urlsafe base64 token."""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_str(token: str) -> str:
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError() from exc


def encrypt_json(payload: dict[str, Any]) -> str:
    """Encrypt a dict — JSON-serialised then Fernet-encrypted."""
    return encrypt_str(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def decrypt_json(token: str) -> dict[str, Any]:
    return json.loads(decrypt_str(token))
