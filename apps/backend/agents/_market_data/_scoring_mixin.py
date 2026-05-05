"""MarketDataAgent — entry score and seasonal boost mixin."""
from __future__ import annotations

from ._models import MarketSignals
from .constants import (
    SEASONAL_MAP,
    _MAX_RESULT_COUNT,
    _MAX_AVG_REVIEWS,
    _TRENDS_WEIGHT,
    _COMPETITION_TOO_SMALL,
    _COMPETITION_SWEET_LOW,
    _COMPETITION_NORMAL_HIGH,
    _BONUS_SWEET_SPOT,
    _BONUS_NORMAL,
    _BONUS_TOO_SMALL,
    _BONUS_CROWDED,
)


class _ScoringMixin:

    def _compute_entry_score(self, signals: MarketSignals) -> float:
        """
        Entry score — Tier 1 o Tier 1+2 a seconda dei dati disponibili.

        Formula:
            demand_proxy     = etsy_demand                         (Tier 1)
                             | blend(etsy_demand, trends_demand)   (Tier 2)
            competition      = etsy_result_count normalizzato
            competition_bonus = moltiplicatore sweet-spot (Alfie 2026)
            entry_score      = (demand / competition) * seasonal_boost
                               * ac_boost * competition_bonus

        Blending Tier 2:
            demand = (1 - _TRENDS_WEIGHT) * etsy_demand + _TRENDS_WEIGHT * trends_demand
            dove trends_demand = google_trend_score / 100

        Competition bonus (fonte: Alfie):
            < 2k    → 1.00 (niche troppo piccola — possibile falso positivo)
            2k–10k  → 1.25 (sweet spot: domanda reale + bassa competizione)
            10k–50k → 1.00 (range normale — nessun aggiustamento)
            > 50k   → 0.90 (mercato affollato — penalità leggera)

        Score finale in [0.05, 1.0].
        Cold-start (nessun dato) → 0.4 flat.
        """
        if signals.etsy_result_count == 0 and signals.avg_reviews == 0.0:
            return 0.4   # cold-start safe

        # --- demand proxy ---
        etsy_demand = min(signals.avg_reviews / _MAX_AVG_REVIEWS, 1.0)

        if signals.tier >= 2 and signals.google_trend_score > 0:
            trends_demand = signals.google_trend_score / 100.0
            demand = (
                (1 - _TRENDS_WEIGHT) * etsy_demand
                + _TRENDS_WEIGHT * trends_demand
            )
        else:
            demand = etsy_demand

        # --- competition density ---
        competition = min(signals.etsy_result_count / _MAX_RESULT_COUNT, 1.0)
        competition = max(competition, 0.05)   # evita divisione per zero

        raw = demand / competition

        # --- competition bonus — sweet spot Alfie ---
        result_count = signals.etsy_result_count
        if result_count < _COMPETITION_TOO_SMALL:
            competition_bonus = _BONUS_TOO_SMALL
        elif result_count < _COMPETITION_SWEET_LOW:
            competition_bonus = _BONUS_SWEET_SPOT
        elif result_count < _COMPETITION_NORMAL_HIGH:
            competition_bonus = _BONUS_NORMAL
        else:
            competition_bonus = _BONUS_CROWDED

        # autocomplete boost: ogni hit +3%, max +20%
        ac_boost = 1.0 + min(signals.autocomplete_hits * 0.03, 0.20)

        score = raw * signals.seasonal_boost * ac_boost * competition_bonus

        return round(max(0.05, min(score, 1.0)), 3)

    def _get_seasonal_boost(self, niche: str) -> float:
        """
        Ritorna il boost stagionale per la niche basato sul mese corrente.
        Default 1.0 se nessuna chiave corrisponde.
        """
        import datetime as _dt
        current_month = _dt.datetime.now().month
        niche_lower   = niche.lower()

        for keyword, monthly_boosts in SEASONAL_MAP.items():
            if keyword in niche_lower:
                return monthly_boosts.get(current_month, 1.0)
        return 1.0
