"""_GenerationMixin — generazione pin Pinterest da listing Etsy.

Pipeline per ogni listing:
1. Selezione varianti (A sempre, B sempre, C/D/E condizionali)
2. Generazione immagine via DesignAgent (stub — fal.ai in B-06+)
3. Generazione titolo + descrizione via Haiku (AGT-6.1 framework)
4. Schedulazione 7-day spread in pinterest_queue
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.backend.core.config import MODEL_HAIKU

logger = logging.getLogger("agentpexi.pinterest.generation")

# 7-day spread offsets (indice = posizione slot, 0-based)
_SPREAD_OFFSETS: list[timedelta] = [
    timedelta(hours=1),   # Slot 1: PIN 1 → NOW+1h (Variant A)
    timedelta(days=1),    # Slot 2: PIN 2 → +1d  (Variant B)
    timedelta(days=2),    # Slot 3: PIN 3 → +2d  (Variant C/D se generati)
    timedelta(days=4),    # Slot 4: PIN 4 → +4d  (Variant A/B ripub)
    timedelta(days=6),    # Slot 5: PIN 5 → +6d  (Variant E o B)
]

# Nomi human-readable per le varianti (usati nel prompt)
_VARIANT_NAMES: dict[int, str] = {
    1: "Lifestyle mockup + audience hook (Variant A)",
    2: "Flat lay prodotto + feature callout (Variant B)",
    3: "Editorial / text-heavy (Variant C)",
    4: "Problem/solution format (Variant D)",
    5: "Cluster showcase (Variant E)",
}

# AGT-6.1: system prompt per pin description (Pinterest visual search)
_AGT_6_1_SYSTEM_PROMPT = """\
PIN DESCRIPTION FRAMEWORK — Pinterest visual search (non social media):

PRINCIPIO FONDAMENTALE: Pinterest è un motore di ricerca visuale.
Le keyword nella descrizione influenzano il ranking nella ricerca Pinterest.
NON scrivere come caption social — scrivi come meta description SEO.

STRUTTURA OBBLIGATORIA (150-250 char, NESSUN hashtag — policy Pinterest 2026):
[Hook visivo] + [Keyword primaria audience-level] + [Benefit concreto] + [CTA implicita]

TITOLO (max 100 caratteri): audience-centered, non product-centered.
  ❌ "ADHD Daily Planner Printable PDF"
  ✅ "The Planner That Finally Works for ADHD Brains"

