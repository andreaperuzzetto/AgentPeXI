"""AnalyticsAgent — failure analysis mixin."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from apps.backend.core.config import MODEL_SONNET

logger = logging.getLogger("agentpexi.analytics")


class _AnalyticsFailureMixin:

    # ------------------------------------------------------------------
    # Caso A — No views
    # ------------------------------------------------------------------

    async def _analyze_no_views(self, listing: dict) -> None:
        lid = listing["listing_id"]
        niche = listing.get("niche", "")
        await self.memory.flag_no_views(lid)
        historical_context = await self._fetch_similar_failures(niche, "no_views")

        analysis = await self._failure_llm(
            prompt=self._no_views_prompt(listing),
            historical_context=historical_context,
        )
        if not analysis:
            return

        chromadb_id = await self._save_failure_chromadb(
            listing=listing,
            failure_type="no_views",
            analysis=analysis,
        )

        await self.memory.save_listing_analysis(
            listing_id=lid,
            analysis_type="no_views",
            cause=analysis["cause"],
            recommendations=analysis["recommendations"],
            avoid_in_future=analysis["avoid_in_future"],
            chromadb_id=chromadb_id,
        )

        recs = "\n".join(f"• {r}" for r in analysis["recommendations"])
        msg = (
            f"⚠️ Listing da ottimizzare — visibilità\n"
            f"📦 {listing.get('title', '')[:60]}\n"
            f"📊 14 giorni · 0 visualizzazioni\n"
            f"🔍 Problema: {analysis['cause']}\n\n"
            f"💡 Cosa fare:\n{recs}\n\n"
            f"🔗 https://www.etsy.com/your-shop/listings/{lid}/edit\n"
            f"#ottimizza #no_views"
        )
        await self._notify_telegram(msg)

    @staticmethod
    def _no_views_prompt(listing: dict) -> str:
        tags = listing.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        return (
            f"Questo listing Etsy non ha ricevuto nessuna visualizzazione dopo 14 giorni.\n"
            f"Problema: discoverabilità — il listing non appare nelle ricerche Etsy.\n\n"
            f"Titolo: {listing.get('title', '')}\n"
            f"Tag: {', '.join(tags)}\n"
            f"Nicchia: {listing.get('niche', '')}\n"
            f"Prezzo: €{listing.get('price_eur', 0)}\n"
            f"Formato: {listing.get('size', '')} {listing.get('template', '')}\n\n"
            f"Analizza titolo e tag. Il problema è probabilmente: keyword troppo generiche, "
            f"nicchia troppo competitiva, tag non allineati alla terminologia Etsy, o titolo "
            f"mal strutturato per l'algoritmo Etsy.\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "cause": "causa principale in max 80 caratteri",\n'
            f'  "recommendations": [\n'
            f'    "azione concreta 1",\n'
            f'    "azione concreta 2",\n'
            f'    "azione concreta 3"\n'
            f'  ],\n'
            f'  "avoid_in_future": "cosa NON ripetere in prodotti simili, max 80 caratteri"\n'
            f'}}'
        )

    # ------------------------------------------------------------------
    # Caso B — No conversion
    # ------------------------------------------------------------------

    async def _analyze_no_conversion(self, listing: dict) -> None:
        lid = listing["listing_id"]
        views = listing.get("views", 0)

        # Gate: almeno 30 views per avere dati significativi
        if views < 30:
            logger.info(
                "Skip no_conversion analysis listing %s: solo %d views (min 30)",
                lid, views,
            )
            await self.memory.flag_no_conversion(lid)
            return

        niche = listing.get("niche", "")
        await self.memory.flag_no_conversion(lid)
        historical_context = await self._fetch_similar_failures(niche, "no_conversion")

        analysis = await self._failure_llm(
            prompt=self._no_conversion_prompt(listing),
            historical_context=historical_context,
        )
        if not analysis:
            return

        chromadb_id = await self._save_failure_chromadb(
            listing=listing,
            failure_type="no_conversion",
            analysis=analysis,
        )

        await self.memory.save_listing_analysis(
            listing_id=lid,
            analysis_type="no_conversion",
            cause=analysis["cause"],
            recommendations=analysis["recommendations"],
            avoid_in_future=analysis["avoid_in_future"],
            chromadb_id=chromadb_id,
        )

        recs = "\n".join(f"• {r}" for r in analysis["recommendations"])
        views = listing.get("views", 0)
        favs = listing.get("favorites", 0)
        msg = (
            f"📉 Listing da ottimizzare — conversione\n"
            f"📦 {listing.get('title', '')[:60]}\n"
            f"📊 45 giorni · {views} views · {favs} ❤️ · 0 vendite\n"
            f"🔍 Problema: {analysis['cause']}\n\n"
            f"💡 Cosa fare:\n{recs}\n\n"
            f"🔗 https://www.etsy.com/your-shop/listings/{lid}/edit\n"
            f"#ottimizza #no_conversion"
        )
        await self._notify_telegram(msg)

    @staticmethod
    def _no_conversion_prompt(listing: dict) -> str:
        tags = listing.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        views = listing.get("views", 0)
        favs = listing.get("favorites", 0)
        ab = listing.get("ab_price_variant", "?")
        return (
            f"Questo listing Etsy ha ricevuto {views} visualizzazioni e {favs} preferiti "
            f"ma 0 vendite dopo 45 giorni. C'è interesse, ma non converte in acquisto.\n"
            f"Problema: conversione — qualcosa blocca l'acquisto.\n\n"
            f"Titolo: {listing.get('title', '')}\n"
            f"Tag: {', '.join(tags)}\n"
            f"Nicchia: {listing.get('niche', '')}\n"
            f"Prezzo: €{listing.get('price_eur', 0)} (variante A/B: {ab})\n"
            f"Formato: {listing.get('size', '')} {listing.get('template', '')}\n"
            f"Views: {views} | Favorites: {favs}\n\n"
            f"Il problema può essere: prezzo non allineato alle aspettative, "
            f"descrizione poco convincente, prodotto non perfettamente adatto "
            f"alla nicchia, mancanza di social proof, o thumbnail non attraente. "
            f"Con {favs} preferiti e 0 vendite il problema è probabilmente "
            f"il prezzo o la descrizione.\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "cause": "causa principale in max 80 caratteri",\n'
            f'  "recommendations": [\n'
            f'    "azione concreta 1",\n'
            f'    "azione concreta 2",\n'
            f'    "azione concreta 3"\n'
            f'  ],\n'
            f'  "avoid_in_future": "cosa NON ripetere in prodotti simili, max 80 caratteri"\n'
            f'}}'
        )

    # ------------------------------------------------------------------
    # Caso C — No views + no sales
    # ------------------------------------------------------------------

    async def _analyze_no_views_no_sales(self, listing: dict) -> None:
        lid = listing["listing_id"]
        niche = listing.get("niche", "")
        await self.memory.flag_no_views_no_sales(lid)
        historical_context = await self._fetch_similar_failures(niche, "no_views_no_sales")

        analysis = await self._failure_llm(
            prompt=self._no_views_no_sales_prompt(listing),
            historical_context=historical_context,
        )
        if not analysis:
            return

        chromadb_id = await self._save_failure_chromadb(
            listing=listing,
            failure_type="no_views_no_sales",
            analysis=analysis,
        )

        await self.memory.save_listing_analysis(
            listing_id=lid,
            analysis_type="no_views_no_sales",
            cause=analysis["cause"],
            recommendations=analysis["recommendations"],
            avoid_in_future=analysis["avoid_in_future"],
            chromadb_id=chromadb_id,
        )

        recs = "\n".join(f"• {r}" for r in analysis["recommendations"])
        msg = (
            f"🚫 Listing da archiviare\n"
            f"📦 {listing.get('title', '')[:60]}\n"
            f"📊 45 giorni · 0 views · 0 vendite\n"
            f"🔍 Problema: {analysis['cause']}\n\n"
            f"💡 Cosa fare:\n{recs}\n\n"
            f"⚠️ Considera di archiviare questo listing su Etsy.\n"
            f"🔗 https://www.etsy.com/your-shop/listings/{lid}/edit\n"
            f"#archivia #no_views_no_sales"
        )
        await self._notify_telegram(msg)

    @staticmethod
    def _no_views_no_sales_prompt(listing: dict) -> str:
        tags = listing.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        return (
            f"Questo listing Etsy ha 0 visualizzazioni e 0 vendite dopo 45 giorni.\n"
            f"Nessun interesse registrato. Problema doppio: discoverabilità E validità "
            f"della nicchia stessa.\n\n"
            f"Titolo: {listing.get('title', '')}\n"
            f"Tag: {', '.join(tags)}\n"
            f"Nicchia: {listing.get('niche', '')}\n"
            f"Prezzo: €{listing.get('price_eur', 0)}\n"
            f"Formato: {listing.get('size', '')} {listing.get('template', '')}\n\n"
            f"Questo è il segnale più negativo possibile. La nicchia potrebbe essere "
            f"troppo di nicchia, stagionale, già satura, o il prodotto non corrisponde "
            f"a ciò che gli acquirenti cercano su Etsy.\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "cause": "causa principale in max 80 caratteri",\n'
            f'  "recommendations": [\n'
            f'    "azione concreta 1 — probabilmente abbandonare questa nicchia",\n'
            f'    "azione concreta 2",\n'
            f'    "azione concreta 3"\n'
            f'  ],\n'
            f'  "avoid_in_future": "nicchia/approccio da NON ripetere mai, max 80 caratteri"\n'
            f'}}'
        )

    # ------------------------------------------------------------------
    # Failure analysis helpers
    # ------------------------------------------------------------------

    async def _fetch_similar_failures(self, niche: str, failure_type: str) -> str:
        """
        Cerca in ChromaDB failure patterns per niche simili.
        Ritorna stringa contestuale da iniettare nel prompt LLM.
        Ritorna "" se ChromaDB è vuoto o la query fallisce.
        """
        try:
            results = await self.memory.query_chromadb_recent(
                query=f"FAILURE {failure_type} niche {niche}",
                n_results=3,
                where={"type": "failure_analysis", "failure_type": failure_type},
                primary_days=90,
                fallback_days=180,
            )
            if not results:
                return ""

            context_lines = []
            for r in results:
                doc = r.get("document", "")
                if "cause:" in doc and "avoid:" in doc:
                    context_lines.append(f"- {doc}")

            if not context_lines:
                return ""

            return (
                f"\nCONTESTO STORICO — fallimenti simili già registrati:\n"
                + "\n".join(context_lines[:3])
                + "\nUsa questo storico per dare raccomandazioni coerenti "
                  "ed evitare di ripetere consigli già dati.\n"
            )
        except Exception:
            return ""

    async def _failure_llm(self, prompt: str, historical_context: str = "") -> dict | None:
        """Chiama Sonnet per failure analysis, parsa JSON."""
        enriched_prompt = prompt
        if historical_context:
            insert_before = "Rispondi SOLO con JSON:"
            if insert_before in enriched_prompt:
                enriched_prompt = enriched_prompt.replace(
                    insert_before,
                    historical_context + insert_before,
                )
            else:
                enriched_prompt += "\n" + historical_context

        response_text = await self._call_llm(
            messages=[{"role": "user", "content": enriched_prompt}],
            system_prompt=(
                "Sei un analista esperto di Etsy marketplace. Analizza i problemi dei listing "
                "e suggerisci azioni concrete. Se hai storico di fallimenti simili, usa quelle "
                "informazioni per dare raccomandazioni coerenti nel tempo."
            ),
            model_override=MODEL_SONNET,
        )
        return self._parse_analysis_json(response_text)

    async def _save_failure_chromadb(
        self,
        listing: dict,
        failure_type: str,
        analysis: dict,
    ) -> str | None:
        niche = listing.get("niche", "")
        template = listing.get("template", "")
        cause = analysis["cause"]
        avoid = analysis["avoid_in_future"]
        recs = "; ".join(analysis["recommendations"])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        text = (
            f"FAILURE {failure_type} | niche: {niche} | template: {template} | "
            f"cause: {cause} | avoid: {avoid} | recommendations: {recs}"
        )
        chromadb_id = await self.memory.store_insight(
            text=text,
            metadata={
                "type": "failure_analysis",
                "failure_type": failure_type,
                "niche": niche,
                "template": template,
                "date": today,
            },
        )
        return chromadb_id

    @staticmethod
    def _parse_analysis_json(text: str) -> dict | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()
        try:
            data = json.loads(cleaned)
            if "cause" in data and "recommendations" in data and "avoid_in_future" in data:
                return data
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None
