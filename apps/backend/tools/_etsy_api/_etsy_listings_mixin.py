"""EtsyAPI — listings mixin."""
from __future__ import annotations

import logging
from typing import Any

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.etsy_api")


class _ListingsMixin:

    # ------------------------------------------------------------------
    # Metodi pubblici — Listings
    # ------------------------------------------------------------------

    async def create_listing(
        self,
        title: str,
        description: str,
        price: float,
        tags: list[str],
        taxonomy_id: int,
        quantity: int = 999,
        who_made: str = "i_did",
        when_made: str = "2020_2025",
        is_supply: bool = False,
        is_digital: bool = True,
        **kwargs: Any,
    ) -> dict:
        if self.mock_mode:
            return await self._mock_create_listing(title=title, price=price, tags=tags,
                                                    description=description, **kwargs)
        shop_id = settings.ETSY_SHOP_ID
        payload = {
            "title": title,
            "description": description,
            "price": price,
            "quantity": quantity,
            "tags": tags,
            "taxonomy_id": taxonomy_id,
            "who_made": who_made,
            "when_made": when_made,
            "is_supply": is_supply,
            "is_digital": is_digital,
            "type": "download",
            **kwargs,
        }
        return await self._request("POST", f"/application/shops/{shop_id}/listings", json_data=payload)

    async def upload_file(self, listing_id: int, file_path: str, name: str) -> dict:
        if self.mock_mode:
            return await self._mock_upload_file(listing_id, file_path, name)
        shop_id = settings.ETSY_SHOP_ID
        with open(file_path, "rb") as f:
            files = {"file": (name, f, "application/octet-stream")}
            return await self._request(
                "POST",
                f"/application/shops/{shop_id}/listings/{listing_id}/files",
                files=files,
                data={"name": name},
            )

    async def upload_image(self, listing_id: int | str, file_path: str) -> dict:
        """Carica un'immagine thumbnail su Etsy per il listing."""
        if self.mock_mode:
            return await self._mock_upload_image(listing_id, file_path)
        import os as _os
        shop_id = settings.ETSY_SHOP_ID
        name = _os.path.basename(file_path)
        with open(file_path, "rb") as f:
            files = {"image": (name, f, "image/png")}
            return await self._request(
                "POST",
                f"/application/shops/{shop_id}/listings/{listing_id}/images",
                files=files,
            )

    async def get_listing(self, listing_id: int) -> dict:
        if self.mock_mode:
            return await self._mock_get_listing(listing_id)
        return await self._request("GET", f"/application/listings/{listing_id}")

    async def get_listing_stats(self, listing_id: int | str) -> dict:
        """
        Ritorna {views, clicks, favorites, num_orders, revenue_eur} per un listing.
        🔴 Etsy v3 non espone clicks senza Etsy Ads API.
        In real mode: clicks=0 (dato non disponibile).
        In mock mode: simula valori CTR realistici per il Ladder System.
        """
        if self.mock_mode:
            return await self._mock_get_listing_stats(listing_id)

        listing_data = await self._request("GET", f"/application/listings/{listing_id}")
        views     = listing_data.get("views", 0)
        favorites = listing_data.get("num_favorers", 0)
        price_dict = listing_data.get("price", {})
        if isinstance(price_dict, dict):
            price_eur = float(price_dict.get("amount", 0)) / 100
        else:
            price_eur = float(price_dict or 0)

        shop_id = listing_data.get("shop_id") or settings.ETSY_SHOP_ID
        try:
            txn_data = await self.get_shop_transactions(
                shop_id=str(shop_id), listing_id=int(listing_id)
            )
            if isinstance(txn_data, dict):
                results = txn_data.get("results", [])
            elif isinstance(txn_data, list):
                results = txn_data
            else:
                results = []
            num_orders = sum(t.get("quantity", 1) for t in results)
        except Exception:
            num_orders = 0

        return {
            "views":       views,
            "clicks":      0,           # Etsy v3 senza Ads API non espone clicks
            "favorites":   favorites,
            "num_orders":  num_orders,
            "revenue_eur": round(num_orders * price_eur, 4),
        }

    async def update_listing(self, listing_id: int, **kwargs: Any) -> dict:
        if self.mock_mode:
            return {}
        shop_id = settings.ETSY_SHOP_ID
        return await self._request(
            "PATCH",
            f"/application/shops/{shop_id}/listings/{listing_id}",
            json_data=kwargs,
        )

    async def get_listings(self, shop_id: str | None = None, limit: int = 100) -> list[dict]:
        if self.mock_mode:
            listings = await self.memory.get_etsy_listings(status="active")
            return listings[:limit]
        sid = shop_id or settings.ETSY_SHOP_ID
        result = await self._request(
            "GET",
            f"/application/shops/{sid}/listings",
            params={"limit": limit},
        )
        return result.get("results", [])
