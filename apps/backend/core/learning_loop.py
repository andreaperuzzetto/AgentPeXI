"""LearningLoop — feedback analytics → scoring niches + CTR attribution.

Blocco 4 — step 4.5

Responsabilità:
  - Aggrega snapshot da listing_performance → aggiorna niche_intelligence
  - Calcola performance_score con peso CTR 30% | conv 40% | revenue 30%
  - flag_low_ctr()        → ChromaDB low_ctr_signal (letto da DesignAgent)
  - flag_for_seo_revision() → abbassa performance_score -0.1 (capped 0.2)
  - get_top_niches()      → usato da AutopilotLoop niche picker e /shop-setup
  - get_intel()           → lettura singola riga niche_intelligence
  - get_unexplored_candidates() → niches con 0 listing (rotazione)

Invarianti:
  - niche_intelligence usa ON CONFLICT DO UPDATE — mai righe duplicate.
  - performance_score neutro = 0.5 quando confidence_level = 'low'.
  - flag_low_ctr è idempotente: scrive in ChromaDB senza controllare duplicati
    (ChromaDB stesso deduplicates per ID).
  - flag_for_seo_revision è idempotente: -0.1 con floor 0.2.
"""

from __future__ import annotations

import json
import logging
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any

from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.learning_loop")

# ---------------------------------------------------------------------------
# Soglie — speculari ad AnalyticsAgent per coerenza
# ---------------------------------------------------------------------------
_CTR_TARGET        = 0.03   # 3% — soglia normalizzazione score
_CONV_TARGET       = 0.05   # 5% — soglia normalizzazione score
_REV_TARGET_EUR    = 20.0   # €20 revenue/listing = score 1.0
_SCORE_MIN         = 0.2    # floor per flag_for_seo_revision
_SEO_PENALTY       = 0.1    # quanto abbassare lo score a ogni flag

# Pesi score (devono sommare a 1.0)
_W_CTR  = 0.30
_W_CONV = 0.40
_W_REV  = 0.30


