"""BundleStrategy — trigger e spec generation per listing bundle.

Blocco 4 — step 4.6

Un "bundle" è un listing Etsy che aggrega 3-5 prodotti digitali della stessa
niche in un unico download a prezzo scontato (~70% della somma dei prezzi
individuali). Strategia consigliata da Alfie per aumentare AOV e visibilità.

Logica trigger (should_create_bundle):
  1. ≥3 listing pubblicati della stessa niche nell'ultimo mese (non già bundle)
  2. Nessun bundle attivo/in-pipeline per quella niche
  3. performance_score (da LearningLoop) > 0.6 — bundle ha senso su niche validate

generate_bundle_spec ritorna una spec completa che AutopilotLoop può passare
direttamente a DesignAgent + PublisherAgent senza ulteriore ricerca.

Invarianti:
  - BundleStrategy non modifica DB — è read-only + spec generation.
  - LearningLoop è opzionale (None guard) — fallback score = 0.5.
  - should_create_bundle è idempotente e fast (solo query SQL, no LLM).
"""

from __future__ import annotations

import json
import logging
import time as _time
from typing import Any

from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.bundle_strategy")

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------
BUNDLE_TRIGGER_MIN_LISTINGS  = 3       # listing pubblicati nello stesso mese
BUNDLE_TRIGGER_WINDOW_DAYS   = 30      # finestra temporale per contare i listing
BUNDLE_MIN_PERFORMANCE_SCORE = 0.6     # score minimo per triggherare un bundle
BUNDLE_PRICE_DISCOUNT        = 0.70    # 70% della somma prezzi individuali
BUNDLE_MAX_COMPONENTS        = 5       # massimo listing da includere nella spec
BUNDLE_KEYWORD_MAX           = 13      # Etsy accetta max 13 tag


