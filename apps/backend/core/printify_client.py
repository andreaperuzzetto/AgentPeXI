"""PrintifyClient — stub POD per integrazione futura.

Blocco 5 / step 5.5 — scaffolding architetturale

Motivazione:
    Un hybrid shop (digital + POD stessa niche) aumenta AOV e visibilità
    su Etsy (Alfie: "listing diversi, stessa niche, stesso cliente").
    Non implementiamo POD ora, ma prepariamo l'architettura perché
    l'integrazione sia un'aggiunta pulita in Blocco 6+.

Prerequisiti per l'attivazione:
    1. settings.POD_ENABLED = True
    2. settings.PRINTIFY_API_KEY configurata nel .env
    3. Implementare i metodi in NotImplementedError

Tipi di prodotto POD supportati (production_queue.product_type):
    - pod_print   — poster/print su carta
    - pod_mug     — tazza
    - pod_tshirt  — t-shirt

Integrazione prevista (Blocco 6+):
    AutopilotLoop._run_design_pipeline:
        if niche_data.get("pod_companion_type") and settings.POD_ENABLED:
            pod = PrintifyClient(api_key=settings.PRINTIFY_API_KEY)
            listing_id = await pod.create_product(niche, design_url, product_type)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentpexi.printify_client")


class PrintifyClient:
    """
    Client per Printify API — integrazione futura (Blocco 6+).

    Documenta i metodi previsti senza implementarli.
    Tutti i metodi sollevano NotImplementedError finché POD non è attivo.

    Utilizzo:
        from apps.backend.core.printify_client import PrintifyClient
        from apps.backend.core.config import settings

        if settings.POD_ENABLED:
            client = PrintifyClient(api_key=settings.PRINTIFY_API_KEY)
            listing_id = await client.create_product(niche, design_url, "pod_print")
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key
        if not api_key:
            logger.warning(
                "PrintifyClient istanziato senza API key — "
                "impostare PRINTIFY_API_KEY nel .env prima di attivare POD."
            )

    async def create_product(
        self,
        niche: str,
        design_url: str,
        product_type: str,
    ) -> str:
        """
        Crea un prodotto POD su Printify e lo pubblica su Etsy.

        Args:
            niche:        Nome della niche (es. "wedding planner").
            design_url:   URL del file design (PNG/SVG) già caricato su storage.
            product_type: Tipo prodotto POD — "pod_print" | "pod_mug" | "pod_tshirt".

        Returns:
            listing_id Etsy del prodotto pubblicato.

        Raises:
            NotImplementedError — da implementare nel Blocco 6+.
        """
        raise NotImplementedError(
            "POD integration not yet available. "
            "Set POD_ENABLED=True and implement PrintifyClient.create_product() in Blocco 6+."
        )

    async def get_product_status(self, printify_product_id: str) -> dict:
        """
        Recupera lo stato di un prodotto POD da Printify.

        Args:
            printify_product_id: ID prodotto su Printify.

        Returns:
            dict con status, etsy_listing_id, fulfilled_at, ecc.

        Raises:
            NotImplementedError — da implementare nel Blocco 6+.
        """
        raise NotImplementedError

    async def list_products(
        self,
        shop_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Lista prodotti POD nello shop Printify.

        Raises:
            NotImplementedError — da implementare nel Blocco 6+.
        """
        raise NotImplementedError

    async def update_product(
        self,
        printify_product_id: str,
        updates: dict[str, Any],
    ) -> dict:
        """
        Aggiorna titolo, descrizione o varianti di un prodotto POD.

        Raises:
            NotImplementedError — da implementare nel Blocco 6+.
        """
        raise NotImplementedError

    async def delete_product(self, printify_product_id: str) -> bool:
        """
        Elimina un prodotto POD (e il relativo listing Etsy se sincronizzato).

        Raises:
            NotImplementedError — da implementare nel Blocco 6+.
        """
        raise NotImplementedError
