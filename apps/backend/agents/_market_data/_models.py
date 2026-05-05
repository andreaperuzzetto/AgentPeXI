"""MarketDataAgent — MarketSignals dataclass."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MarketSignals:
    """Segnali di mercato raccolti per una niche."""

    niche: str
    product_type: str | None = None

    # Tier 1 — Etsy
    etsy_result_count: int   = 0
    avg_reviews: float       = 0.0
    avg_price_eur: float     = 0.0
    autocomplete_hits: int   = 0     # quante suggestions includono la keyword

    # Tier 2 — Google Trends (popolato in step 1.3)
    google_trend_score: float  = 0.0
    erank_search_volume: int   = 0

    # Scoring
    entry_score: float    = 0.0
    seasonal_boost: float = 1.0

    # Meta
    tier: int           = 1
    collected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche":               self.niche,
            "product_type":        self.product_type,
            "etsy_result_count":   self.etsy_result_count,
            "avg_reviews":         self.avg_reviews,
            "avg_price_eur":       self.avg_price_eur,
            "autocomplete_hits":   self.autocomplete_hits,
            "google_trend_score":  self.google_trend_score,
            "erank_search_volume": self.erank_search_volume,
            "entry_score":         self.entry_score,
            "seasonal_boost":      self.seasonal_boost,
            "tier":                self.tier,
            "collected_at":        self.collected_at,
        }
