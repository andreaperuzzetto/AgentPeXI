"""EtsyAPI — sections mixin (PA-6)."""
from __future__ import annotations

import logging

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.etsy_api")


class _SectionsMixin:

    async def get_shop_sections(self, shop_id: str | None = None) -> list[dict]:
        """GET /v3/application/shops/{shop_id}/sections

        Ritorna lista di sezioni Etsy del negozio.
        Ogni dict ha: shop_section_id (str), title (str), active_listing_count (int).
        In mock mode ritorna lista vuota — nessuna sezione da sincronizzare.
        """
        if self.mock_mode:
            return []
        sid = shop_id or settings.ETSY_SHOP_ID
        result = await self._request(
            "GET",
            f"/application/shops/{sid}/sections",
        )
        return result.get("results", [])

    async def create_shop_section(
        self,
        title: str,
        shop_id: str | None = None,
    ) -> dict:
        """POST /v3/application/shops/{shop_id}/sections

        Crea una nuova sezione Etsy con il titolo dato.
        Ritorna il dict della sezione creata con shop_section_id e title.
        In mock mode ritorna un dict stub senza chiamata reale.
        """
        if self.mock_mode:
            return {"shop_section_id": "mock-section-id", "title": title, "active_listing_count": 0}
        sid = shop_id or settings.ETSY_SHOP_ID
        return await self._request(
            "POST",
            f"/application/shops/{sid}/sections",
            json_data={"title": title},
        )
