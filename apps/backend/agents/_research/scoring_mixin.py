"""ResearchAgent — scoring mixin."""
from __future__ import annotations

from typing import Any


class _ResearchScoringMixin:

    @staticmethod
    def _calculate_confidence(
        data_sources: dict[str, str],
        output: dict,
    ) -> tuple[float, list[str]]:
        """
        Confidence 0.0-1.0 basata su:
        - Qualità fonti dati (55%)
        - Completezza output selling-critical (45%)
        """
        score = 0.0
        missing: list[str] = []

        # === PARTE 1: Qualità fonti dati (55% del totale) ===

        # Entry point scoring (peso 0.15) — dati strutturati da MarketDataAgent
        # Aggiunge confidenza quando abbiamo segnali di mercato reali pre-LLM
        entry_src = data_sources.get("entry_point", "none")
        if entry_src == "market_signals":
            score += 0.15
        # Se assente: score non penalizzato (source opzionale)

        # Pricing (peso 0.20)
        pricing_src = data_sources.get("pricing", "")
        if pricing_src in ("etsy_api", "etsy_extract", "cached"):
            score += 0.20
        elif pricing_src == "blog_inference":
            score += 0.06
            missing.append("prezzi reali da listing Etsy")
        elif pricing_src == "llm_inference":
            score += 0.02
            missing.append("qualsiasi dato prezzo reale")

        # Trend (peso 0.15)
        trend_src = data_sources.get("trend", "")
        if trend_src in ("google_trends", "cached"):
            score += 0.15
        else:
            score += 0.03
            missing.append("dati trend Google Trends")

        # Keywords (peso 0.12)
        kw_src = data_sources.get("keywords", "")
        if kw_src in ("erank_content", "cached", "erank_api"):
            score += 0.12
        elif kw_src == "community_search":
            score += 0.07
        elif kw_src == "llm_inference":
            score += 0.03
            missing.append("volume keyword reale da eRank o community")

        # Competitors (peso 0.08)
        comp_src = data_sources.get("competitors", "")
        if comp_src in ("etsy_api", "etsy_extract", "cached"):
            score += 0.08
        elif comp_src == "blog_mention":
            score += 0.04
            missing.append("dati competitor reali con metriche shop")

        # === PARTE 2: Completezza output selling-critical (45% del totale) ===

        niches = output.get("niches", [])
        viable_niches = [n for n in niches if n.get("viable", True)]

        if not viable_niches:
            missing.append("nessuna nicchia viable trovata")
            return round(min(score, 1.0), 2), missing

        sample = viable_niches[0]

        # 13 tag presenti e validi (peso 0.15)
        tags = sample.get("etsy_tags_13", [])
        if len(tags) == 13:
            score += 0.15
        elif len(tags) >= 8:
            score += 0.08
            missing.append(f"solo {len(tags)}/13 tag Etsy generati")
        else:
            score += 0.02
            missing.append(f"tag insufficienti: {len(tags)}/13 — listing non pubblicabile")

        # Selling signals presenti (peso 0.15)
        selling = sample.get("selling_signals", {})
        selling_complete = all([
            selling.get("thumbnail_style"),
            selling.get("conversion_triggers"),
            selling.get("bundle_vs_single"),
            selling.get("first_listing_recommendation"),
        ])
        if selling_complete:
            score += 0.15
        elif selling:
            score += 0.07
            missing.append("selling signals incompleti (thumbnail style o conversion triggers mancanti)")
        else:
            score += 0.01
            missing.append("selling signals assenti — Design Agent lavora senza guida visiva")

        # Pricing specifico per conversione (peso 0.10)
        pricing = sample.get("pricing", {})
        if pricing.get("conversion_sweet_spot_usd") and pricing.get("launch_price_usd"):
            score += 0.10
        elif pricing.get("conversion_sweet_spot_usd") or pricing.get("sweet_spot_usd"):
            score += 0.05
            missing.append("launch price strategy mancante")
        else:
            score += 0.01
            missing.append("pricing strategico assente")

        # Seasonal timing (peso 0.05)
        if sample.get("demand", {}).get("peak_months") and sample.get("demand", {}).get("publish_timing_advice"):
            score += 0.05
        else:
            score += 0.01
            missing.append("timing stagionale non specificato")

        # audience_target mandatory (schema v2)
        # Absent → cap score at 0.4; without audience targeting A.0 filtering is unreliable.
        if not sample.get("audience_target", "").strip():
            missing.append("audience_target assente — cap confidence a 0.4")
            score = min(score, 0.40)

        return round(min(score, 1.0), 2), missing
