"""AnalyticsAgent — bestsellers mixin."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("agentpexi.analytics")


class _AnalyticsBestsellersMixin:

    # ------------------------------------------------------------------
    # Passo 4 — Bestseller e proposte varianti
    # ------------------------------------------------------------------

    async def _find_bestsellers(self) -> list[dict]:
        """Identifica bestseller con soglia dinamica, propone varianti via pending_action."""
        all_listings = await self.memory.get_etsy_listings(status="active")

        total_sales_all = sum((l.get("sales") or 0) for l in all_listings)
        avg_sales = total_sales_all / max(len(all_listings), 1)
        # Soglia dinamica: almeno 2 vendite, o 50% sopra la media, cap a 10
        threshold = min(10, max(2, avg_sales * 1.5))

        top = sorted(
            [l for l in all_listings if (l.get("sales") or 0) >= threshold],
            key=lambda x: x.get("revenue_eur", 0),
            reverse=True,
        )[:3]

        bestsellers = []
        for lst in top:
            lid = lst["listing_id"]
            niche = lst.get("niche", "")
            template = lst.get("template", "")
            color_scheme = lst.get("color_scheme", "")
            bestsellers.append({
                "listing_id": lid,
                "title": lst.get("title", ""),
                "sales": lst.get("sales", 0),
                "revenue_eur": lst.get("revenue_eur", 0),
            })

            # Nota: il segnale ChromaDB per template/colore vincenti viene scritto da
            # pepe.py (_store_design_winner via _handle_learning_loop) come tipo
            # "design_winner" — effettivamente letto da Design e Finance.
            # "success_pattern" era ridondante e non letto da nessun agente.

            # Controlla se già esiste un pending_action per questo listing
            existing = await self.memory.get_pending_action("production_queue_proposal")
            if existing and existing.get("payload", {}).get("listing_id") == lid:
                continue

            payload = {
                "listing_id": lid,
                "listing_title": lst.get("title", ""),
                "niche": lst.get("niche", ""),
                "template": lst.get("template", ""),
                "product_type": lst.get("product_type", ""),
                "sales": lst.get("sales", 0),
                "revenue_eur": lst.get("revenue_eur", 0),
                "color_scheme": lst.get("color_scheme", ""),
            }
            await self.memory.save_pending_action(
                "production_queue_proposal", payload, expires_hours=24
            )

            title = lst.get("title", "")[:60]
            sales = lst.get("sales", 0)
            revenue = lst.get("revenue_eur", 0)
            msg = (
                f"💡 Opportunità variante identificata\n"
                f"📦 {title}\n"
                f"📊 {sales} vendite · €{revenue:.2f} revenue\n\n"
                f"Questo prodotto funziona. Prova una variante con\n"
                f"schema colore diverso o formato alternativo (es. Letter\n"
                f"invece di A4, o palette terracotta invece di sage).\n\n"
                f"Vuoi metterla in coda di produzione?\n"
                f"Rispondi \"sì\" per aggiungerla o \"no\" per ignorare.\n"
                f"(proposta valida 24 ore)\n\n"
                f"#bestseller #variante"
            )
            await self._notify_telegram(msg)

        return bestsellers

    async def _write_design_outcomes(
        self,
        niche: str,
        template: str,
        color_scheme: str,
        performance: str,
        summary: str,
    ) -> str | None:
        """Salva design outcome in ChromaDB per il learning loop."""
        return await self.memory.store_insight(
            text=(
                f"DESIGN_OUTCOME niche: {niche} | template: {template} | "
                f"color_scheme: {color_scheme} | performance: {performance} | "
                f"{summary}"
            ),
            metadata={
                "type": "design_outcome",
                "niche": niche,
                "template": template,
                "color_scheme": color_scheme,
                "performance": performance,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            },
        )