class LearningLoop:
    """
    Service di apprendimento: aggrega metriche Etsy e aggiorna
    i punteggi di niche per guidare l'AutopilotLoop.

    Non fa chiamate LLM. Non dipende da altri service oltre MemoryManager.
    """

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory

    async def _db(self):
        return await self._memory.get_db()

    # ------------------------------------------------------------------
    # Core — aggiornamento niche_intelligence da listing_performance
    # ------------------------------------------------------------------

    async def update_niche_intelligence(self) -> int:
        """
        Aggrega tutti gli snapshot in listing_performance e aggiorna
        niche_intelligence tramite UPSERT.

        Ritorna il numero di righe aggiornate/inserite.
        """
        db = await self._db()

        cursor = await db.execute(
            """
            SELECT
                niche,
                product_type,
                COUNT(DISTINCT etsy_listing_id)             AS total_listings,
                COALESCE(SUM(orders), 0)                    AS total_orders,
                COALESCE(SUM(revenue_eur), 0.0)             AS total_revenue,
                COALESCE(AVG(ctr), 0.0)                     AS avg_ctr,
                COALESCE(AVG(conversion_rate), 0.0)         AS avg_cr,
                COALESCE(AVG(favorite_rate), 0.0)           AS avg_fr
            FROM listing_performance
            GROUP BY niche, product_type
            """
        )
        rows = await cursor.fetchall()

        updated = 0
        for row in rows:
            niche        = row["niche"]
            product_type = row["product_type"]
            n_listings   = int(row["total_listings"] or 0)
            n_orders     = int(row["total_orders"] or 0)
            total_rev    = float(row["total_revenue"] or 0.0)
            avg_ctr      = float(row["avg_ctr"] or 0.0)
            avg_cr       = float(row["avg_cr"] or 0.0)

            score      = self._calculate_performance_score(
                n_listings=n_listings,
                avg_ctr=avg_ctr,
                avg_cr=avg_cr,
                total_revenue=total_rev,
            )
            confidence = self._confidence_level(n_listings)

            await db.execute(
                """
                INSERT INTO niche_intelligence
                    (niche, product_type, total_listings, total_orders,
                     total_revenue_eur, avg_ctr, avg_conversion_rate,
                     performance_score, confidence_level, last_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch())
                ON CONFLICT(niche, product_type) DO UPDATE SET
                    total_listings      = excluded.total_listings,
                    total_orders        = excluded.total_orders,
                    total_revenue_eur   = excluded.total_revenue_eur,
                    avg_ctr             = excluded.avg_ctr,
                    avg_conversion_rate = excluded.avg_conversion_rate,
                    performance_score   = excluded.performance_score,
                    confidence_level    = excluded.confidence_level,
                    last_updated_at     = unixepoch()
                """,
                (
                    niche, product_type, n_listings, n_orders,
                    round(total_rev, 4), round(avg_ctr, 4), round(avg_cr, 4),
                    score, confidence,
                ),
            )
            updated += 1

        await db.commit()
        logger.info("update_niche_intelligence: %d righe aggiornate", updated)
        return updated

    # ------------------------------------------------------------------
    # Score helpers
    # ------------------------------------------------------------------

    def _calculate_performance_score(
        self,
        n_listings: int,
        avg_ctr: float,
        avg_cr: float,
        total_revenue: float,
    ) -> float:
        """
        Score composito per una niche+product_type.

        Pesi:
          - CTR   30% — normalizzato su target 3%
          - Conv  40% — normalizzato su target 5%
          - Rev   30% — revenue/listing normalizzata su €20

        Confidence weighting: con pochi listing lo score tende a 0.5
        (neutro) per evitare di esaltare/penalizzare dati rumorosi.

        Formula: score = 0.5*(1-w) + raw*w
        dove w = min(n_listings/5, 1.0)
        """
        if n_listings == 0:
            return 0.5

        ctr_score = min(avg_ctr / _CTR_TARGET, 1.0)
        cr_score  = min(avg_cr  / _CONV_TARGET, 1.0)

        rev_per_listing = total_revenue / max(n_listings, 1)
        rev_score       = min(rev_per_listing / _REV_TARGET_EUR, 1.0)

        raw    = ctr_score * _W_CTR + cr_score * _W_CONV + rev_score * _W_REV
        weight = min(n_listings / 5.0, 1.0)

        # Blend verso 0.5 quando confidence è bassa
        score  = 0.5 * (1.0 - weight) + raw * weight
        return round(score, 3)

    def _confidence_level(self, n_listings: int) -> str:
        """high ≥5 listings | medium ≥2 | low <2."""
        if n_listings >= 5:
            return "high"
        if n_listings >= 2:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # CTR attribution — segnale per DesignAgent
    # ------------------------------------------------------------------

    async def flag_low_ctr(
        self,
        niche: str,
        product_type: str,
        template: str,
        color_scheme: str,
    ) -> None:
        """
        Registra in ChromaDB (pepe_memory) che template+color_scheme ha
        prodotto CTR basso su questa niche.

        DesignAgent legge questo segnale via _lookup_failure_patterns()
        e lo inietta nel prompt per evitare la combinazione.

        Idempotente — ChromaDB deduplicates per document ID.
        """
        doc_id = f"low_ctr_{niche}_{product_type}_{template}_{color_scheme}"
        text   = (
            f"Low CTR detected: niche='{niche}' product_type='{product_type}' "
            f"template='{template}' color_scheme='{color_scheme}' "
            f"— CTR below {_CTR_TARGET*100:.0f}% threshold. "
            f"Avoid this template/color combination for this niche."
        )
        metadata = {
            "type":         "low_ctr_signal",
            "niche":        niche,
            "product_type": product_type,
            "template":     template or "",
            "color_scheme": color_scheme or "",
            "date":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        try:
            await self._memory.store_insight(text, metadata)
            logger.info(
                "LearningLoop: low_ctr_signal — niche=%s template=%s/%s",
                niche, template, color_scheme,
            )
        except Exception as exc:
            # Non bloccare il flusso se ChromaDB non è raggiungibile
            logger.warning("flag_low_ctr: store_insight fallito: %s", exc)

    # ------------------------------------------------------------------
    # SEO revision — abbassa score per ridurre priorità niche
    # ------------------------------------------------------------------

    async def flag_for_seo_revision(
        self,
        niche: str,
        product_type: str,
    ) -> float:
        """
        Abbassa temporaneamente performance_score per questa niche
        di _SEO_PENALTY (0.1), capped al valore minimo _SCORE_MIN (0.2).

        Effetto: l'AutopilotLoop darà meno priorità a questa niche
        finché update_niche_intelligence() non la rivaluterà con nuovi dati.

        Ritorna il nuovo score.
        """
        db = await self._db()
        cursor = await db.execute(
            """
            SELECT performance_score FROM niche_intelligence
            WHERE niche = ? AND product_type = ?
            """,
            (niche, product_type),
        )
        row = await cursor.fetchone()

        current_score = float(row["performance_score"]) if row else 0.5
        new_score     = round(max(current_score - _SEO_PENALTY, _SCORE_MIN), 3)

        await db.execute(
            """
            INSERT INTO niche_intelligence
                (niche, product_type, performance_score, last_updated_at)
            VALUES (?, ?, ?, unixepoch())
            ON CONFLICT(niche, product_type) DO UPDATE SET
                performance_score = ?,
                last_updated_at   = unixepoch()
            """,
            (niche, product_type, new_score, new_score),
        )
        await db.commit()

        logger.info(
            "LearningLoop: flag_for_seo_revision niche=%s type=%s score %.3f→%.3f",
            niche, product_type, current_score, new_score,
        )
        return new_score

    # ------------------------------------------------------------------
    # Query helpers — usati da AutopilotLoop e comandi Telegram
    # ------------------------------------------------------------------

    async def get_top_niches(self, limit: int = 5) -> list[str]:
        """
        Restituisce le niche con performance_score più alto.
        Usato da: _autopilot_niche_picker, /shop-setup.

        Ritorna lista di stringhe (nomi niche), non dict, per comodità
        nei formatter Telegram.
        """
        db     = await self._db()
        cursor = await db.execute(
            """
            SELECT DISTINCT niche
            FROM niche_intelligence
            WHERE confidence_level IN ('medium', 'high')
            ORDER BY performance_score DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [row["niche"] for row in rows]

    async def get_intel(
        self,
        niche: str,
        product_type: str | None,
    ) -> dict | None:
        """
        Legge la riga niche_intelligence per niche+product_type.
        Se product_type è None, ritorna la riga con score più alto per quella niche.

        Ritorna dict o None se non trovata.
        """
        db = await self._db()

        if product_type is not None:
            cursor = await db.execute(
                """
                SELECT * FROM niche_intelligence
                WHERE niche = ? AND product_type = ?
                """,
                (niche, product_type),
            )
            row = await cursor.fetchone()
        else:
            cursor = await db.execute(
                """
                SELECT * FROM niche_intelligence
                WHERE niche = ?
                ORDER BY performance_score DESC
                LIMIT 1
                """,
                (niche,),
            )
            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "niche":               row["niche"],
            "product_type":        row["product_type"],
            "total_listings":      int(row["total_listings"]    or 0),
            "total_orders":        int(row["total_orders"]      or 0),
            "total_revenue_eur":   float(row["total_revenue_eur"] or 0.0),
            "avg_ctr":             float(row["avg_ctr"]         or 0.0),
            "avg_conversion_rate": float(row["avg_conversion_rate"] or 0.0),
            "performance_score":   float(row["performance_score"] or 0.5),
            "confidence_level":    row["confidence_level"]      or "low",
            "last_updated_at":     row["last_updated_at"],
        }

    async def get_unexplored_candidates(self) -> list[dict]:
        """
        Niches presenti in niche_intelligence ma senza listing pubblicati
        negli ultimi 90 giorni in production_queue.

        Utile per rotazione niche — evita di concentrarsi sempre sulle stesse.

        Ritorna lista di {niche, product_type, performance_score}.
        """
        cutoff = _time.time() - 90 * 86400
        db     = await self._db()
        cursor = await db.execute(
            """
            SELECT ni.niche, ni.product_type, ni.performance_score
            FROM niche_intelligence ni
            WHERE NOT EXISTS (
                SELECT 1 FROM production_queue pq
                WHERE pq.niche        = ni.niche
                  AND pq.product_type = ni.product_type
                  AND pq.status       = 'published'
                  AND pq.published_at >= ?
            )
            ORDER BY ni.performance_score DESC
            LIMIT 20
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "niche":            row["niche"],
                "product_type":     row["product_type"],
                "performance_score": float(row["performance_score"] or 0.5),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Utility — chiamato da /learn (Telegram handler)
    # ------------------------------------------------------------------

    async def run_full_update(self) -> dict:
        """
        Esegue update_niche_intelligence() e ritorna un summary.
        Usato dal comando /learn per forzare il ricalcolo.
        """
        n_updated = await self.update_niche_intelligence()
        top       = await self.get_top_niches(limit=3)
        return {
            "n_updated": n_updated,
            "top_niches": top,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
