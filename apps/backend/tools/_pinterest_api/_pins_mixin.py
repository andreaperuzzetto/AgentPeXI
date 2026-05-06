"""PinterestAPI — Pins stub (pronto per PINTEREST_DELIVERY_METHOD=direct)."""
from __future__ import annotations


class _PinsMixin:

    async def create_pin(
        self,
        board_id: str,
        title: str,
        description: str,
        image_url: str,
        link: str,
    ) -> dict:
        """Crea un Pin su Pinterest.

        Stub pronto per attivazione futura (Standard Access).
        Non viene chiamato finché PINTEREST_DELIVERY_METHOD != 'direct'.
        """
        return await self._request(
            "POST",
            "/v5/pins",
            json_data={
                "board_id": board_id,
                "title": title,
                "description": description,
                "media_source": {
                    "source_type": "image_url",
                    "url": image_url,
                },
                "link": link,
            },
        )