class BundleStrategy:
    """
    Strategia bundle: decide quando creare un bundle e genera la sua spec.

    Dipendenze:
      - memory: MemoryManager  (accesso DB production_queue)
      - learning_loop: Any | None  (opzionale — legge performance_score)
    """

    def __init__(
        self,
        memory: MemoryManager,
        learning_loop: Any | None = None,
    ) -> None:
        self._memory        = memory
        self._learning_loop = learning_loop

    async def _db(self):
        return await self._memory.get_db()

    # ------------------------------------------------------------------
    # Trigger check
    # ------------------------------------------------------------------

    async def should_create_bundle(self, niche: str) -> bool:
        """
        Ritorna True se la niche soddisfa tutte le condizioni per un bundle:
          1. ≥ BUNDLE_TRIGGER_MIN_LISTINGS pubblicati nell'ultimo mese
          2. Nessun bundle attivo (published / scheduled / approved / pending)
          3. performance_score ≥ BUNDLE_MIN_PERFORMANCE_SCORE
        """
        db     = await self._db()
        cutoff = _time.time() - BUNDLE_TRIGGER_WINDOW_DAYS * 86400

        # 1 — conta listing pubblicati (escludi bundle stessi)
        cursor = await db.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM production_queue
            WHERE niche        = ?
              AND status       = 'published'
              AND product_type != 'bundle'
              AND published_at  >= ?
            """,
            (niche, cutoff),
        )
        row = await cursor.fetchone()
        count = int(row["cnt"] or 0)

        if count < BUNDLE_TRIGGER_MIN_LISTINGS:
            logger.debug(
                "should_create_bundle [%s]: solo %d listing pubblicati (min %d)",
                niche, count, BUNDLE_TRIGGER_MIN_LISTINGS,
            )
            return False

        # 2 — controlla che non esista già un bundle attivo per questa niche
        cursor = await db.execute(
            """
            SELECT 1 FROM production_queue
            WHERE niche        = ?
              AND product_type = 'bundle'
              AND status NOT IN ('skipped', 'failed', 'discarded')
            LIMIT 1
            """,
            (niche,),
        )
        has_bundle = await cursor.fetchone()

        if has_bundle:
            logger.debug("should_create_bundle [%s]: bundle già attivo in pipeline", niche)
            return False

        # 3 — performance_score (richiede LearningLoop)
        score = await self._get_performance_score(niche)
        if score < BUNDLE_MIN_PERFORMANCE_SCORE:
            logger.debug(
                "should_create_bundle [%s]: score %.3f < %.1f",
                niche, score, BUNDLE_MIN_PERFORMANCE_SCORE,
            )
            return False

        logger.info(
            "should_create_bundle [%s]: TRIGGER — %d listing, score %.3f",
            niche, count, score,
        )
        return True

    # ------------------------------------------------------------------
    # Spec generation
    # ------------------------------------------------------------------

    async def generate_bundle_spec(self, niche: str) -> dict:
        """
        Genera la spec completa per un bundle della niche.

        Ritorna un dict pronto per AutopilotLoop._run_design_pipeline:
        {
            niche, product_type,
            component_titles,    # list[str] titoli listing inclusi
            component_images,    # list[str] image_url dei componenti
            suggested_price,     # 70% somma prezzi individuali (arrotondato a .99)
            keywords,            # list[str] merge deduplicate dei keywords
            entry_score,         # score niche usato come entry_score
            n_components,        # numero effettivo di componenti
        }

        Se la niche non ha abbastanza listing, ritorna spec con dati parziali
        (non solleva eccezione — è responsabilità del chiamante verificare).
        """
        db     = await self._db()
        cutoff = _time.time() - BUNDLE_TRIGGER_WINDOW_DAYS * 86400

        cursor = await db.execute(
            """
            SELECT listing_title, keywords, listing_price, image_url
            FROM production_queue
            WHERE niche        = ?
              AND status       = 'published'
              AND product_type != 'bundle'
              AND published_at  >= ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (niche, cutoff, BUNDLE_MAX_COMPONENTS),
        )
        rows = await cursor.fetchall()

        component_titles = []
        component_images = []
        all_keywords     = []
        total_price      = 0.0

        for row in rows:
            if row["listing_title"]:
                component_titles.append(row["listing_title"])
            if row["image_url"]:
                component_images.append(row["image_url"])
            if row["listing_price"]:
                total_price += float(row["listing_price"])

            # Deserializza keywords JSON
            raw_kw = row["keywords"]
            if raw_kw:
                try:
                    kw_list = json.loads(raw_kw) if isinstance(raw_kw, str) else raw_kw
                    if isinstance(kw_list, list):
                        all_keywords.extend(kw_list)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Prezzo bundle: 70% della somma, arrotondato a .99
        suggested_price = 0.0
        if total_price > 0.0:
            raw_price       = total_price * BUNDLE_PRICE_DISCOUNT
            suggested_price = round(round(raw_price, 0) - 0.01, 2)
            suggested_price = max(suggested_price, 0.99)

        merged_keywords = self._merge_keywords(all_keywords)
        score           = await self._get_performance_score(niche)

        spec = {
            "niche":             niche,
            "product_type":      "bundle",
            "component_titles":  component_titles,
            "component_images":  component_images,
            "suggested_price":   suggested_price,
            "keywords":          merged_keywords,
            "entry_score":       score,
            "n_components":      len(rows),
        }

        logger.info(
            "generate_bundle_spec [%s]: %d componenti, prezzo €%.2f, %d keywords",
            niche, len(rows), suggested_price, len(merged_keywords),
        )
        return spec

    # ------------------------------------------------------------------
    # Scan tutte le niches per bundle opportunità
    # ------------------------------------------------------------------

    async def check_all_niches(self) -> list[dict]:
        """
        Scansiona tutte le niches con listing pubblicati e ritorna
        quelle bundle-ready.

        Usato da:
          - AutopilotLoop._check_bundle_priority() per prioritizzare bundles
          - /bundle Telegram command (senza argomenti)

        Ritorna lista di {niche, n_listings, score, spec}.
        """
        db     = await self._db()
        cutoff = _time.time() - BUNDLE_TRIGGER_WINDOW_DAYS * 86400

        # Niches con abbastanza listing pubblicati nell'ultimo mese
        cursor = await db.execute(
            """
            SELECT niche, COUNT(*) AS cnt
            FROM production_queue
            WHERE status       = 'published'
              AND product_type != 'bundle'
              AND published_at  >= ?
            GROUP BY niche
            HAVING cnt >= ?
            ORDER BY cnt DESC
            """,
            (cutoff, BUNDLE_TRIGGER_MIN_LISTINGS),
        )
        candidates = await cursor.fetchall()

        results = []
        for row in candidates:
            niche = row["niche"]
            if await self.should_create_bundle(niche):
                spec = await self.generate_bundle_spec(niche)
                results.append({
                    "niche":      niche,
                    "n_listings": int(row["cnt"]),
                    "score":      spec["entry_score"],
                    "spec":       spec,
                })

        logger.info(
            "check_all_niches: %d candidati → %d bundle-ready",
            len(candidates), len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    def _merge_keywords(self, keywords: list[str]) -> list[str]:
        """
        Deduplicazione case-insensitive, mantiene ordine first-seen,
        cap a BUNDLE_KEYWORD_MAX (Etsy max 13 tag).

        Aggiunge "bundle" e "digital bundle" come prime keyword se non presenti.
        """
        seen    = {}
        ordered = []

        # Seed con keyword bundle-specific
        for seed_kw in ("digital bundle", "bundle", "printable bundle"):
            key = seed_kw.lower()
            if key not in seen:
                seen[key]  = True
                ordered.append(seed_kw)

        for kw in keywords:
            if not kw or not isinstance(kw, str):
                continue
            key = kw.strip().lower()
            if key and key not in seen:
                seen[key] = True
                ordered.append(kw.strip())

        return ordered[:BUNDLE_KEYWORD_MAX]

    async def _get_performance_score(self, niche: str) -> float:
        """
        Legge performance_score da LearningLoop (se disponibile)
        o da niche_intelligence direttamente. Fallback: 0.5.
        """
        if self._learning_loop is not None:
            try:
                intel = await self._learning_loop.get_intel(niche, None)
                if intel:
                    return float(intel["performance_score"])
            except Exception as exc:
                logger.warning("_get_performance_score via LearningLoop fallito: %s", exc)

        # Fallback: lettura diretta niche_intelligence
        try:
            db     = await self._db()
            cursor = await db.execute(
                """
                SELECT performance_score FROM niche_intelligence
                WHERE niche = ?
                ORDER BY performance_score DESC
                LIMIT 1
                """,
                (niche,),
            )
            row = await cursor.fetchone()
            if row:
                return float(row["performance_score"])
        except Exception as exc:
            logger.warning("_get_performance_score fallback DB fallito: %s", exc)

        return 0.5
