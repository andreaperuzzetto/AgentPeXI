"""PublisherAgent — SEO generation mixin."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("agentpexi.publisher")


class _SeoMixin:

    def _build_seo_system_prompt(self, selling_signals: dict) -> str:
        """System prompt SEO dinamico con selling_signals e contesto stagionale."""
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

        return (
            "Sei un copywriter Etsy specializzato in prodotti digitali stampabili.\n"
            "Il tuo obiettivo è massimizzare conversioni, non solo ottimizzare per search.\n\n"
            f"STILE THUMBNAIL DA MENZIONARE NELLA DESCRIPTION: {thumbnail_style}\n"
            f"{trigger_instructions}\n"
            f"{bundle_instruction}\n"
            f"{seasonal_instruction}\n\n"
            "REGOLE ASSOLUTE:\n"
            "1. Title: keyword principale PRIMA di tutto, benefit nei primi 60 chars\n"
            "2. Description: prima riga ottimizzata per Etsy search preview (150 chars max)\n"
            "3. Bullet points con \u2022 per le caratteristiche\n"
            "4. Tags: usa ESATTAMENTE la lista fornita, non modificare\n"
            "5. Nessun claim falso (no \"best seller\", \"award winning\")\n"
            "6. Sempre in inglese\n"
            "7. AI DISCLOSURE OBBLIGATORIA (policy Etsy 2024): includi nella description, "
            "PRIMA dei bullet points, esattamente questa frase: "
            "\"This design was created with the assistance of artificial intelligence.\"\n"
            '8. Rispondi SOLO con JSON valido: {"title": "...", "description": "...", "tags": [...]}\n'
        )

    async def _generate_seo(
        self,
        niche: str,
        template: str,
        keywords: list[str],
        color_scheme: str,
        size: str,
        research_data: dict,
    ) -> dict:
        """Genera title, description, tags via LLM usando dati Research. Retry una volta se JSON malformato."""
        etsy_tags_13 = research_data.get("etsy_tags_13", [])
        selling_signals = research_data.get("selling_signals", {})
        conversion_triggers = selling_signals.get("conversion_triggers", [])
        bundle_vs_single = selling_signals.get("bundle_vs_single", "single")
        thumbnail_style = selling_signals.get("thumbnail_style", "")

        system_prompt = self._build_seo_system_prompt(selling_signals)

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
