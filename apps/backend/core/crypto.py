"""Shared cryptographic utilities for AgentPeXI backend."""
from __future__ import annotations

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from apps.backend.core.config import settings

_fernet_instance: Fernet | None = None


def get_fernet() -> Fernet:
    """Return a lazily-initialised Fernet instance derived from SECRET_KEY + CRYPTO_SALT.

    Uses PBKDF2HMAC with SHA-256 and 600,000 iterations (OWASP 2024 recommendation)
    to derive the 32-byte encryption key. The salt is fixed per deployment and stored
    in CRYPTO_SALT (hex-encoded). Using a module-level singleton means the KDF is run
    once and the key is reused.
    """
    global _fernet_instance
    if _fernet_instance is None:
        salt = bytes.fromhex(settings.CRYPTO_SALT)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
        key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.encode()))
        _fernet_instance = Fernet(key)
    return _fernet_instance
