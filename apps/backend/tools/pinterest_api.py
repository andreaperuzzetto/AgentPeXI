"""PinterestAPI — Wrapper async per Pinterest v5 API con token management.

Non viene usato in produzione finché PINTEREST_DELIVERY_METHOD != 'direct'.
Stub pronto per attivazione post Standard Access approval.
"""
from __future__ import annotations

import asyncio

from apps.backend.tools._pinterest_api._auth_mixin import _AuthMixin
from apps.backend.tools._pinterest_api._boards_mixin import _BoardsMixin
from apps.backend.tools._pinterest_api._pins_mixin import _PinsMixin

__all__ = ["PinterestAPI"]


class PinterestAPI(_BoardsMixin, _PinsMixin, _AuthMixin, object):
    """Client async per Pinterest v5 API."""

    def __init__(self, memory, pepe=None) -> None:
        self.memory = memory
        self.pepe = pepe
        self._client = None  # httpx.AsyncClient (lazy init in _request)
        super().__init__()  # triggers _AuthMixin.__init__ → sets _token_lock
