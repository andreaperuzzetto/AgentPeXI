"""DesignAgent — input validation and research context extraction mixin."""
from __future__ import annotations

import logging

from apps.backend.agents._design.presets import AVAILABLE_TEMPLATES

logger = logging.getLogger("agentpexi.design")


class _DesignValidationMixin:

    # ------------------------------------------------------------------
    # Input validation (Intervento 19)
    # ------------------------------------------------------------------

    async def _validate_and_normalize_input(self, input_data: dict) -> tuple[dict | None, str | None]:
        """Valida e normalizza l'input del Design Agent."""
        required_fields = ["niche", "product_type"]
        for field in required_fields:
            if not input_data.get(field):
                return None, f"Missing required field: {field}"

        product_type = input_data.get("product_type", "")
        valid_types = set(AVAILABLE_TEMPLATES.keys())
        if product_type not in valid_types:
            return None, f"Invalid product_type: {product_type}. Must be one of: {', '.join(valid_types)}"

        template = input_data.get("template")
        if template:
            valid_templates = AVAILABLE_TEMPLATES.get(product_type, [])
            if template not in valid_templates:
                input_data["template"] = None

        num_variants = input_data.get("num_variants", 2)
        if not isinstance(num_variants, int) or num_variants < 1:
            num_variants = 2
        if num_variants > 5:
            num_variants = 5
        input_data["num_variants"] = num_variants

        color_schemes = input_data.get("color_schemes", [])
        if not color_schemes:
            color_schemes = ["neutral", "warm"]
        input_data["color_schemes"] = color_schemes[:num_variants]

        return input_data, None

    # ------------------------------------------------------------------
    # Research context extraction (Intervento 17)
    # ------------------------------------------------------------------

    def _extract_research_context(self, task_input: dict) -> dict | None:
        """Estrae e normalizza il contesto research dal task input."""
        research = task_input.get("research_result") or task_input.get("research_context")
        if not research:
            return None

        market = research.get("market_insights", {})
        return {
            "top_keywords": research.get("top_keywords", [])[:10],
            "avg_price": market.get("avg_price"),
            "competition_level": market.get("competition_level"),
            "target_audience": market.get("target_audience"),
            "gaps": market.get("gaps", []),
            "trending_styles": market.get("trending_styles", []),
            "confidence": research.get("confidence", 0.0),
        }

    # ------------------------------------------------------------------
    # Failure pattern lookup (Intervento 16)
    # ------------------------------------------------------------------

    async def _lookup_failure_patterns(self, niche: str, template: str) -> dict | None:
        """Cerca in ChromaDB pattern di fallimento, design outcome e winner precedenti.

        Fonti:
        - failure_analysis: scritto da Analytics (no_views, no_conversion)
        - design_outcome:   scritto da Level 1 (ogni generazione Design)
        - design_winner:    scritto da Level 3 (listing con vendite reali)
        """
        try:
            # Failure analysis recenti (Analytics)
            failures = await self.memory.query_chromadb_recent(
                query=f"FAILURE niche {niche} template {template}",
                n_results=3,
                where={"type": "failure_analysis"},
                primary_days=90,
                fallback_days=180,
            )
            # Design outcome recenti (Level 1 — ogni generazione)
            outcomes = await self.memory.query_chromadb_recent(
                query=f"DESIGN_OUTCOME niche {niche}",
                n_results=5,
                where={"type": "design_outcome"},
                primary_days=90,
                fallback_days=180,
            )
            # Design winner (Level 3 — listing che hanno convertito)
            winners = await self.memory.query_chromadb_recent(
                query=f"DESIGN_WINNER niche {niche} success",
                n_results=3,
                where={"type": "design_winner"},
                primary_days=180,
                fallback_days=365,
            )

            # Low CTR signals — B5/5.3 A/B thumbnail testing
            # Scritti da LearningLoop.flag_low_ctr() quando AnalyticsAgent
            # rileva ladder_level='ctr_low'. DesignAgent li usa per escludere
            # combinazioni template+color_scheme già verificate come inefficaci.
            low_ctr_signals = await self.memory.query_chromadb_recent(
                query=f"low CTR {niche}",
                n_results=5,
                where={"type": "low_ctr_signal", "niche": niche},
                primary_days=90,
                fallback_days=180,
            )

            known_issues = [r["document"] for r in failures[:2]] if failures else []
            avoid = []
            for r in failures:
                meta = r.get("metadata", {})
                if meta.get("failure_type"):
                    avoid.append(meta["failure_type"])

            # Estrai preset/template/color_scheme dai metadati strutturati (Level 1)
            structured_outcomes = []
            for o in outcomes[:5]:
                meta = o.get("metadata", {})
                structured_outcomes.append({
                    "preset": meta.get("preset", ""),
                    "template": meta.get("template", ""),
                    "color_scheme": meta.get("color_scheme", ""),
                    "pdf_valid": meta.get("pdf_valid", ""),
                    "pages": meta.get("pages", ""),
                    "date": meta.get("date", ""),
                })

            # Estrai template/color_scheme dai winner (Level 3)
            structured_winners = []
            for w in winners[:3]:
                meta = w.get("metadata", {})
                structured_winners.append({
                    "template": meta.get("template", ""),
                    "color_scheme": meta.get("color_scheme", ""),
                    "views": meta.get("views", ""),
                    "sales": meta.get("sales", ""),
                    "date": meta.get("date", ""),
                })

            # Estrai combinazioni template+color_scheme con CTR basso (B5/5.3)
            low_ctr_combos = []
            for r in (low_ctr_signals or []):
                meta = r.get("metadata", {})
                tmpl = meta.get("template", "")
                cs   = meta.get("color_scheme", "")
                if tmpl or cs:
                    low_ctr_combos.append({"template": tmpl, "color_scheme": cs})

            if known_issues or structured_outcomes or structured_winners or low_ctr_combos:
                result: dict = {}
                if known_issues:
                    result["known_issues"] = known_issues
                    result["avoid"] = avoid
                if structured_outcomes:
                    result["recent_outcomes"] = structured_outcomes
                if structured_winners:
                    result["winners"] = structured_winners
                if low_ctr_combos:
                    result["low_ctr_combos"] = low_ctr_combos
                return result
        except Exception:
            logger.exception("Unexpected error")
        return None
