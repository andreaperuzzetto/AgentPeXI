"""PublisherAgent — confidence, status, pricing, and seasonal context mixin."""
from __future__ import annotations

import datetime
import logging

from apps.backend.agents._publisher.constants import AB_PRICES
from apps.backend.core.config import settings
from apps.backend.core.models import TaskStatus

logger = logging.getLogger("agentpexi.publisher")


class _ResolveMixin:

    def _calculate_publish_confidence(
        self, results: list[dict], task_context: dict,
    ) -> tuple[float, list[str]]:
        """Calcola confidence basata su qualità oggettiva del publishing."""
        missing: list[str] = []
        score = 0.0

        # 40% — dati Research presenti e usati
        research_score = 0.0
        if task_context.get("etsy_tags_13"):
            research_score += 0.20
        else:
            missing.append("etsy_tags_13 mancanti da Research Agent")
        if task_context.get("selling_signals"):
            research_score += 0.10
        else:
            missing.append("selling_signals mancanti da Research Agent")
        if task_context.get("pricing", {}).get("launch_price_usd"):
            research_score += 0.10
        else:
            missing.append("pricing da Research mancante — usato prezzo hardcoded")
        score += research_score

        # 35% — success rate listing pubblicati
        if results:
            successful = sum(1 for r in results if r.get("listing_id"))
            success_rate = successful / len(results)
            score += 0.35 * success_rate
            if success_rate < 1.0:
                missing.append(f"{len(results) - successful} listing su {len(results)} falliti")
        else:
            missing.append("Nessun listing pubblicato")

        # 15% — thumbnail caricate
        if results:
            with_images = sum(1 for r in results if r.get("images_uploaded", 0) > 0)
            image_rate = with_images / len(results)
            score += 0.15 * image_rate
            if image_rate < 1.0:
                missing.append(f"{len(results) - with_images} listing senza thumbnail")

        # 10% — SEO validato
        if results:
            valid_seo = sum(1 for r in results if r.get("seo_validated", False))
            score += 0.10 * (valid_seo / len(results))

        return round(score, 2), missing

    def _calculate_status(self, results: list[dict]) -> TaskStatus:
        """COMPLETED: 100% pubblicati. PARTIAL: 50-99%. FAILED: <50% o 0."""
        if not results:
            return TaskStatus.FAILED

        successful = sum(1 for r in results if r.get("listing_id"))
        total = len(results)
        ratio = successful / total

        if ratio == 1.0:
            return TaskStatus.COMPLETED
        elif ratio >= 0.5:
            return TaskStatus.PARTIAL
        else:
            return TaskStatus.FAILED

    def _resolve_price(self, file_type: str, research_data: dict, variant: str = "a") -> float:
        """Usa il prezzo da Research se disponibile, fallback su AB_PRICES.

        ATTENZIONE: il valore restituito è in ETSY_SHOP_CURRENCY (default EUR).
        AB_PRICES e le conversioni USD→EUR assumono che il tuo shop Etsy sia in EUR.
        Se ETSY_SHOP_CURRENCY != "EUR", i prezzi hardcoded e il rate di conversione
        devono essere ricalibrati.
        """
        if settings.ETSY_SHOP_CURRENCY != "EUR":
            logger.warning(
                "ETSY_SHOP_CURRENCY='%s' ma i prezzi sono calibrati in EUR. "
                "Verificare AB_PRICES e il rate di conversione USD→EUR in config.",
                settings.ETSY_SHOP_CURRENCY,
            )

        pricing = research_data.get("pricing", {})

        if variant.lower() == "a" and pricing.get("launch_price_usd"):
            usd = float(pricing["launch_price_usd"])
            return round(usd * settings.USD_EUR_RATE, 2)
        elif variant.lower() == "b" and pricing.get("mature_price_usd"):
            usd = float(pricing["mature_price_usd"])
            return round(usd * settings.USD_EUR_RATE, 2)

        # Fallback su AB_PRICES (valori in EUR)
        ab_key = variant.upper()
        prices = AB_PRICES.get(file_type, AB_PRICES["printable_pdf"])
        return prices.get(ab_key, prices["A"])

    def _get_when_made(self) -> str:
        """Ritorna valore 'when_made' valido per l'API Etsy.

        Prodotti digitali generati da AI → 'made_to_order' è semanticamente corretto
        e sempre valido indipendentemente dall'anno.

        Enum accettati da spec ufficiale (OAS 3.0):
            made_to_order, 2020_2026, 2010_2019, 2007_2009, before_2007, ...
        NON esistono range arbitrari tipo '2025_2026' — causerebbero HTTP 400.
        """
        return "made_to_order"

    def _get_seasonal_context(self) -> dict:
        """Ritorna season e keyword rilevanti per il mese corrente."""
        month = datetime.datetime.now().month
        seasonal_map = {
            1: {"season": "New Year", "keywords": ["new year goals", "fresh start", "2026 planner"]},
            2: {"season": "Valentine's", "keywords": ["gift idea", "printable gift", "love"]},
            3: {"season": "Spring", "keywords": ["spring refresh", "organization", "spring cleaning"]},
            4: {"season": "Spring/Easter", "keywords": ["spring", "productivity", "goal setting"]},
            5: {"season": "Mother's Day", "keywords": ["gift for mom", "printable gift", "mothers day"]},
            6: {"season": "Summer", "keywords": ["summer planning", "vacation tracker", "summer goals"]},
            7: {"season": "Midyear Review", "keywords": ["mid year review", "goal check-in", "halfway goals"]},
            8: {"season": "Back to School", "keywords": ["back to school", "student planner", "study tracker"]},
            9: {"season": "Fall/Q4 Prep", "keywords": ["fall planning", "q4 goals", "autumn organizer"]},
            10: {"season": "Halloween/Q4", "keywords": ["october", "halloween", "end of year planning"]},
            11: {"season": "Thanksgiving/Black Friday", "keywords": ["gratitude", "holiday planner", "gift guide"]},
            12: {"season": "Christmas/Year End", "keywords": ["christmas gift", "year in review", "holiday organizer"]},
        }
        return seasonal_map.get(month, {"season": "General", "keywords": []})
