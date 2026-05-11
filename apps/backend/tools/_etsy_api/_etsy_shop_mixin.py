"""EtsyAPI — shop, messages, and ads mixin."""
from __future__ import annotations

import logging
from typing import Any

from apps.backend.core.config import settings
from apps.backend.tools._etsy_api._errors import EtsyAPIError

logger = logging.getLogger("agentpexi.etsy_api")


class _ShopMixin:

    # ------------------------------------------------------------------
    # Metodi pubblici — Messaggi
    # ------------------------------------------------------------------

    async def get_messages(self, shop_id: str | None = None) -> list[dict]:
        if self.mock_mode:
            return []
        raise NotImplementedError("Etsy v3 non espone un endpoint messaggi pubblico")

    async def reply_message(
        self, shop_id: str, conversation_id: str, message: str
    ) -> dict:
        if self.mock_mode:
            return {}
        raise NotImplementedError("Etsy v3 non espone un endpoint messaggi pubblico")

    # ------------------------------------------------------------------
    # Metodi pubblici — Shop & Stats
    # ------------------------------------------------------------------

    async def get_shop(self, shop_id: str | None = None) -> dict:
        if self.mock_mode:
            return await self._mock_get_shop(shop_id)
        sid = shop_id or settings.ETSY_SHOP_ID
        return await self._request("GET", f"/application/shops/{sid}")

    async def get_shop_stats(self, shop_id: str | None = None) -> dict:
        """Info shop (Etsy v3 non ha endpoint stats dedicato, usa shop info)."""
        if self.mock_mode:
            return await self._mock_get_shop(shop_id)
        return await self.get_shop(shop_id)

    async def update_shop(
        self,
        title: str | None = None,
        announcement: str | None = None,
        shop_id: str | None = None,
    ) -> dict:
        """
        Aggiorna titolo e/o announcement dello shop via Etsy API v3.

        PATCH /v3/application/shops/{shop_id}

        Args:
            title:        Titolo shop (max 55 char — limite Etsy).
            announcement: Testo About/Announcement (max 5000 char).
            shop_id:      Opzionale — usa settings.ETSY_SHOP_ID se omesso.

        Nota: L'"About" section di Etsy (pagina About dello shop) non è
        modificabile via API pubblica. L'announcement è il campo più vicino
        accessibile via API v3 e viene mostrato nella shop page.
        """
        if self.mock_mode:
            return await self._mock_update_shop(title=title, announcement=announcement)

        sid  = shop_id or settings.ETSY_SHOP_ID
        body: dict = {}
        if title is not None:
            body["title"] = title[:55]          # enforced Etsy limit
        if announcement is not None:
            body["announcement"] = announcement[:5000]

        if not body:
            return {}

        return await self._request(
            "PATCH",
            f"/application/shops/{sid}",
            json_data=body,
        )

    async def get_shop_receipts(
        self, shop_id: str | None = None, min_created: int | None = None
    ) -> list[dict]:
        if self.mock_mode:
            return []
        sid = shop_id or settings.ETSY_SHOP_ID
        params: dict[str, Any] = {"limit": 100}
        if min_created is not None:
            params["min_created"] = min_created
        result = await self._request(
            "GET",
            f"/application/shops/{sid}/receipts",
            params=params,
        )
        return result.get("results", [])

    async def get_shop_transactions(
        self, shop_id: str | None = None, listing_id: int | None = None,
    ) -> dict:
        """Transazioni per un listing specifico o per tutto lo shop."""
        if self.mock_mode:
            return await self._mock_get_shop_transactions(shop_id, listing_id)
        sid = shop_id or settings.ETSY_SHOP_ID
        if listing_id is not None:
            return await self._request(
                "GET",
                f"/application/shops/{sid}/listings/{listing_id}/transactions",
                params={"limit": 100},
            )
        return await self._request(
            "GET",
            f"/application/shops/{sid}/transactions",
            params={"limit": 100},
        )

    # ------------------------------------------------------------------
    # Etsy Ads — B5/5.2
    # ------------------------------------------------------------------

    async def create_ad_campaign(
        self,
        listing_id: str | int,
        daily_budget_eur: float,
        shop_id: str | None = None,
    ) -> dict:
        """
        Attiva una campagna Etsy Ads per un listing.

        POST /v3/application/shops/{shop_id}/ads
        Body: { listing_ids: [int], daily_budget: int }  ← budget in cents (EUR × 100)

        Nota: Etsy esprime il budget in centesimi di EUR.
        €1.50/giorno → daily_budget=150.

        Args:
            listing_id:       ID del listing su Etsy.
            daily_budget_eur: Budget giornaliero in EUR (es. 1.50 → €1.50/giorno).
            shop_id:          Opzionale — usa settings.ETSY_SHOP_ID se omesso.

        Returns:
            dict con listing_id, daily_budget, status (o mock=True in mock mode).
        """
        if self.mock_mode:
            return await self._mock_create_ad_campaign(listing_id, daily_budget_eur)

        sid = shop_id or settings.ETSY_SHOP_ID
        budget_cents = max(1, int(round(daily_budget_eur * 100)))

        return await self._request(
            "POST",
            f"/application/shops/{sid}/ads",
            json={
                "listing_ids":  [int(listing_id)],
                "daily_budget": budget_cents,
            },
        )

    async def pause_ad_campaign(
        self,
        listing_id: str | int,
        shop_id: str | None = None,
    ) -> dict:
        """
        Mette in pausa (elimina) una campagna Etsy Ads per un listing.

        DELETE /v3/application/shops/{shop_id}/ads/{listing_id}

        Etsy non ha PATCH ads — delete equivale a pausa permanente;
        per riattivare basta chiamare create_ad_campaign di nuovo.

        Args:
            listing_id: ID del listing.
            shop_id:    Opzionale — usa settings.ETSY_SHOP_ID se omesso.
        """
        if self.mock_mode:
            return await self._mock_pause_ad_campaign(listing_id)

        sid = shop_id or settings.ETSY_SHOP_ID
        return await self._request(
            "DELETE",
            f"/application/shops/{sid}/ads/{listing_id}",
        )

    async def get_listing_ad_stats(
        self,
        listing_id: str | int,
        shop_id: str | None = None,
    ) -> dict:
        """
        Statistiche ads per un singolo listing.

        GET /v3/application/shops/{shop_id}/ads/{listing_id}

        Etsy v3 ritorna: listing_id, daily_budget, impressions, clicks, spend, ...
        Normalizzato a: {listing_id, impressions, clicks, spend_eur, orders}

        In caso di 404 (listing non ha ads attive) ritorna zero-stats senza raise.
        """
        if self.mock_mode:
            return await self._mock_get_listing_ad_stats(listing_id)

        sid = shop_id or settings.ETSY_SHOP_ID
        try:
            raw = await self._request(
                "GET",
                f"/application/shops/{sid}/ads/{listing_id}",
            )
        except EtsyAPIError:
            # Listing non ha campagna attiva oppure endpoint non disponibile
            return {
                "listing_id":  str(listing_id),
                "impressions": 0,
                "clicks":      0,
                "spend_eur":   0.0,
                "orders":      0,
            }

        # Normalizza il budget Etsy da centesimi a EUR
        spend_cents = raw.get("daily_budget", 0) or 0
        return {
            "listing_id":  str(listing_id),
            "impressions": raw.get("impressions", 0) or 0,
            "clicks":      raw.get("clicks", 0) or 0,
            "spend_eur":   round(spend_cents / 100, 2),
            "orders":      raw.get("orders", 0) or 0,
        }
