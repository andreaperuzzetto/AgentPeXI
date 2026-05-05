"""EtsyAPI — Wrapper async per Etsy v3 API con rate limiting, retry e token management."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from apps.backend.core.memory import MemoryManager
from apps.backend.tools._etsy_api._errors import EtsyAPIError, _is_retryable
from apps.backend.tools._etsy_api._etsy_auth_mixin import _AuthMixin
from apps.backend.tools._etsy_api._etsy_listings_mixin import _ListingsMixin
from apps.backend.tools._etsy_api._etsy_mock_mixin import _MockMixin
from apps.backend.tools._etsy_api._etsy_sections_mixin import _SectionsMixin
from apps.backend.tools._etsy_api._etsy_shop_mixin import _ShopMixin

__all__ = ["EtsyAPI", "EtsyAPIError", "_is_retryable"]


class EtsyAPI(_ShopMixin, _SectionsMixin, _ListingsMixin, _AuthMixin, _MockMixin, object):
    """Client async per Etsy v3 API."""

    def __init__(self, memory: MemoryManager, pepe: Any = None) -> None:
        self.memory = memory
        self.pepe = pepe

        # Rate limiting: max 10 req/sec
        self._semaphore = asyncio.Semaphore(10)
        self._last_request_time: float = 0.0
        self._min_interval: float = 0.1  # 100ms tra chiamate

        # Contatore giornaliero API calls (in memoria, reset a mezzanotte)
        self._daily_count: int = 0
        self._daily_reset_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # HTTP client (lazy init)
        self._client = None

