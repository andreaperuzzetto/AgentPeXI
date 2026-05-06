"""_StyleGuideMixin — genera 3 opzioni style guide via Haiku + ShopIdentityService.

Il metodo generate_style_options() orchestra:
  1. analyze_all_sections() [da _CompetitiveMixin] per ottenere segnali per sezione
  2. Chiamata diretta ad Anthropic Haiku per sintetizzare 3 opzioni brand identity
  3. ShopIdentityService.create() per persistere le opzioni (is_active=0)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic
import aiosqlite

logger = logging.getLogger("agentpexi.market_data.style_guide")

_HAIKU_MODEL = "claude-haiku-4-5"

_SYSTEM_PROMPT = """\
You are a brand identity strategist specializing in Etsy digital product shops.
Given competitive aesthetic signals for each product section, generate exactly 3
distinct brand identity options that could work across ALL sections simultaneously.

RULES:
- Each option must feel coherent across party, wellness, planners, AND kids learning
- Palettes must use hex codes (#RRGGBB format)
- tone must be 1-2 sentences, conversational
- mockup_style: "flat_lay" OR "lifestyle"
- rationale: 1 sentence explaining the strategic fit

Respond ONLY with a valid JSON array of exactly 3 objects. No markdown, no extra text.
Each object must have these exact keys:
  aesthetic_name, palette_primary, palette_secondary, palette_accent,
  mockup_style, tone, rationale
"""


def _build_user_prompt(signals: list[dict[str, Any]]) -> str:
    lines = ["Competitive aesthetic signals per section:\n"]
    for s in signals:
        lines.append(f"Section: {s['section_key']}")
        lines.append(f"  Palette labels: {', '.join(s['palette_labels'])}")
        lines.append(f"  Style keywords: {', '.join(s['style_keywords'])}")
        lines.append(f"  Dominant mockup: {s['mockup_style']}")
        lines.append(f"  Avg price range: {s['avg_price_range']}")
        lines.append(f"  Tone anchor: {s['tone_anchor']}\n")
    lines.append("Generate 3 distinct brand identity options:")
    return "\n".join(lines)


class _StyleGuideMixin:
    """Mixin: sintetizza 3 opzioni style guide via Haiku e le persiste in shop_identity."""

    async def generate_style_options(self, db: aiosqlite.Connection) -> list[int]:
        """Genera e persiste 3 opzioni style guide. Ritorna lista di ID creati.

        Args:
            db: connessione aiosqlite aperta (con row_factory = aiosqlite.Row).
        """
        # 1. Gather competitive signals (from _CompetitiveMixin)
        signals = await self.analyze_all_sections()  # type: ignore[attr-defined]

        # 2. Call Haiku
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        user_prompt = _build_user_prompt(signals)

        logger.info("StyleGuideMixin: calling Haiku for 3 style options")
        msg = await client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=1200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = msg.content[0].text.strip()

        # 3. Parse JSON
        options: list[dict[str, Any]] = json.loads(raw_text)
        if len(options) != 3:
            raise ValueError(f"Expected 3 style options, got {len(options)}")

        # 4. Persist via ShopIdentityService
        from apps.backend.core.shop_identity_service import ShopIdentityService
        svc = ShopIdentityService(db)
        ids: list[int] = []
        for opt in options:
            identity_id = await svc.create(
                aesthetic_name=opt["aesthetic_name"],
                palette_primary=opt["palette_primary"],
                palette_secondary=opt["palette_secondary"],
                palette_accent=opt["palette_accent"],
                mockup_style=opt["mockup_style"],
                tone=opt["tone"],
                approved_by="ai_generated",
            )
            ids.append(identity_id)
            logger.info("StyleGuideMixin: created option id=%d name=%s", identity_id, opt["aesthetic_name"])

        return ids
