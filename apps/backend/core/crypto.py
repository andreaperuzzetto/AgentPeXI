"""Shared cryptographic utilities for AgentPeXI backend."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from apps.backend.core.config import settings

_fernet_instance: Fernet | None = None


def get_fernet() -> Fernet:
    """Return a lazily-initialised Fernet instance derived from SECRET_KEY.

    Using a module-level singleton means the key is derived once and reused,
    preventing divergence if SECRET_KEY ever changes in memory between calls.
    """
    global _fernet_instance
    if _fernet_instance is None:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
        _fernet_instance = Fernet(key)
    return _fernet_instance
