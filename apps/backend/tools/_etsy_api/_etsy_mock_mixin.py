"""EtsyAPI — mock implementations mixin."""
from __future__ import annotations

import logging
import random
import time as _time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("agentpexi.etsy_api")


class _MockMixin:

    # ------------------------------------------------------------------
    # Mock implementations — usati quando self.mock_mode is True
    # ------------------------------------------------------------------

    def _mock_listing_id(self) -> str:
        """Genera listing_id mock univoco."""
        return f"MOCK_{int(_time.time())}_{random.randint(1000, 9999)}"

    async def _mock_create_listing(self, title: str, price: float, tags: list[str], **kwargs) -> dict:
        """Simula creazione listing Etsy — salva nel DB locale."""
        listing_id = self._mock_listing_id()
        return {
            "listing_id": listing_id,
            "title": title,
            "description": kwargs.get("description", ""),
            "price": {"amount": int(price * 100), "divisor": 100, "currency_code": "EUR"},
            "tags": tags,
            "state": "active",
            "views": 0,
            "num_favorers": 0,
            "quantity": 999,
            "is_digital": True,
            "url": f"https://www.etsy.com/listing/{listing_id}/mock-product",
            "creation_timestamp": int(_time.time()),
            "shop_id": "MOCK_SHOP_001",
        }

    async def _mock_upload_file(self, listing_id: int | str, file_path: str, name: str) -> dict:
        """Simula upload file — no-op, ritorna success."""
        return {
            "listing_file_id": f"MOCKFILE_{int(_time.time())}",
            "listing_id": str(listing_id),
            "filename": name,
            "filesize": "1.2 MB",
            "filetype": "application/pdf",
            "create_timestamp": int(_time.time()),
        }

    async def _mock_upload_image(self, listing_id: int | str, file_path: str) -> dict:
        """Simula upload immagine thumbnail — no-op, ritorna success."""
        import os as _os
        name = _os.path.basename(file_path)
        return {
            "listing_image_id": f"MOCKIMG_{int(_time.time())}_{random.randint(100, 999)}",
            "listing_id": str(listing_id),
            "url_75x75": f"https://mock.etsy.com/images/{name}?w=75",
            "url_fullxfull": f"https://mock.etsy.com/images/{name}",
            "is_watermarked": False,
            "creation_tsz": int(_time.time()),
        }

    async def _mock_get_listing(self, listing_id: int | str) -> dict:
        """Legge listing dal DB locale + aggiunge drift views."""
        try:
            listings = await self.memory.get_etsy_listings()
            listing = next(
                (l for l in listings if str(l.get("listing_id")) == str(listing_id)),
                None
            )
        except Exception:
            listing = None

        if listing:
            current_views = listing.get("views", 0)
            view_drift = random.randint(0, 15)
            return {
                "listing_id": str(listing_id),
                "title": listing.get("title", "Mock Product"),
                "price": {
                    "amount": int(listing.get("price_eur", 4.99) * 100),
                    "divisor": 100,
                    "currency_code": "EUR",
                },
                "state": listing.get("status", "active"),
                "views": current_views + view_drift,
                "num_favorers": listing.get("favorites", 0) + random.randint(0, 3),
                "shop_id": "MOCK_SHOP_001",
            }

        return {
            "listing_id": str(listing_id),
            "title": "Mock Product",
            "price": {"amount": 499, "divisor": 100, "currency_code": "EUR"},
            "state": "active",
            "views": random.randint(10, 150),
            "num_favorers": random.randint(0, 20),
            "shop_id": "MOCK_SHOP_001",
        }

    async def _mock_get_listing_stats(self, listing_id: int | str) -> dict:
        """
        Simula stats listing con distribuzione realistica (fonte: Alfie).
        CTR medio Etsy 2026: ~2-4%. Conversion su click: ~0.5-3%.
        """
        try:
            listings = await self.memory.get_etsy_listings()
            listing  = next(
                (l for l in listings if str(l.get("listing_id")) == str(listing_id)),
                None,
            )
            base_views = listing.get("views", 0) + random.randint(0, 20) if listing else random.randint(10, 200)
            price_eur  = listing.get("price_eur", 4.99) if listing else 4.99
        except Exception:
            base_views = random.randint(10, 200)
            price_eur  = 4.99

        views      = max(0, base_views)
        # CTR gaussiana troncata: media 2.5%, deviazione 1.2%, range [0.5%, 6%]
        ctr        = max(0.005, min(0.06, random.gauss(0.025, 0.012)))
        clicks     = max(0, int(views * ctr))
        # Conversion su click: media 1.8%, deviazione 0.8%
        conv_rate  = max(0.005, min(0.04, random.gauss(0.018, 0.008)))
        num_orders = max(0, int(clicks * conv_rate))
        favorites  = max(0, int(clicks * random.uniform(0.15, 0.45)))

        return {
            "views":       views,
            "clicks":      clicks,
            "favorites":   favorites,
            "num_orders":  num_orders,
            "revenue_eur": round(num_orders * price_eur, 4),
        }

    async def _mock_get_shop_transactions(
        self, shop_id: str | None = None, listing_id: int | None = None
    ) -> dict:
        """
        Simula transazioni realistiche.
        Distribuzione: 60% → 0 vendite, 25% → 1-2, 10% → 3-5, 5% → 6-10.
        """
        roll = random.random()
        if roll < 0.60:
            num_sales = 0
        elif roll < 0.85:
            num_sales = random.randint(1, 2)
        elif roll < 0.95:
            num_sales = random.randint(3, 5)
        else:
            num_sales = random.randint(6, 10)

        results = []
        for i in range(num_sales):
            results.append({
                "transaction_id": f"MOCKTX_{int(_time.time())}_{i}",
                "listing_id": str(listing_id) if listing_id else "0",
                "quantity": 1,
                "price": {"amount": 499, "divisor": 100, "currency_code": "EUR"},
                "create_timestamp": int(_time.time()) - random.randint(0, 86400 * 30),
            })

        return {"count": num_sales, "results": results}

    async def _mock_get_shop(self, shop_id: str | None = None) -> dict:
        """Shop info mock."""
        return {
            "shop_id": "MOCK_SHOP_001",
            "shop_name": "AgentPeXI Mock Shop",
            "title": "Digital Products by AgentPeXI",
            "listing_active_count": 0,
            "currency_code": "EUR",
            "is_vacation": False,
            "url": "https://www.etsy.com/shop/AgentPeXIMock",
        }

    async def _mock_update_shop(
        self,
        title: str | None = None,
        announcement: str | None = None,
    ) -> dict:
        """Mock update shop — ritorna i campi aggiornati."""
        return {
            "shop_id":      "MOCK_SHOP_001",
            "shop_name":    "AgentPeXI Mock Shop",
            "title":        title or "Digital Products by AgentPeXI",
            "announcement": announcement or "",
            "mock":         True,
        }

    async def _mock_check_auth_status(self) -> dict:
        """Mock auth — sempre autenticato."""
        return {
            "authenticated": True,
            "expired": False,
            "mock": True,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }

    async def _mock_create_ad_campaign(
        self,
        listing_id: str | int,
        daily_budget_eur: float,
    ) -> dict:
        """Mock attivazione campagna ads."""
        logger.info(
            "[MOCK] Ads activated — listing %s, budget €%.2f/day",
            listing_id, daily_budget_eur,
        )
        return {
            "listing_id":   str(listing_id),
            "daily_budget": daily_budget_eur,
            "status":       "active",
            "mock":         True,
        }

    async def _mock_pause_ad_campaign(self, listing_id: str | int) -> dict:
        """Mock pausa campagna ads."""
        logger.info("[MOCK] Ads paused — listing %s", listing_id)
        return {
            "listing_id": str(listing_id),
            "status":     "paused",
            "mock":       True,
        }

    async def _mock_get_listing_ad_stats(self, listing_id: str | int) -> dict:
        """Mock statistiche ads — valori realistici deterministici per listing_id."""
        import random as _r
        rng = _r.Random(hash(str(listing_id)) % 100_000)
        impressions = rng.randint(50, 600)
        clicks      = rng.randint(0, max(1, impressions // 15))
        return {
            "listing_id":  str(listing_id),
            "impressions": impressions,
            "clicks":      clicks,
            "spend_eur":   round(rng.uniform(0.30, 3.00), 2),
            "orders":      rng.randint(0, 2),
            "mock":        True,
        }
