"""Fernet crypto — round-trip + failure modes."""
from __future__ import annotations

import pytest

from app.core.crypto import (
    DecryptionError,
    EncryptionKeyMissingError,
    decrypt_json,
    encrypt_json,
    reset_cache,
)


def test_encrypt_decrypt_roundtrip():
    payload = {"client_id": "abc", "access_token": "xyz", "meta": {"n": 3}}
    token = encrypt_json(payload)
    assert token != str(payload)
    assert "client_id" not in token  # ciphertext must not leak keys
    assert decrypt_json(token) == payload


def test_encrypt_output_is_url_safe():
    token = encrypt_json({"k": "v"})
    # Fernet output is urlsafe-base64 → only [A-Za-z0-9_-=] chars
    assert all(c.isalnum() or c in "-_=" for c in token)


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    reset_cache()
    # Also monkey-patch the cached settings.
    from app.core import config as config_module
    monkeypatch.setattr(config_module.settings, "ENCRYPTION_KEY", None)
    try:
        with pytest.raises(EncryptionKeyMissingError):
            encrypt_json({"a": 1})
    finally:
        # Restore for other tests.
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        monkeypatch.setattr(config_module.settings, "ENCRYPTION_KEY", key)
        reset_cache()


def test_tampered_token_raises_decryption_error():
    token = encrypt_json({"k": "v"})
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(DecryptionError):
        decrypt_json(tampered)
