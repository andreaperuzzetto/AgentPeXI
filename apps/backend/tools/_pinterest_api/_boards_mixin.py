"""PinterestAPI — Boards stubs (pronto per PINTEREST_DELIVERY_METHOD=direct)."""
from __future__ import annotations


class _BoardsMixin:

    async def list_boards(self, page_size: int = 25, bookmark: str | None = None) -> dict:
        """Elenca le board dell'utente autenticato.

        Stub pronto per attivazione futura (Standard Access).
        """
        params: dict = {"page_size": page_size}
        if bookmark:
            params["bookmark"] = bookmark
        return await self._request("GET", "/v5/boards", params=params)

    async def create_board(self, name: str, description: str = "") -> dict:
        """Crea una nuova board Pinterest.

        Stub pronto per attivazione futura (Standard Access).
        """
        return await self._request(
            "POST",
            "/v5/boards",
            json_data={"name": name, "description": description},
        )
