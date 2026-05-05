"""DesignAgent — preset, color scheme, and template selection mixin."""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from apps.backend.core.config import MODEL_HAIKU
from apps.backend.agents._design.presets import (
    AVAILABLE_TEMPLATES,
    PRESET_KEYWORDS,
    STYLE_PRESETS,
)

logger = logging.getLogger("agentpexi.design")


class _DesignSelectionMixin:

    # ------------------------------------------------------------------
    # LLM helper methods (tracciati via _call_llm)
    # ------------------------------------------------------------------

    async def _select_preset(
        self,
        niche: str,
        template: str,
        research_context: dict | None,
        failure_patterns: dict | None = None,
    ) -> str:
        """Stage 1: Keyword scoring veloce. Stage 2: LLM per casi ambigui con contesto storico."""
        text = f"{niche} {template}".lower()

        scores = {preset: 0 for preset in PRESET_KEYWORDS}
        for preset, keywords in PRESET_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scores[preset] += 1

        max_score = max(scores.values())
        if max_score >= 2:
            winner = max(scores, key=scores.get)  # type: ignore[arg-type]
            return winner

        research_summary = ""
        if research_context:
            research_summary = f"""
Research context:
- Target audience: {research_context.get('target_audience', 'unknown')}
- Price range: {research_context.get('avg_price', 'unknown')}
- Top keywords: {', '.join(research_context.get('top_keywords', [])[:5])}
- Competition level: {research_context.get('competition_level', 'unknown')}
"""

        history_summary = ""
        if failure_patterns:
            lines = []
            winners = failure_patterns.get("winners", [])
            if winners:
                lines.append("⭐ Winning combinations for this niche (real sales data):")
                for w in winners:
                    lines.append(
                        f"  - template '{w['template']}' | scheme '{w['color_scheme']}' | "
                        f"{w['sales']} sales, {w['views']} views ({w['date']})"
                    )
                lines.append("Strongly prefer these combinations — they have proven conversion.")
            outcomes = failure_patterns.get("recent_outcomes", [])
            if outcomes:
                lines.append("Design history for this niche (most recent first):")
                for o in outcomes:
                    valid = "✓" if o.get("pdf_valid") == "True" else "✗"
                    lines.append(
                        f"  - preset '{o['preset']}' | template '{o['template']}' | "
                        f"scheme '{o['color_scheme']}' | PDF {valid} | {o['date']}"
                    )
                lines.append("Prefer variety: avoid repeating the exact same preset+template combo.")
            issues = failure_patterns.get("known_issues", [])
            if issues:
                lines.append("Known issues to consider:")
                for issue in issues[:2]:
                    lines.append(f"  - {issue[:120]}")
            # B5/5.3 — Low CTR combos to avoid (A/B thumbnail signal)
            low_ctr_combos = failure_patterns.get("low_ctr_combos", [])
            if low_ctr_combos:
                combos_str = "; ".join(
                    f"template='{c['template']}' color='{c['color_scheme']}'"
                    for c in low_ctr_combos[:3]
                )
                lines.append(
                    f"⚠️ Low-CTR combinations to AVOID (real Etsy data): {combos_str}"
                )
                lines.append(
                    "These thumbnail/color combos had CTR < 3% in this niche — "
                    "choose a visually distinct alternative."
                )
            if lines:
                history_summary = "\n" + "\n".join(lines) + "\n"

        prompt = f"""Select the best visual style preset for this Etsy digital product.

Product niche: {niche}
Template type: {template}
{research_summary}{history_summary}
Available presets:
- minimal: Clean, professional, whitespace-focused. For planners, budgets, productivity tools.
- decorative: Elegant, ornamental, serif fonts. For weddings, botanical, vintage, luxury products.
- corporate: Structured, business-ready. For professional templates, business tools, reports.
- playful: Fun, colorful, casual. For kids, educational, party, creative products.

Respond with ONLY one word: minimal, decorative, corporate, or playful"""

        try:
            result = (
                await self._call_llm(
                    messages=[{"role": "user", "content": prompt}],
                    model_override=MODEL_HAIKU,
                    max_tokens=10,
                )
            ).strip().lower()
        except Exception:
            result = "minimal"

        if result not in STYLE_PRESETS:
            result = "minimal"
        return result

    async def _resolve_color_scheme_niche_aware(
        self,
        color_scheme_name: str,
        niche: str,
        preset: str,
    ) -> dict[str, str]:
        """Genera palette colori coerente con nicchia e preset."""
        preset_data = STYLE_PRESETS[preset]

        prompt = f"""Generate a color palette for an Etsy digital product.
Return ONLY a JSON object, no explanation.

Product niche: {niche}
Style preset: {preset} ({preset_data['description']})
Requested color scheme: {color_scheme_name}

Requirements:
- Colors must feel cohesive and professional
- Background should be light (for printability)
- Text must have minimum 4.5:1 contrast ratio with background
- Accent should complement, not clash
- Inspired by {color_scheme_name} palette but adapted for {niche}

Return exactly:
{{"primary": "#hex", "secondary": "#hex", "accent": "#hex", "bg": "#hex", "text": "#hex"}}"""

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                model_override=MODEL_HAIKU,
                max_tokens=100,
            )
            match = re.search(r"\{[^}]+\}", raw)
            if match:
                data = json.loads(match.group())
                required_keys = {"primary", "secondary", "accent", "bg", "text"}
                if required_keys.issubset(data.keys()):
                    for val in data.values():
                        if not re.match(r"^#[0-9A-Fa-f]{6}$", val):
                            raise ValueError(f"Invalid hex: {val}")
                    return data
        except Exception:
            logger.exception("Unexpected error")
        return {
            "primary": preset_data["accent_color"],
            "secondary": preset_data["bg_color"],
            "accent": preset_data["accent_color"],
            "bg": preset_data["bg_color"],
            "text": preset_data["text_color"],
        }

    async def _select_template_llm(
        self,
        niche: str,
        product_type: str,
        research_context: dict | None,
        failure_patterns: dict | None = None,
    ) -> str:
        """Seleziona il template più adatto alla nicchia tramite LLM con contesto storico."""
        templates = AVAILABLE_TEMPLATES.get(product_type, ["weekly_planner"])

        research_info = ""
        if research_context:
            top_keywords = research_context.get("top_keywords", [])
            gaps = research_context.get("gaps", [])
            research_info = f"""
Research insights:
- Top buyer keywords: {', '.join(top_keywords[:5])}
- Market gaps to fill: {', '.join(gaps[:3])}
- Avg price: {research_context.get('avg_price', 'unknown')}
"""

        history_info = ""
        if failure_patterns:
            lines = []
            winners = failure_patterns.get("winners", [])
            if winners:
                winning_templates = [w["template"] for w in winners if w.get("template")]
                if winning_templates:
                    lines.append(f"⭐ Templates with proven sales for this niche: {', '.join(winning_templates)}")
                    lines.append("These templates have real conversion data — consider reusing them with a different color scheme.")
            outcomes = failure_patterns.get("recent_outcomes", [])
            if outcomes:
                used_templates = [o["template"] for o in outcomes if o.get("template")]
                if used_templates:
                    lines.append(f"Templates already used (no sales data yet): {', '.join(used_templates)}")
                    lines.append("If no winner exists, prefer a template not yet used to increase variety.")
            issues = failure_patterns.get("known_issues", [])
            if issues:
                lines.append("Known performance issues:")
                for issue in issues[:2]:
                    lines.append(f"  - {issue[:120]}")
            # B5/5.3 — Low CTR combos: evita template incriminati
            low_ctr_combos = failure_patterns.get("low_ctr_combos", [])
            if low_ctr_combos:
                avoid_templates = list(dict.fromkeys(
                    c["template"] for c in low_ctr_combos if c.get("template")
                ))
                if avoid_templates:
                    lines.append(
                        f"🚫 Templates with proven low CTR in this niche: {', '.join(avoid_templates[:3])}"
                    )
                    lines.append(
                        "Do NOT select these — they failed the CTR threshold in real Etsy data. "
                        "Pick a different template to generate a genuine A/B alternative."
                    )
            if lines:
                history_info = "\n" + "\n".join(lines) + "\n"

        prompt = f"""Select the best template for this Etsy digital product.

Niche: {niche}
Product type: {product_type}
{research_info}{history_info}
Available templates:
{chr(10).join(f'- {t}' for t in templates)}

Choose the template that:
1. Best matches what buyers in this niche actually search for
2. Has the highest commercial potential
3. Is coherent with the niche identity
4. Adds variety to existing products (avoid repeating already-used templates)

Respond with ONLY the template name, exactly as listed."""

        try:
            result = (
                await self._call_llm(
                    messages=[{"role": "user", "content": prompt}],
                    model_override=MODEL_HAIKU,
                    max_tokens=30,
                )
            ).strip().lower().replace(" ", "_")
            if result in templates:
                return result
        except Exception:
            logger.exception("Unexpected error")
        return templates[0]

    async def _should_include_dates(
        self,
        template: str,
        niche: str,
    ) -> bool:
        """Decide se il planner deve avere date specifiche o essere undated."""
        NO_DATE_TEMPLATES = {
            "wall_art_quote", "botanical_print", "abstract_art",
            "watercolor_print", "minimalist_poster", "vintage_poster",
            "icon_set", "pattern_bundle", "monogram_set",
            "clipart_bundle", "frame_bundle",
        }

        if template in NO_DATE_TEMPLATES:
            return False

        current_month = date.today().month

        prompt = f"""Should this Etsy planner be dated (specific year: 2026) or undated (no specific dates)?

Template: {template}
Niche: {niche}
Current month: {current_month} (1=January, 12=December)

Rules:
- Undated planners sell year-round (safer for evergreen sales)
- Dated planners are more relevant but expire after the year
- If current month is October-December: dated for next year can work
- If current month is January-February: dated for current year works
- Otherwise: undated is usually safer

Respond with ONLY: dated or undated"""

        try:
            result = (
                await self._call_llm(
                    messages=[{"role": "user", "content": prompt}],
                    model_override=MODEL_HAIKU,
                    max_tokens=5,
                )
            ).strip().lower()
            return result == "dated"
        except Exception:
            return False
