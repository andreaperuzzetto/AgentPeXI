"""EntryPointScoring — filtra e classifica candidati niche prima dell'analisi LLM.

Si inserisce tra _mine_opportunity_candidates() e l'analisi Haiku in parallelo:

    candidati grezzi (6-8)
        → EntryPointScoring.rank_candidates()
            → MarketDataAgent.collect_full() per ciascuno
            → quality_gap_factor  (proxy prezzo/saturazione)
            → performance_multiplier (da niche_intelligence — learning loop)
            → eligibility check  (cooldown, già in lavorazione)
        → top_k candidati scored
    → analisi LLM solo sui top_k

Vantaggi:
- Riduce le chiamate LLM (8 candidati → 3 top)
- Porta dati strutturati reali nel prompt di Research
- Cold-start safe: se nessun dato → score 0.4 flat → comportamento invariato

Nessun LLM. Solo DB + MarketDataAgent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from apps.backend.core.memory import MemoryManager

if TYPE_CHECKING:
    from apps.backend.agents.market_data import MarketDataAgent, MarketSignals

logger = logging.getLogger("agentpexi.entry_point_scoring")

# Giorni di cooldown default se non configurato in DB
_DEFAULT_COOLDOWN_DAYS = 7

# Range quality_gap_factor
_QGF_HIGH_PRICE_THRESHOLD = 8.0    # EUR — sopra: buyers pagano premium
_QGF_LOW_PRICE_THRESHOLD  = 4.0    # EUR — sotto: race to bottom
_QGF_SATURATION_THRESHOLD = 0.6    # fraction di _MAX_RESULT_COUNT


# ---------------------------------------------------------------------------
# Dataclass risultato
# ---------------------------------------------------------------------------

@dataclass
class ScoredCandidate:
    """Candidato niche con score finale e dettaglio componenti."""

    niche:        str
    product_type: str | None

    # Score components
    base_score:              float = 0.0   # dall'entry_score di MarketDataAgent
    quality_gap_factor:      float = 1.0   # proxy qualità/prezzo
    performance_multiplier:  float = 1.0   # da niche_intelligence (learning loop)
    final_score:             float = 0.0   # base × qgf × perf_mult

    # Eligibility
    eligible:          bool       = True
    exclusion_reason:  str | None = None

    # Segnali raw (opzionale — utile per debug e prompt enrichment)
    signals: Any | None = None   # MarketSignals | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche":                    self.niche,
            "product_type":             self.product_type,
            "base_score":               self.base_score,
            "quality_gap_factor":       self.quality_gap_factor,
            "performance_multiplier":   self.performance_multiplier,
            "final_score":              self.final_score,
            "eligible":                 self.eligible,
            "exclusion_reason":         self.exclusion_reason,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EntryPointScoring:
    """
    Classifica e filtra candidati niche usando dati di mercato strutturati.

    Uso:
        scorer = EntryPointScoring(memory=memory, market_data=market_data_agent)
        ranked = await scorer.rank_candidates(candidates, top_k=3)
        # ranked: list[ScoredCandidate], ordinata per final_score DESC, solo eligible
    """

    def __init__(
        self,
        memory:      MemoryManager,
        market_data: "MarketDataAgent",
    ) -> None:
        self._memory      = memory
        self._market_data = market_data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def rank_candidates(
        self,
        candidates: list[dict[str, Any]],
        top_k: int = 3,
    ) -> list[ScoredCandidate]:
        """
        Prende una lista di candidati grezzi {niche, product_type, ...},
        calcola il final_score per ciascuno, filtra gli ineligibili,
        ritorna i top_k ordinati per score DESC.

        Se tutti gli ineligibili → ritorna lista vuota (il caller deve gestire).
        Se top_k=0 → ritorna tutti gli eligible ordinati.
        """
        if not candidates:
            return []

        # Scoring parallelo (usa asyncio.gather per velocità)
        import asyncio
        scored = await asyncio.gather(
            *[self.score_single(c["niche"], c.get("product_type")) for c in candidates],
            return_exceptions=True,
        )

        results: list[ScoredCandidate] = []
        for i, item in enumerate(scored):
            if isinstance(item, Exception):
                niche = candidates[i].get("niche", "?")
                logger.warning("entry_point_scoring: errore su '%s': %s", niche, item)
                # Non esclude il candidato — usa score di fallback
                results.append(ScoredCandidate(
                    niche        = niche,
                    product_type = candidates[i].get("product_type"),
                    base_score   = 0.4,
                    final_score  = 0.4,
                    eligible     = True,
                ))
            else:
                results.append(item)

        # Separa eligible e ineligibili
        eligible   = [r for r in results if r.eligible]
        ineligible = [r for r in results if not r.eligible]

        if ineligible:
            logger.info(
                "entry_point_scoring: %d candidati esclusi: %s",
                len(ineligible),
                [(r.niche, r.exclusion_reason) for r in ineligible],
            )

        # Ordina per final_score DESC
        eligible.sort(key=lambda x: x.final_score, reverse=True)

        result = eligible[:top_k] if top_k > 0 else eligible

        logger.info(
            "entry_point_scoring: top %d candidati: %s",
            len(result),
            [(r.niche, round(r.final_score, 3)) for r in result],
        )
        return result

    async def score_single(
        self,
        niche:        str,
        product_type: str | None = None,
        force_refresh: bool = False,
    ) -> ScoredCandidate:
        """
        Calcola il ScoredCandidate per una singola niche.
        Recupera i segnali da MarketDataAgent (con cache DB 24h).
        """
        # 1. Eligibility check — veloce, solo DB
        eligible, reason = await self._check_eligibility(niche)

        # 2. Raccolta segnali di mercato
        try:
            signals = await self._market_data.collect_full(
                niche, product_type, force_refresh=force_refresh
            )
            base_score = signals.entry_score
        except Exception as e:
            logger.warning("entry_point_scoring: MarketData fallito per '%s': %s", niche, e)
            signals    = None
            base_score = 0.4   # cold-start safe

        # 3. Fattori moltiplicativi
        qgf   = self._quality_gap_factor(signals) if signals else 1.0
        perf  = await self._performance_multiplier(niche, product_type)

        # 4. Score finale
        final = round(base_score * qgf * perf, 3)
        final = max(0.05, min(final, 1.0))

        return ScoredCandidate(
            niche                = niche,
            product_type         = product_type,
            base_score           = base_score,
            quality_gap_factor   = qgf,
            performance_multiplier = perf,
            final_score          = final,
            eligible             = eligible,
            exclusion_reason     = reason,
            signals              = signals,
        )

    # ------------------------------------------------------------------
    # Quality Gap Factor
    # ------------------------------------------------------------------

    def _quality_gap_factor(self, signals: "MarketSignals") -> float:
        """
        Stima il gap di qualità nel mercato basandosi su prezzo e saturazione.

        Logica:
          - prezzo alto + saturazione media  → buyers pagano premium, non c'è offerta
                                              perfetta → 1.2 (opportunità differenziazione)
          - prezzo molto alto (≥12€)         → 1.15 (nicchia premium, margini buoni)
          - prezzo basso + mercato saturo    → race to bottom, difficile emergere → 0.85
          - prezzo molto basso (< 3€)        → 0.8
          - altrimenti                       → 1.0 (neutro)

        Range: [0.8, 1.2]. Non amplifica mai oltre 1.2 per non distorcere lo score.
        """
        price        = getattr(signals, "avg_price_eur", 0.0) or 0.0
        result_count = getattr(signals, "etsy_result_count", 0) or 0

        competition_norm = min(result_count / 50_000, 1.0)

        if price >= 12.0:
            return 1.15
        elif price >= _QGF_HIGH_PRICE_THRESHOLD and competition_norm < _QGF_SATURATION_THRESHOLD:
            return 1.2
        elif price < 3.0:
            return 0.8
        elif price < _QGF_LOW_PRICE_THRESHOLD and competition_norm > _QGF_SATURATION_THRESHOLD:
            return 0.85
        else:
            return 1.0

    # ------------------------------------------------------------------
    # Performance Multiplier (Learning Loop)
    # ------------------------------------------------------------------

    async def _performance_multiplier(
        self,
        niche:        str,
        product_type: str | None,
    ) -> float:
        """
        Legge niche_intelligence per il moltiplicatore dal learning loop.
        Default 1.0 se nessun dato (cold-start safe).

        Applicato solo se confidence in ('medium', 'high').

        Trasformazione:
          performance_score 0.5 (neutro) → 1.0
          performance_score 1.0 (top)    → 1.5
          performance_score 0.0 (pessimo)→ 0.5
        """
        try:
            db = await self._memory.get_db()
            cursor = await db.execute(
                """
                SELECT performance_score, confidence_level
                FROM niche_intelligence
                WHERE niche = ?
                  AND (product_type = ? OR product_type IS NULL)
                ORDER BY last_updated_at DESC
                LIMIT 1
                """,
                (niche, product_type),
            )
            row = await cursor.fetchone()
        except Exception as e:
            logger.debug("entry_point_scoring: niche_intelligence query fallita: %s", e)
            return 1.0

        if row is None:
            return 1.0

        if row["confidence_level"] not in ("medium", "high"):
            return 1.0   # dati insufficienti → neutro

        # performance_score [0,1] → moltiplicatore [0.5, 1.5]
        return round(0.5 + float(row["performance_score"]), 3)

    # ------------------------------------------------------------------
    # Eligibility Check
    # ------------------------------------------------------------------

    async def _check_eligibility(self, niche: str) -> tuple[bool, str | None]:
        """
        Verifica se la niche è eleggibile per un nuovo ciclo.

        Esclusioni:
        1. Cooldown: listing pubblicato negli ultimi N giorni (config: policy.niche_cooldown_days)
        2. In lavorazione: item in production_queue con status non terminale
           (pending_design | pending_approval | approved | scheduled)

        Ritorna (True, None) se eleggibile, (False, motivo) altrimenti.
        """
        cooldown_days = await self._get_cooldown_days()
        cutoff        = time.time() - (cooldown_days * 86400)

        try:
            db = await self._memory.get_db()

            # Check 1: cooldown — pubblicata di recente?
            cursor = await db.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM production_queue
                WHERE niche = ?
                  AND status = 'published'
                  AND published_at >= ?
                """,
                (niche, cutoff),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] > 0:
                return False, f"cooldown ({cooldown_days}d)"

            # Check 2: già in pipeline attiva?
            cursor = await db.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM production_queue
                WHERE niche = ?
                  AND status IN (
                      'pending_design', 'pending_approval',
                      'approved', 'scheduled'
                  )
                """,
                (niche,),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] > 0:
                return False, "in_pipeline"

        except Exception as e:
            # Se la query fallisce (es. colonna non ancora migrata) → non bloccare
            logger.debug("entry_point_scoring: eligibility check fallito: %s", e)
            return True, None

        return True, None

    async def _get_cooldown_days(self) -> int:
        """Legge policy.niche_cooldown_days dalla tabella config. Default 7."""
        try:
            db = await self._memory.get_db()
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = 'policy.niche_cooldown_days'",
            )
            row = await cursor.fetchone()
            if row:
                return int(row["value"])
        except Exception:
            pass
        return _DEFAULT_COOLDOWN_DAYS
