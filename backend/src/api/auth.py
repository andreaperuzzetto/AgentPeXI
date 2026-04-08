from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as _jwt
from jwt.exceptions import InvalidTokenError

# Re-export per compatibilità nei dipendenti
JWTError = InvalidTokenError

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 24


def _secret_key() -> str:
    return os.environ["SECRET_KEY"]


def verify_password(plain: str) -> bool:
    """Verifica la password rispetto all'hash bcrypt in OPERATOR_PASSWORD_HASH."""
    stored_hash = os.environ.get("OPERATOR_PASSWORD_HASH", "")
    if not stored_hash:
        return False
    return bcrypt.checkpw(plain.encode(), stored_hash.encode())


def create_access_token(email: str) -> str:
    """Genera JWT firmato con SECRET_KEY. Scade dopo 24h."""
    expire = datetime.now(timezone.utc) + timedelta(hours=_TOKEN_EXPIRE_HOURS)
    payload: dict = {"sub": email, "exp": expire}
    return _jwt.encode(payload, _secret_key(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> str:
    """
    Decodifica e valida il JWT. Restituisce l'email del sub.
    Solleva InvalidTokenError se il token è invalido o scaduto.
    """
    claims = _jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
    sub: str | None = claims.get("sub")
    if not sub:
        raise InvalidTokenError("Token privo di sub")
    return sub


def create_portal_token(payload: dict) -> str:
    """Genera JWT firmato con PORTAL_SECRET_KEY per il portale cliente."""
    return _jwt.encode(payload, _portal_secret_key(), algorithm=_ALGORITHM)


def decode_portal_token(token: str) -> dict:
    """
    Decodifica e valida il JWT del portale. Solleva InvalidTokenError se invalido/scaduto.
    """
    return _jwt.decode(token, _portal_secret_key(), algorithms=[_ALGORITHM])  # type: ignore[return-value]


def _portal_secret_key() -> str:
    return os.environ["PORTAL_SECRET_KEY"]
