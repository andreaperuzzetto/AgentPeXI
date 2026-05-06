"""PublisherAgent — SEO generation mixin."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("agentpexi.publisher")


# AGT-3.3 — LADDER_PROMPTS dispatcher (ETSY_STRATEGY §AGT-3.3)
# Ogni tier ha regole specifiche per titolo, prezzo e description hook.
LADDER_PROMPTS: dict[str, dict] = {
    "tripwire": {
        "title_prefix": "Printable",          # prima parola forzata — segnala prezzo basso
        "price_cap_eur": 2.50,                # massimo assoluto per tripwire
        "description_hook": "budget_anchor",  # "For less than a coffee, get..."
        "tags_strategy": "volume_first",      # L1 e L2 tag prioritari per impressions
        "title_rule": "Il titolo INIZIA SEMPRE con la parola 'Printable' — segnala prezzo basso e download immediato.",
    },
    "core": {
        "title_prefix": None,
        "price_cap_eur": None,
        "description_hook": "benefit_first",
        "tags_strategy": "balanced",
        "title_rule": "Formula standard: [Audience Benefit] [Product Type] for [Specific Audience] | [Differentiator]",
    },
    "core_premium": {
        "title_suffix": "| Complete Edition",
        "price_multiplier": 1.30,             # +30% rispetto al core base
        "description_hook": "value_stack",    # enfatizza contenuto aggiuntivo
        "tags_strategy": "balanced",
        "title_rule": "Il titolo include '| Complete Edition' o differentiator di valore in posizione finale.",
    },
    "bundle": {
        "title_contains": ["Bundle", "Complete Set", "Full Collection"],
        "description_hook": "bundle_value",   # elenca componenti, calcola risparmio %
        "tags_strategy": "l2_l3_focus",       # audience-specific, meno volume play
        "title_rule": "Il titolo contiene 'Bundle', 'Complete Set', o 'Full Collection' in posizione 2-3.",
    },
}


class _SeoMixin:

    def _build_ladder_context(self, product_tier: str) -> str:
        """Genera il blocco di istruzioni LADDER per il titolo in base al product_tier.

        Fonte: ETSY_STRATEGY §AGT-3.3 + etsy-seo-mcp TIER 1 patterns.
        """
        tier_data = LADDER_PROMPTS.get(product_tier, LADDER_PROMPTS["core"])
        rule = tier_data.get("title_rule", "")
        hook = tier_data.get("description_hook", "benefit_first")

        hook_instructions = {
            "budget_anchor": (
                "DESCRIPTION HOOK (tripwire): le prime 160 char iniziano con "
                "un anchor di prezzo basso — es. 'For less than a coffee, get [benefit]...'"
            ),
            "benefit_first": (
                "DESCRIPTION HOOK (core): le prime 160 char partono dal benefit immediato "
                "— NO 'Welcome to my shop', NO 'This listing is for'."
            ),
            "value_stack": (
                "DESCRIPTION HOOK (premium): le prime 160 char elencano il valore aggiuntivo "
                "rispetto al core — numero di pagine extra, formati bonus, ecc."
            ),
            "bundle_value": (
                "DESCRIPTION HOOK (bundle): le prime 160 char elencano i componenti del bundle "
                "e calcolano il risparmio rispetto all'acquisto separato."
            ),
        }.get(hook, "")

        return (
            f"\nLADDER TIER: {product_tier.upper()}\n"
            f"REGOLA TITOLO: {rule}\n"
            f"{hook_instructions}\n"
        )

    def _build_seo_system_prompt(self, selling_signals: dict, product_tier: str = "core", has_research_tags: bool = False) -> str:
        """System prompt SEO dinamico con selling_signals, AGT-3 copywriting framework e LADDER tier."""
        thumbnail_style = selling_signals.get("thumbnail_style", "clean mockup")
        conversion_triggers = selling_signals.get("conversion_triggers", [])
        bundle_vs_single = selling_signals.get("bundle_vs_single", "single")

        trigger_instructions = ""
        if conversion_triggers:
            trigger_instructions = (
                "\nLINGUAGGIO DI CONVERSIONE (usa questi concetti nella description):\n"
                + "\n".join(f"- {t}" for t in conversion_triggers[:3])
            )

        bundle_instruction = ""
        if bundle_vs_single == "bundle":
            bundle_instruction = (
                "\nEnfatizza il VALORE del bundle: più file, più risparmio "
                "rispetto all'acquisto singolo."
            )

        seasonal = self._get_seasonal_context()
        seasonal_instruction = ""
        if seasonal["keywords"]:
            seasonal_instruction = (
                f"\nCONTESTO STAGIONALE ({seasonal['season']}):\n"
                f"Se rilevante per la niche, considera di includere nel title o description:\n"
                f"{', '.join(seasonal['keywords'][:3])}\n"
                f"Non forzarlo se non c'entra con il prodotto."
            )

        ladder_context = self._build_ladder_context(product_tier)

        return (
            "Sei un copywriter Etsy specializzato in prodotti digitali stampabili.\n"
            "Il tuo obiettivo è massimizzare conversioni, non solo ottimizzare per search.\n\n"
            # AGT-3.1 — Copywriting formula
            "FORMULA TITOLO OBBLIGATORIA (max 140 char, Etsy tronca oltre):\n"
            "[Audience Benefit] [Product Type] for [Specific Audience] | [Differentiator]\n"
            "REGOLE TITOLO:\n"
            "1. Il BENEFIT viene PRIMA del prodotto ('Stay Calm Daily — Mindfulness Journal' > 'Mindfulness Journal')\n"
            "2. L'AUDIENCE SPECIFICA è SEMPRE inclusa ('for Teachers', 'for ADHD Adults', 'for Brides')\n"
            "3. Il differentiator finale (dopo |) include: numero specifico, formato, o aggettivo raro\n"
            "4. MAI keyword stuffing — titoli con 8+ keyword separate da virgole penalizzano il ranking Etsy 2026\n"
            f"{ladder_context}\n"
            # AGT-3.2 — Page-CRO 5-section structure
            "STRUTTURA DESCRIPTION (5 sezioni CRO, ETSY_STRATEGY §AGT-3.2):\n"
            "  SEZIONE 1 — Above the fold (prime 160 char, CRITICA):\n"
            "    Benefit immediato + chi è per + cosa ottieni. NO 'Welcome to my shop', NO 'This listing is for'.\n"
            "    Nessuna emoji nei primi 160 char. Keyword principale nel primo paragrafo.\n"
            "  SEZIONE 2 — What's included: bullet list specifici e numerici (✓ 52 weekly spreads, non 'many pages')\n"
            "  SEZIONE 3 — Who it's for: identità + pain point + outcome (il buyer si riconosce)\n"
            "  SEZIONE 4 — How it works: 'Download the PDF instantly after purchase. Print at home or at any print shop.'\n"
            "  SEZIONE 5 — (Solo se cluster ha ≥2 listing) Cross-reference altri prodotti correlati\n"
            # etsy-seo-mcp TIER 1 — Tag strategy
            "\nSTRATEGIA TAG (etsy-seo-mcp TIER 1):\n"
            "  13 tag esatti, mix broad/specific:\n"
            "  - L1 (broad, alto volume): 3-4 tag — es. 'printable planner', 'digital download'\n"
            "  - L2 (mid, audience-specific): 5-6 tag — es. 'planner for teachers', 'ADHD planner printable'\n"
            "  - L3 (long-tail, alta conversione): 3-4 tag — es. 'ADHD daily routine planner undated'\n"
            "  AI DISCLOSURE OBBLIGATORIA (policy Etsy 2024): nella description, PRIMA dei bullet points:\n"
            "  'This design was created with the assistance of artificial intelligence.'\n"
            f"\nSTILE THUMBNAIL DA MENZIONARE: {thumbnail_style}\n"
            f"{trigger_instructions}\n"
            f"{bundle_instruction}\n"
            f"{seasonal_instruction}\n\n"
            "REGOLE ASSOLUTE:\n"
            "1. Title: segui la FORMULA TITOLO e la REGOLA TITOLO LADDER sopra — il tier sovrascrive le regole generali\n"
            "2. Description: prima riga ottimizzata per Etsy search preview (160 chars max)\n"
            "3. Bullet points con ✓ o • per le caratteristiche\n"
            + (
                "4. Tags: usa ESATTAMENTE la lista fornita (13 tag ottimizzati da Research)\n"
                if has_research_tags else
                "4. Tags: genera 13 tag secondo la strategia L1/L2/L3 descritta sopra\n"
            ) +
            "5. Nessun claim falso (no 'best seller', 'award winning')\n"
            "6. Sempre in inglese\n"
            '7. Rispondi SOLO con JSON valido: {"title": "...", "description": "...", "tags": [...]}\n'
        )

    async def _generate_seo(
        self,
        niche: str,
        template: str,
        keywords: list[str],
        color_scheme: str,
        size: str,
        research_data: dict,
        product_tier: str = "core",  # AGT-3: ladder tier da production_queue
    ) -> dict:
        """Genera title, description, tags via LLM usando dati Research. Retry una volta se JSON malformato."""
        etsy_tags_13 = research_data.get("etsy_tags_13", [])
        selling_signals = research_data.get("selling_signals", {})
        conversion_triggers = selling_signals.get("conversion_triggers", [])
        bundle_vs_single = selling_signals.get("bundle_vs_single", "single")
        thumbnail_style = selling_signals.get("thumbnail_style", "")

        system_prompt = self._build_seo_system_prompt(
            selling_signals,
            product_tier=product_tier,
            has_research_tags=bool(etsy_tags_13),
        )

        # Build user message con dati Research
        tags_instruction = ""
        if etsy_tags_13:
            tags_instruction = (
                "\nTAG ETSY OBBLIGATORI (usa esattamente questi 13, sono già ottimizzati da Research):\n"
                f"{json.dumps(etsy_tags_13, ensure_ascii=False)}\n\n"
                "IMPORTANTE: Il campo \"tags\" DEVE essere esattamente la lista fornita sopra, non generarne di nuovi.\n"
            )

        signals_section = ""
        if selling_signals:
            signals_section = (
                "\nSEGNALI DI VENDITA DA RESEARCH:\n"
                f"- Stile thumbnail vincente: {thumbnail_style}\n"
                f"- Trigger di conversione: {', '.join(conversion_triggers)}\n"
                f"- Formato consigliato: {bundle_vs_single}\n"
            )

        first_keyword = etsy_tags_13[0] if etsy_tags_13 else niche
        tags_json = json.dumps(etsy_tags_13) if etsy_tags_13 else '["...", ...]'

        user_prompt = (
            f"Crea il listing Etsy per: {niche} ({size})\n"
            f"Template: {template}\n"
            f"Schema colore: {color_scheme}\n"
            f"Keywords target: {', '.join(keywords) if keywords else 'nessuna'}\n"
            f"{tags_instruction}"
            f"{signals_section}\n"
            f"REGOLE TITLE:\n"
            f"- Inizia con la keyword principale: \"{first_keyword}\"\n"
            f"- Max 140 caratteri\n"
            f"- Includi il benefit principale nei primi 60 caratteri\n\n"
            f"REGOLE DESCRIPTION:\n"
            f"- Prima riga: keyword principale + benefit immediato (per Etsy search preview)\n"
            f"- 150-300 parole\n"
            f"- Bullet points per caratteristiche (\u2022)\n"
            f"- Includi: cosa ricevi, come scaricarlo, come usarlo\n"
            + (f"- Usa il linguaggio dei conversion_triggers sopra\n" if conversion_triggers else "")
            + f"\nOUTPUT JSON:\n"
            f'{{\"title\": \"...\", \"description\": \"...\", \"tags\": {tags_json}}}\n'
        )

        for attempt in range(2):
            response_text = await self._call_llm(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=system_prompt,
            )
            parsed = self._parse_seo_json(response_text, etsy_tags_13=etsy_tags_13)
            if parsed:
                return parsed
            logger.warning("SEO JSON malformato (tentativo %d): %s", attempt + 1, response_text[:200])

        raise RuntimeError("LLM non ha generato JSON SEO valido dopo 2 tentativi")

    def _parse_seo_json(self, text: str, etsy_tags_13: list[str] | None = None) -> dict | None:
        """Estrae e valida JSON SEO dalla risposta LLM con quality check."""
        cleaned = text.strip()
        # Estrai JSON grezzo da qualsiasi wrapper (```json ... ```, ``` ... ```, o testo libero)
        # Strategia: trova la prima { e l'ultima } — robusto su JSON annidati
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
        try:
            data = json.loads(cleaned)
            if not ("title" in data and "description" in data and "tags" in data):
                return None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

        # --- SEO quality validation ---
        issues: list[str] = []
        seo_validated = True

        title = str(data.get("title", ""))
        description = str(data.get("description", ""))
        tags = data.get("tags", [])

        # Title validation
        if len(title) > 140:
            data["title"] = title[:140]
            issues.append("title troncato a 140 chars")
        if len(title) < 30:
            issues.append("title troppo corto (<30 chars)")
            seo_validated = False

        # Description bullet points
        if "\u2022" not in description and "- " not in description:
            issues.append("description senza bullet points")
            seo_validated = False

        # Description length — il prompt richiede 150-300 parole, validazione coerente
        desc_word_count = len(description.split())
        if desc_word_count < 150:
            issues.append(f"description troppo corta ({desc_word_count} parole, min 150)")
            seo_validated = False
        elif desc_word_count > 300:
            issues.append(f"description troppo lunga ({desc_word_count} parole, max 300)")

        # Tags — override con Research se disponibili
        if etsy_tags_13:
            # Validazione: Etsy accetta max 13 tag, ognuno max 20 chars
            sanitized = [str(t)[:20] for t in etsy_tags_13[:13]]
            if len(sanitized) != 13:
                issues.append(f"Research ha fornito {len(sanitized)} tag invece di 13 — slot Etsy non sfruttati")
                seo_validated = False
            long_tags = [t for t in etsy_tags_13 if len(str(t)) > 20]
            if long_tags:
                issues.append(f"{len(long_tags)} tag troncati a 20 chars: {long_tags[:3]}")
            data["tags"] = sanitized
        else:
            if len(tags) < 10:
                issues.append(f"solo {len(tags)} tag (min 10)")
                seo_validated = False
            data["tags"] = [str(t)[:20] for t in tags[:13]]

        if issues:
            logger.warning("SEO issues: %s", issues)

        data["seo_validated"] = seo_validated
        data["seo_issues"] = issues
        return data
