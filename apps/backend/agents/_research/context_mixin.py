"""ResearchAgent — context reading mixin."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentpexi.research")


class _ResearchContextMixin:

    @staticmethod
    def _build_market_context(scored_candidate) -> str:
        """
        Converte un ScoredCandidate in una stringa compatta per il prompt LLM.
        Se signals è None (cold-start) ritorna stringa vuota.
        """
        sc = scored_candidate
        signals = sc.signals
        if signals is None:
            return ""

        lines = [
            "## Dati di mercato strutturati (MarketDataAgent)",
            f"Entry score: {sc.final_score:.3f} "
            f"(base={sc.base_score:.3f}, qgf={sc.quality_gap_factor}, "
            f"perf_mult={sc.performance_multiplier})",
            f"Listing Etsy trovati: {getattr(signals, 'etsy_result_count', 'n/d'):,}",
            f"Avg favorites (domanda proxy): {getattr(signals, 'avg_reviews', 0):.1f}",
            f"Avg prezzo: €{getattr(signals, 'avg_price_eur', 0):.2f}",
            f"Autocomplete hits: {getattr(signals, 'autocomplete_hits', 0)}",
            f"Google Trends score: {getattr(signals, 'google_trend_score', 0):.1f}/100",
            f"Seasonal boost: {getattr(signals, 'seasonal_boost', 1.0)}",
        ]
        return "\n".join(lines)

    async def _read_finance_context(self, niche: str) -> str:
        """
        Legge da ChromaDB i segnali prodotti da Finance:
          - niche_roi_snapshot: ROI storico per nicchia specifica
          - finance_directive: nicchie da scalare / abbandonare per direttiva strategica

        Ritorna una stringa pronta per essere iniettata nel prompt LLM.
        Ritorna stringa vuota se non ci sono dati (cold-start safe).
        """
        lines: list[str] = []

        # 1. ROI storico per questa nicchia
        try:
            roi_docs = await self.memory.query_chromadb_recent(
                query=f"Finance ROI snapshot nicchia {niche}",
                n_results=3,
                where={"type": {"$eq": "niche_roi_snapshot"}},
                primary_days=30,
                fallback_days=90,
            )
            if roi_docs:
                lines.append("## ROI storico (Finance)")
                for doc in roi_docs:
                    meta = doc.get("metadata", {})
                    doc_niche = meta.get("niche", "")
                    if doc_niche.lower() in niche.lower() or niche.lower() in doc_niche.lower():
                        roi_pct = meta.get("roi_pct", "n/d")
                        sales = meta.get("total_sales", "0")
                        margin = meta.get("net_margin_eur", "n/d")
                        lines.append(
                            f"  - Niche '{doc_niche}': ROI {roi_pct}%, "
                            f"{sales} vendite, €{margin} margine netto"
                        )
        except Exception:
            logger.exception("Unexpected error")
        # 2. Finance insight — economia di pricing (break-even, costo per listing)
        #    Critico per la pricing analysis: Research deve sapere il costo reale
        #    per listing e quante vendite servono per coprirlo.
        try:
            insight_docs = await self.memory.query_chromadb_recent(
                query=f"Finance insight nicchia {niche} break-even costo listing pricing",
                n_results=3,
                where={"type": {"$eq": "finance_insight"}},
                primary_days=30,
                fallback_days=90,
            )
            if insight_docs:
                lines.append("## Economia reale per listing (Finance)")
                for doc in insight_docs:
                    meta = doc.get("metadata", {})
                    doc_niche = meta.get("niche", "")
                    if doc_niche.lower() in niche.lower() or niche.lower() in doc_niche.lower():
                        avg_price = meta.get("avg_price_eur", "n/d")
                        break_even = meta.get("break_even_units", "n/d")
                        cost_pl = meta.get("cost_per_listing_eur", "n/d")
                        roi = meta.get("roi_pct", "n/d")
                        lines.append(
                            f"  - Niche '{doc_niche}': prezzo medio reale €{avg_price}, "
                            f"break-even a {break_even} vendite, "
                            f"costo LLM/listing €{cost_pl}, ROI attuale {roi}%"
                        )
                        lines.append(
                            f"    → Il tuo pricing deve garantire almeno {break_even} vendite "
                            f"per coprire i costi di produzione. "
                            f"Raccomanda prezzi che rendano questo realistico."
                        )
        except Exception:
            logger.exception("Unexpected error")
        # 2. Direttiva strategica Finance (nicchie da scalare / abbandonare)
        try:
            directive_docs = await self.memory.query_chromadb_recent(
                query="finance directive scale abandon niche strategy",
                n_results=1,
                where={"type": {"$eq": "finance_directive"}},
                primary_days=30,
                fallback_days=90,
            )
            if directive_docs:
                meta = directive_docs[0].get("metadata", {})
                to_scale = meta.get("niches_to_scale", "")
                to_abandon = meta.get("niches_to_abandon", "")
                date = meta.get("date", "")

                lines.append(f"## Direttiva strategica Finance (aggiornata {date})")

                niche_lower = niche.lower()
                abandon_list = [n.strip().lower() for n in to_abandon.split("|") if n.strip()]
                scale_list = [n.strip().lower() for n in to_scale.split("|") if n.strip()]

                if any(niche_lower in ab or ab in niche_lower for ab in abandon_list):
                    lines.append(
                        f"  ⛔ ATTENZIONE: Finance ha classificato questa nicchia come "
                        f"'da abbandonare' (ROI negativo). Valuta con estrema cautela."
                    )
                elif any(niche_lower in sc or sc in niche_lower for sc in scale_list):
                    lines.append(
                        f"  ✅ Finance raccomanda di SCALARE questa nicchia (ROI positivo confermato)."
                    )
                else:
                    if to_scale:
                        lines.append(f"  Nicchie da scalare: {to_scale.replace('|', ', ')}")
                    if to_abandon:
                        lines.append(f"  Nicchie da abbandonare: {to_abandon.replace('|', ', ')}")
        except Exception:
            logger.exception("Unexpected error")
        if not lines:
            return ""

        return "\n\n## Contesto finanziario (Finance Agent)\n" + "\n".join(lines)

    async def _read_shared_context(self, query: str) -> str:
        """Legge insight cross-domain da shared_memory.

        Ritorna una stringa pronta per l'iniezione nel prompt LLM.
        Stringa vuota se shared_memory è vuota o non disponibile (cold-start safe).
        """
        try:
            docs = await self.memory.query_shared_memory(
                query=query,
                n_results=2,
                agent="research",
            )
            if not docs:
                return ""
            lines = ["## Insight cross-domain (Personal ↔ Etsy)"]
            for doc in docs:
                text = doc.get("document", "").strip()
                if text:
                    lines.append(f"- {text[:200]}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""
