"""Tests for apps/backend/core/crypto.py — encrypt/decrypt, key derivation."""
from __future__ import annotations

import base64
import hashlib
import importlib

import pytest
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import apps.backend.core.crypto as crypto_mod


def _make_fernet_for_secret(secret: str) -> Fernet:
    """Reproduce the key-derivation logic from get_fernet() with an arbitrary secret."""
    digest = hashlib.sha256(secret.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


@pytest.fixture(autouse=True)
def reset_fernet_singleton():
    """Reset the module-level singleton before each test so key changes take effect."""
    crypto_mod._fernet_instance = None
    yield
    crypto_mod._fernet_instance = None


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "test-secret-key-roundtrip")
    fernet = crypto_mod.get_fernet()

    plaintext = b"hello, AgentPeXI!"
    token = fernet.encrypt(plaintext)
    assert fernet.decrypt(token) == plaintext


def test_roundtrip_preserves_utf8(monkeypatch):
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "test-secret-utf8")
    fernet = crypto_mod.get_fernet()

    plaintext = "ciao mondo 🎉".encode()
    assert fernet.decrypt(fernet.encrypt(plaintext)) == plaintext


# ---------------------------------------------------------------------------
# Wrong key raises exception
# ---------------------------------------------------------------------------

def test_wrong_key_raises_invalid_token(monkeypatch):
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "secret-A")
    fernet_a = crypto_mod.get_fernet()
    token = fernet_a.encrypt(b"sensitive data")

    # Use a different key to decrypt
    fernet_b = _make_fernet_for_secret("secret-B")
    with pytest.raises(InvalidToken):
        fernet_b.decrypt(token)


def test_tampered_token_raises(monkeypatch):
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "test-tamper")
    fernet = crypto_mod.get_fernet()
    token = bytearray(fernet.encrypt(b"data"))
    token[10] ^= 0xFF  # flip bits in the middle
    with pytest.raises((InvalidToken, Exception)):
        fernet.decrypt(bytes(token))


# ---------------------------------------------------------------------------
# SHA-256 derivation determinism
# ---------------------------------------------------------------------------

def test_same_secret_same_key(monkeypatch):
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "deterministic-secret")
    f1 = crypto_mod.get_fernet()

    # Reset singleton; same secret → same instance key material
    crypto_mod._fernet_instance = None
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "deterministic-secret")
    f2 = crypto_mod.get_fernet()

    # Tokens encrypted with f1 are decryptable by f2
    token = f1.encrypt(b"payload")
    assert f2.decrypt(token) == b"payload"


def test_different_secrets_different_keys():
    fernet_x = _make_fernet_for_secret("secret-X")
    fernet_y = _make_fernet_for_secret("secret-Y")

    token = fernet_x.encrypt(b"payload")
    with pytest.raises(InvalidToken):
        fernet_y.decrypt(token)


def test_get_fernet_is_singleton(monkeypatch):
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", "singleton-test")
    f1 = crypto_mod.get_fernet()
    f2 = crypto_mod.get_fernet()
    assert f1 is f2


def test_key_derivation_is_pbkdf2(monkeypatch):
    """Key derivation must use PBKDF2HMAC (not raw SHA-256) — MED-1 fix."""
    secret = "known-secret"
    monkeypatch.setattr(crypto_mod.settings, "SECRET_KEY", secret)
    fernet = crypto_mod.get_fernet()

    salt = bytes.fromhex(crypto_mod.settings.CRYPTO_SALT)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    expected_key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    manual_fernet = Fernet(expected_key)

    token = manual_fernet.encrypt(b"check")
    assert fernet.decrypt(token) == b"check"
