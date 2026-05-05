"""MarketDataAgent — mock data mixin."""
from __future__ import annotations

import random

from ._models import MarketSignals


class _MockMixin:

    def _mock_tier1(
        self,
        niche: str,
        product_type: str | None,
    ) -> MarketSignals:
        """
        Genera dati Tier 1 simulati realistici per test/sviluppo.
        Usa un seed deterministico sulla niche per risultati stabili.
        """
        seed = sum(ord(c) for c in niche)
        rng  = random.Random(seed)

        # Simula tre tipi di niche: satura, media, nicchia vuota
        scenario = seed % 3
        if scenario == 0:   # niche satura
            count      = rng.randint(40_000, 80_000)
            avg_favs   = rng.uniform(80, 200)
            avg_price  = rng.uniform(3.5, 8.0)
            ac_hits    = rng.randint(6, 10)
        elif scenario == 1: # niche media — sweet spot
            count      = rng.randint(8_000, 30_000)
            avg_favs   = rng.uniform(30, 100)
            avg_price  = rng.uniform(5.0, 15.0)
            ac_hits    = rng.randint(3, 7)
        else:               # niche vuota / emergente
            count      = rng.randint(500, 5_000)
            avg_favs   = rng.uniform(2, 25)
            avg_price  = rng.uniform(4.0, 12.0)
            ac_hits    = rng.randint(0, 3)

        return MarketSignals(
            niche              = niche,
            product_type       = product_type,
            etsy_result_count  = count,
            avg_reviews        = round(avg_favs, 1),
            avg_price_eur      = round(avg_price, 2),
            autocomplete_hits  = ac_hits,
            tier               = 1,
        )

    def _mock_tier2(self, niche: str) -> dict[str, float]:
        """
        Genera un Google Trends score simulato.
        Stesso seed deterministico di _mock_tier1 per coerenza.
        """
        seed  = sum(ord(c) for c in niche)
        rng   = random.Random(seed + 1)   # +1 per diversificare dal Tier 1
        score = round(rng.triangular(10, 100, 45), 1)
        return {"score": score}
