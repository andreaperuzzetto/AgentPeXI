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

        # Evaluate ALL viable niches; use the worst-case completeness contribution
        # so that any incomplete niche lowers the overall confidence score.
        best_completeness: float | None = None
        worst_audience_capped = False
        for sample in viable_niches:
            completeness = 0.0
            sample_missing: list[str] = []

            # 13 tag presenti e validi (peso 0.15)
            tags = sample.get("etsy_tags_13", [])
            if len(tags) == 13:
                completeness += 0.15
            elif len(tags) >= 8:
                completeness += 0.08
                sample_missing.append(f"solo {len(tags)}/13 tag Etsy generati")
            else:
                completeness += 0.02
                sample_missing.append(f"tag insufficienti: {len(tags)}/13 — listing non pubblicabile")

            # Selling signals presenti (peso 0.15)
            selling = sample.get("selling_signals", {})
            selling_complete = all([
                selling.get("thumbnail_style"),
                selling.get("conversion_triggers"),
                selling.get("bundle_vs_single"),
                selling.get("first_listing_recommendation"),
            ])
            if selling_complete:
                completeness += 0.15
            elif selling:
                completeness += 0.07
                sample_missing.append("selling signals incompleti (thumbnail style o conversion triggers mancanti)")
            else:
                completeness += 0.01
                sample_missing.append("selling signals assenti — Design Agent lavora senza guida visiva")

            # Pricing specifico per conversione (peso 0.10)
            pricing = sample.get("pricing", {})
            if pricing.get("conversion_sweet_spot_usd") and pricing.get("launch_price_usd"):
                completeness += 0.10
            elif pricing.get("conversion_sweet_spot_usd") or pricing.get("sweet_spot_usd"):
                completeness += 0.05
                sample_missing.append("launch price strategy mancante")
            else:
                completeness += 0.01
                sample_missing.append("pricing strategico assente")

            # Seasonal timing (peso 0.05)
            if sample.get("demand", {}).get("peak_months") and sample.get("demand", {}).get("publish_timing_advice"):
                completeness += 0.05
            else:
                completeness += 0.01
                sample_missing.append("timing stagionale non specificato")

            # audience_target mandatory (schema v2)
            audience_capped = not sample.get("audience_target", "").strip()
            if audience_capped:
                sample_missing.append("audience_target assente — cap confidence a 0.4")

            if best_completeness is None or completeness < best_completeness:
                best_completeness = completeness
                worst_audience_capped = audience_capped
                missing.extend(m for m in sample_missing if m not in missing)

        score += best_completeness or 0.0
        # Apply audience_target cap AFTER adding completeness (cap is ceiling, not floor)
        if worst_audience_capped:
            score = min(score, 0.40)

        return round(min(score, 1.0), 2), missing
