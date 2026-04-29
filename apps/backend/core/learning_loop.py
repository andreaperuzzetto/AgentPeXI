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
    # A/B Thumbnail comparison — B5/5.3
    # ------------------------------------------------------------------

    async def compare_ab_thumbnails(self, niche: str) -> dict:
        """
        Confronta CTR originale vs alternativo per A/B thumbnail testing.

        Algoritmo:
        1. Trova listing in questa niche con ladder_level='ctr_low' (originale).
        2. Cerca un listing alternativo nella stessa niche pubblicato DOPO
           il segnale ctr_low (generato come regen_thumbnail) con almeno
           _ABS_MIN_DAYS giorni di dati.
        3. Confronta avg_ctr di original vs alternative.
        4. Winner → store design_winner in ChromaDB.
           Loser  → rinforza low_ctr_signal (re-flag, ChromaDB dedup per ID).
        5. Ritorna dict con risultati o reason di skip.

        Chiamato da: Scheduler job domenicale (dopo etsy_learning_loop).

        Args:
            niche: Nome della niche da confrontare.

        Returns:
            dict: {status: 'compared'|'skipped', winner, loser, niche, ...}
        """
        _AB_MIN_DAYS = 7   # giorni minimi di vita per valutare

        db = await self._db()

        # 1. Trova la niche_intelligence row per avere product_type
        cursor = await db.execute(
            """
            SELECT product_type FROM niche_intelligence
            WHERE niche = ? ORDER BY performance_score DESC LIMIT 1
            """,
            (niche,),
        )
        row = await cursor.fetchone()
        product_type = row["product_type"] if row else "digital_print"

        # 2. Tutti i listing in questa niche con performance data
        cursor = await db.execute(
            """
            SELECT
                pq.id            AS queue_id,
                pq.etsy_listing_id,
                pq.listing_title,
                pq.published_at,
                lp.ctr,
                lp.views,
                lp.clicks,
                lp.template,
                lp.color_scheme,
                lp.ladder_level,
                lp.snapshot_at
            FROM production_queue pq
            JOIN listing_performance lp ON lp.production_queue_id = pq.id
            WHERE pq.niche = ?
              AND pq.status = 'published'
              AND lp.days_live >= ?
              AND lp.snapshot_at = (
                  SELECT MAX(lp2.snapshot_at)
                  FROM listing_performance lp2
                  WHERE lp2.production_queue_id = pq.id
              )
            ORDER BY pq.published_at ASC
            """,
            (niche, _AB_MIN_DAYS),
        )
        rows = await cursor.fetchall()

        if len(rows) < 2:
            return {
                "status": "skipped",
                "reason": f"solo {len(rows)} listing con dati sufficienti in '{niche}'",
                "niche":  niche,
            }

        # 3. Cerca coppia originale (ctr_low) + alternativo (pubblicato dopo)
        original    = None
        alternative = None

        for r in rows:
            if r["ladder_level"] == "ctr_low" and original is None:
                original = r
            elif original and r["published_at"] and original["published_at"]:
                if r["published_at"] > original["published_at"]:
                    alternative = r
                    break

        if not original or not alternative:
            return {
                "status": "skipped",
                "reason": "nessuna coppia originale+alternativo trovata",
                "niche":  niche,
            }

        orig_ctr = float(original["ctr"] or 0)
        alt_ctr  = float(alternative["ctr"] or 0)

        # 4. Determina winner/loser
        alt_wins    = alt_ctr >= orig_ctr
        winner      = alternative if alt_wins else original
        loser       = original    if alt_wins else alternative
        winner_ctr  = alt_ctr  if alt_wins else orig_ctr
        loser_ctr   = orig_ctr if alt_wins else alt_ctr

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 4a. Scrivi design_winner in ChromaDB
        winner_template = winner["template"] or ""
        winner_cs       = winner["color_scheme"] or ""
        if winner_template or winner_cs:
            winner_text = (
                f"A/B winner: niche='{niche}' template='{winner_template}' "
                f"color_scheme='{winner_cs}' CTR={winner_ctr:.1%} "
                f"vs loser CTR={loser_ctr:.1%}. "
                f"A/B thumbnail test completed {now_str}."
            )
            winner_meta = {
                "type":         "design_winner",
                "niche":        niche,
                "product_type": product_type,
                "template":     winner_template,
                "color_scheme": winner_cs,
                "source":       "ab_test",
                "ctr":          str(round(winner_ctr, 4)),
                "date":         now_str,
            }
            try:
                await self._memory.store_insight(winner_text, winner_meta)
                logger.info(
                    "compare_ab: design_winner — niche=%s template=%s/%s CTR=%.1f%%",
                    niche, winner_template, winner_cs, winner_ctr * 100,
                )
            except Exception as exc:
                logger.warning("compare_ab: store design_winner fallito: %s", exc)

        # 4b. Rinforza low_ctr_signal per il loser
        loser_template = loser["template"] or ""
        loser_cs       = loser["color_scheme"] or ""
        if loser_template or loser_cs:
            try:
                await self.flag_low_ctr(
                    niche=niche,
                    product_type=product_type,
                    template=loser_template,
                    color_scheme=loser_cs,
                )
            except Exception as exc:
                logger.warning("compare_ab: flag_low_ctr loser fallito: %s", exc)

        result = {
            "status":     "compared",
            "niche":      niche,
            "winner": {
                "template":     winner_template,
                "color_scheme": winner_cs,
                "ctr":          round(alt_ctr if alt_ctr >= orig_ctr else orig_ctr, 4),
            },
            "loser": {
                "template":     loser_template,
                "color_scheme": loser_cs,
                "ctr":          round(orig_ctr if alt_ctr >= orig_ctr else alt_ctr, 4),
            },
        }
        logger.info(
            "compare_ab: completato — niche=%s winner_ctr=%.1f%% loser_ctr=%.1f%%",
            niche,
            (alt_ctr if alt_ctr >= orig_ctr else orig_ctr) * 100,
            (orig_ctr if alt_ctr >= orig_ctr else alt_ctr) * 100,
        )
        return result

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
