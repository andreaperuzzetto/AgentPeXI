"""FinanceAgent — upstream ChromaDB learning context reader mixin."""
from __future__ import annotations

import logging

logger = logging.getLogger("agentpexi.finance")


class _ContextMixin:

    # ------------------------------------------------------------------
    # Learning context reader (upstream ChromaDB signals)
    # ------------------------------------------------------------------

    async def _read_learning_context(self) -> dict:
        """
        Legge da ChromaDB i segnali prodotti dagli agenti upstream.

        Reads:
          - design_winner: combinazioni template/colore che hanno venduto
          - publish_failure / publish_success: tasso di fallimento deploy

        Returns:
            {
                "design_winners": list[dict],   # niche, template, color_scheme, sales, views
                "failure_count": int,
                "success_count": int,
                "failure_rate": float,           # 0.0-1.0
            }
        """
        design_winners: list[dict] = []
        failure_count = 0
        success_count = 0

        try:
            winners_raw = await self.memory.query_chromadb_recent(
                query="design winner best selling template niche",
                n_results=10,
                where={"type": {"$eq": "design_winner"}},
                primary_days=30,
                fallback_days=90,
            )
            for doc in (winners_raw or []):
                meta = doc.get("metadata", {})
                if meta.get("niche") and meta.get("template"):
                    design_winners.append({
                        "niche": meta["niche"],
                        "template": meta["template"],
                        "color_scheme": meta.get("color_scheme", ""),
                        "sales": meta.get("sales", "0"),
                        "views": meta.get("views", "0"),
                    })
        except Exception as exc:
            logger.warning("Finance: errore lettura design_winner: %s", exc)

        try:
            failures_raw = await self.memory.query_chromadb_recent(
                query="publish failure skipped error listing",
                n_results=50,
                where={"type": {"$eq": "publish_failure"}},
                primary_days=30,
                fallback_days=90,
            )
            failure_count = len(failures_raw or [])
        except Exception as exc:
            logger.warning("Finance: errore lettura publish_failure: %s", exc)

        try:
            successes_raw = await self.memory.query_chromadb_recent(
                query="publish success listing published etsy",
                n_results=50,
                where={"type": {"$eq": "publish_success"}},
                primary_days=30,
                fallback_days=90,
            )
            success_count = len(successes_raw or [])
        except Exception as exc:
            logger.warning("Finance: errore lettura publish_success: %s", exc)

        total_attempts = failure_count + success_count
        failure_rate = round(failure_count / total_attempts, 3) if total_attempts > 0 else 0.0

        # Research pricing recommendations (per confronto con prezzi reali)
        research_pricing: list[dict] = []
        try:
            rp_docs = await self.memory.query_chromadb_recent(
                query="research report pricing sweet spot niche digital products etsy",
                n_results=10,
                where={"type": {"$eq": "research_report"}},
                primary_days=30,
                fallback_days=90,
            )
            for doc in (rp_docs or []):
                meta = doc.get("metadata", {})
                niche_name = meta.get("niche", "")
                if niche_name:
                    research_pricing.append({
                        "niche": niche_name,
                        # Primo paragrafo significativo del summary
                        "summary": doc.get("document", "")[:250],
                    })
        except Exception as exc:
            logger.warning("Finance: errore lettura research_report pricing: %s", exc)

        return {
            "design_winners": design_winners,
            "failure_count": failure_count,
            "success_count": success_count,
            "failure_rate": failure_rate,
            "research_pricing": research_pricing,
        }