Rispondi ESCLUSIVAMENTE con JSON valido, nessun testo extra:
{
  "title": "...",
  "description": "..."
}
"""


class _GenerationMixin:
    """Mixin generazione pin: variant selection, copy, image, scheduling."""

    # ------------------------------------------------------------------
    # Variant selection
    # ------------------------------------------------------------------

    def _select_variants(self, listing_data: dict) -> list[int]:
        """Determina quali varianti pin generare per questo listing.

        Regole (AGT-6.2):
        - A (1): sempre
        - B (2): sempre
        - C (3): se selling_signals.thumbnail_style == "editorial"
        - D (4): se gap_to_exploit contiene "pain" (case-insensitive)
        - E (5): solo se cluster_size >= 3
        """
        variants = [1, 2]

        selling_signals: dict = listing_data.get("selling_signals") or {}
        if selling_signals.get("thumbnail_style") == "editorial":
            variants.append(3)  # C — Editorial

        gap_to_exploit: str = listing_data.get("gap_to_exploit") or ""
        if "pain" in gap_to_exploit.lower():
            variants.append(4)  # D — Problem/solution

        cluster_size: int = listing_data.get("cluster_size") or 0
        if cluster_size >= 3:
            variants.append(5)  # E — Cluster showcase

        return variants

    # ------------------------------------------------------------------
    # Image generation (stub — DesignAgent integration successiva)
    # ------------------------------------------------------------------

    async def _generate_pin_image(self, variant: int, listing_data: dict) -> tuple[str, dict]:
        """Genera immagine pin 1000×1500px via DesignAgent.

        Output: (image_path, {"cost_image_gen": float})
        Implementazione completa in B-06+ (DesignAgent + fal.ai).
        """
        logger.debug("[gen/image] stub per variant=%d listing=%s", variant, listing_data.get("listing_id"))
        return "", {"cost_image_gen": 0.0}

    # ------------------------------------------------------------------
    # Copy generation (Haiku — AGT-6.1)
    # ------------------------------------------------------------------

    async def _generate_pin_copy(
        self, variant: int, listing_data: dict
    ) -> tuple[str, str, dict]:
        """Genera titolo (≤100 char) e descrizione (150-250 char) via Haiku.

        Usa AGT-6.1 framework. Nessun hashtag.

        Returns:
            (title, description, {"cost_llm": float, "cost_image_gen": float})
        """
        variant_name = _VARIANT_NAMES.get(variant, f"Variant {variant}")
        user_content = (
            f"Genera titolo e descrizione Pinterest per questo listing.\n\n"
            f"Prodotto: {listing_data.get('title', '')}\n"
            f"Niche: {listing_data.get('niche', '')}\n"
            f"Sezione: {listing_data.get('section_key', '')}\n"
            f"Audience: {listing_data.get('audience_target', '')}\n"
            f"Trigger: {', '.join(listing_data.get('conversion_triggers') or [])}\n"
            f"Tipo variante: {variant_name}\n"
        )

        raw = await self._call_llm(  # type: ignore[attr-defined]
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_AGT_6_1_SYSTEM_PROMPT,
            model_override=MODEL_HAIKU,
        )

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("[gen/copy] risposta Haiku non JSON per variant=%d: %r", variant, raw[:200])
            data = {}

        title: str = str(data.get("title") or "")[:100]
        description: str = str(data.get("description") or "")
        description = re.sub(r"#\w+", "", description).strip()

        logger.debug("[gen/copy] variant=%d title=%d chars desc=%d chars", variant, len(title), len(description))

        return title, description, {"cost_llm": 0.0, "cost_image_gen": 0.0}

    # ------------------------------------------------------------------
    # Scheduling (7-day spread → pinterest_queue)
    # ------------------------------------------------------------------

    async def _schedule_pins(self, listing_data: dict, pins: list[dict]) -> list[int]:
        """Inserisce i pin in pinterest_queue con 7-day spread.

        Slot offsets: +1h, +1d, +2d, +4d, +6d (fino a 5 slot).

        Returns:
            Lista di ID (lastrowid) degli insert.
        """
        now = datetime.now(timezone.utc)
        board_id: str = listing_data.get("board_id") or ""
        pq_id: Any = listing_data.get("production_queue_id")

        db = await self.memory.get_db()  # type: ignore[attr-defined]
        ids: list[int] = []

        for idx, pin in enumerate(pins[:5]):
            offset = _SPREAD_OFFSETS[idx] if idx < len(_SPREAD_OFFSETS) else timedelta(days=idx + 1)
            scheduled_at = (now + offset).isoformat()

            cursor = await db.execute(
                """
                INSERT INTO pinterest_queue
                  (production_queue_id, pin_variant, image_path, title, description,
                   board_id, scheduled_at, cost_image_gen, cost_llm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pq_id,
                    pin["variant"],
                    pin.get("image_path", ""),
                    pin.get("title", ""),
                    pin.get("description", ""),
                    board_id,
                    scheduled_at,
                    pin.get("cost_image_gen", 0.0),
                    pin.get("cost_llm", 0.0),
                ),
            )
            ids.append(cursor.lastrowid)

        await db.commit()

        logger.info("[gen/schedule] %d pin schedulati per listing=%s", len(ids), listing_data.get("listing_id"))
        return ids

    # ------------------------------------------------------------------
    # Orchestratore principale
    # ------------------------------------------------------------------

    async def generate_pins(self, listing_data: dict) -> list[dict]:
        """Genera varianti pin per un listing e le schedula.

        Flusso:
        1. Selezione varianti → [1, 2, ...5]
        2. Per ogni variante: genera immagine + copy
        3. Schedula 7-day spread in pinterest_queue

        Args:
            listing_data: dict con listing_id, title, niche, section_key,
                          audience_target, conversion_triggers, selling_signals,
                          gap_to_exploit, cluster_size, board_id, production_queue_id.

        Returns:
            list[dict] con variant, image_path, title, description, cost_image_gen,
            cost_llm, queue_id per ogni pin generato.
        """
        listing_id = listing_data.get("listing_id", "unknown")
        logger.info("[gen] avvio generazione pin per listing=%s", listing_id)

        variants = self._select_variants(listing_data)
        logger.debug("[gen] varianti selezionate: %s", variants)

        pins: list[dict] = []
        for variant in variants:
            image_path, image_costs = await self._generate_pin_image(variant, listing_data)
            title, description, copy_costs = await self._generate_pin_copy(variant, listing_data)
            pins.append(
                {
                    "variant": variant,
                    "image_path": image_path,
                    "title": title,
                    "description": description,
                    "cost_image_gen": image_costs.get("cost_image_gen", 0.0),
                    "cost_llm": copy_costs.get("cost_llm", 0.0),
                }
            )

        pin_ids = await self._schedule_pins(listing_data, pins)
        for i, pid in enumerate(pin_ids):
            if i < len(pins):
                pins[i]["queue_id"] = pid

        logger.info("[gen] %d pin generati e schedulati per listing=%s", len(pins), listing_id)
        return pins
