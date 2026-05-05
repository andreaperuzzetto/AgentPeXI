"""EtsyAPI — error classes and retry helpers."""
from __future__ import annotations

import httpx


def _is_retryable(exc: BaseException) -> bool:
    """Riprova solo su 429 (rate limit) o 5xx (server error), non su 4xx client error."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


class EtsyAPIError(Exception):
    """Eccezione per errori Etsy API (4xx/5xx, token scaduto, rate limit)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
