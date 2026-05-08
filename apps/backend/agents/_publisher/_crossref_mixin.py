"""PublisherAgent — cross-reference update mixin (C.4)."""
from __future__ import annotations

import logging

from apps.backend.core.production_queue import ProductionQueueService

logger = logging.getLogger("agentpexi.publisher")

_CROSSREF_SEPARATOR = "─" * 25


class _CrossrefMixin:

    async def _update_cluster_crossrefs(
        self,
        cluster_id: str,
        new_listing_id: str,
        new_listing_title: str,
        new_listing_url: str,
    ) -> None:
        """
        Aggiorna le descrizioni di tutti i listing pubblicati dello stesso cluster
        aggiungendo/sostituendo il blocco cross-reference.

        Gates:
        - cluster_id NOT NULL
        - ≥ 2 listing del cluster con status='completed' e etsy_listing_id NOT NULL
        - Non in mock mode (no real Etsy IDs disponibili)
        """
        if getattr(self.memory, "mock_mode", False):
            logger.debug("publisher: cross-ref skip — mock mode")
            return

        pq = ProductionQueueService(await self.memory.get_db())
        cluster_items = await pq.get_cluster_items(cluster_id)
        published = [
            i for i in cluster_items
            if i.status == "completed" and i.etsy_listing_id
        ]

        if len(published) < 2:
            logger.debug(
                "publisher: cross-ref skip — solo %d listing pubblicati nel cluster %s",
                len(published), cluster_id,
            )
            return

        updated_count = 0
        for item in published:
            other_listings = [p for p in published if p.etsy_listing_id != item.etsy_listing_id]
            if not other_listings:
                continue

            crossref_lines = [
                "",
                _CROSSREF_SEPARATOR,
                "You might also like from our shop:",
            ]
            for other in other_listings[:5]:
                crossref_lines.append(
                    f"→ {other.listing_title or 'Related item'} "
                    f"— etsy.com/listing/{other.etsy_listing_id}"
                )
            crossref_lines.append(_CROSSREF_SEPARATOR)
            crossref_block = "\n".join(crossref_lines)

            current_desc = item.listing_description or ""
            if _CROSSREF_SEPARATOR in current_desc:
                current_desc = current_desc[:current_desc.index(_CROSSREF_SEPARATOR)].rstrip()
            new_desc = current_desc + crossref_block

            try:
                await self.etsy_api.patch_listing_description(
                    listing_id=item.etsy_listing_id,
                    description=new_desc,
                )
                if not item.etsy_listing_url:
                    await pq.set_etsy_listing_url(
                        item_id=item.id,
                        url=f"etsy.com/listing/{item.etsy_listing_id}",
                    )
                updated_count += 1
            except Exception as e:
                logger.error(
                    "publisher: PATCH cross-ref fallito per listing %s: %s",
                    item.etsy_listing_id, e,
                )

        if updated_count > 0:
            links_per_listing = min(len(published) - 1, 5)
            await self._notify_telegram(
                f"🔗 Cross-ref aggiornato — cluster {cluster_id[:6]}\n"
                f"{updated_count} listing aggiornati · {links_per_listing} link correlati per listing"
            )
