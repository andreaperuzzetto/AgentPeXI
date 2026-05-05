"""AnalyticsAgent — diagnostics and remediation mixin."""
from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any

from apps.backend.agents._analytics.constants import (
    CONV_MIN,
    CTR_MIN,
    MIN_DAYS_LIVE,
    REMEDIATION_COOLDOWN_HOURS,
    VIEWS_MIN_7DAYS,
)

logger = logging.getLogger("agentpexi.analytics")


class _AnalyticsDiagnosticsMixin:

    # ------------------------------------------------------------------
    # Ladder System — polling performance (schedulato ogni 6h)
    # ------------------------------------------------------------------

    async def poll_listing_performance(self) -> None:
        """
        Inserisce snapshot in listing_performance per ogni listing pubblicato.
        Poi esegue run_ladder_diagnostic_all() e aggiorna il LearningLoop
        (se disponibile — wired in step 4.5).

        Schedulato: IntervalTrigger(hours=6)
        """
        if self._production_queue is None:
            logger.warning("poll_listing_performance: production_queue non configurato — skip")
            return

        published = await self._production_queue.get_recent(
            status="published", days=90, limit=200
        )
        if not published:
            logger.info("poll_listing_performance: nessun listing pubblicato")
            return

        db  = await self.memory.get_db()
        sem = asyncio.Semaphore(5)

        async def _poll_one(item) -> bool:
            if not item.etsy_listing_id:
                return False
            async with sem:
                try:
                    stats = await self.etsy_api.get_listing_stats(item.etsy_listing_id)
                except Exception as exc:
                    logger.warning("get_listing_stats %s fallito: %s", item.etsy_listing_id, exc)
                    return False

            days_live = 0
            if item.published_at:
                days_live = int((_time.time() - item.published_at) / 86400)

            views      = stats.get("views", 0)
            clicks     = stats.get("clicks", 0)
            favorites  = stats.get("favorites", 0)
            orders     = stats.get("num_orders", 0)
            revenue    = stats.get("revenue_eur", 0.0)
            ctr        = round(clicks / max(views, 1), 4)
            conv_rate  = round(orders / max(clicks, 1), 4)
            fav_rate   = round(favorites / max(views, 1), 4)

            # template + color_scheme: lookup dal listing memory per CTR attribution
            template     = ""
            color_scheme = ""
            try:
                listings = await self.memory.get_etsy_listings()
                ml = next(
                    (l for l in listings if str(l.get("listing_id")) == str(item.etsy_listing_id)),
                    None,
                )
                if ml:
                    template     = ml.get("template") or ""
                    color_scheme = ml.get("color_scheme") or ""
            except Exception:
                logger.exception("Unexpected error")
            await db.execute(
                """
                INSERT INTO listing_performance
                    (etsy_listing_id, production_queue_id, niche, product_type,
                     template, color_scheme,
                     views, clicks, favorites, orders, revenue_eur,
                     ctr, conversion_rate, favorite_rate, days_live)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.etsy_listing_id), item.id, item.niche, item.product_type,
                    template, color_scheme,
                    views, clicks, favorites, orders, revenue,
                    ctr, conv_rate, fav_rate, days_live,
                ),
            )
            return True

        results = await asyncio.gather(
            *[_poll_one(i) for i in published], return_exceptions=True
        )
        success = sum(1 for r in results if r is True)
        await db.commit()
        logger.info("poll_listing_performance: %d/%d snapshot inseriti", success, len(published))

        await self.run_ladder_diagnostic_all()

        if self._learning_loop is not None:
            try:
                await self._learning_loop.update_niche_intelligence()
            except Exception as exc:
                logger.warning("update_niche_intelligence fallito: %s", exc)

    # ------------------------------------------------------------------
    # Ladder System — diagnostica
    # ------------------------------------------------------------------

    async def run_ladder_diagnostic_all(self) -> list[dict]:
        """
        Esegue diagnostica Ladder su tutti i listing pubblicati con ≥ MIN_DAYS_LIVE.
        Aggiorna ladder_level in listing_performance. Ritorna lista risultati.
        """
        if self._production_queue is None:
            return []
        published = await self._production_queue.get_recent(
            status="published", days=90, limit=200
        )
        results = []
        for item in published:
            result = await self.run_ladder_diagnostic_by_id(item.id)
            results.append(result)
        return results

    async def run_ladder_diagnostic_by_id(self, queue_item_id: int) -> dict:
        """
        Diagnostica Ladder per singolo listing (tramite production_queue id).
        Classifica: too_new | views_low | ctr_low | conv_low | ok.
        Innesca _trigger_remediation se l'azione non è stata tentata di recente.
        """
        if self._production_queue is None:
            return {"error": "production_queue non configurato"}

        item = await self._production_queue.get_item(queue_item_id)
        if not item:
            return {"error": f"item {queue_item_id} non trovato"}

        db     = await self.memory.get_db()
        cursor = await db.execute(
            """
            SELECT views, clicks, orders, ctr, conversion_rate, days_live,
                   template, color_scheme
            FROM listing_performance
            WHERE production_queue_id = ?
            ORDER BY snapshot_at DESC
            LIMIT 1
            """,
            (queue_item_id,),
        )
        row = await cursor.fetchone()

        if not row or row["days_live"] < MIN_DAYS_LIVE:
            return {
                "item_id": queue_item_id,
                "niche":   item.niche,
                "level":   "too_new",
                "action":  None,
            }

        # Classificazione a cascata (dal basso della ladder)
        if row["views"] < VIEWS_MIN_7DAYS:
            level  = "views_low"
            action = "rewrite_seo"
        elif row["ctr"] < CTR_MIN:
            level  = "ctr_low"
            action = "regen_thumbnail"
        elif row["conversion_rate"] < CONV_MIN and row["clicks"] >= 10:
            level  = "conv_low"
            action = "update_listing"
        else:
            level  = "ok"
            action = None

        # Persiste ladder_level nel record più recente
        await db.execute(
            """
            UPDATE listing_performance
            SET ladder_level = ?, last_diagnostic_at = unixepoch()
            WHERE production_queue_id = ?
              AND snapshot_at = (
                  SELECT MAX(snapshot_at) FROM listing_performance
                  WHERE production_queue_id = ?
              )
            """,
            (level, queue_item_id, queue_item_id),
        )
        await db.commit()

        if action and not self._remediation_attempted_recently(queue_item_id, action):
            await self._trigger_remediation(item, level, action, row)

        result = {
            "item_id":   queue_item_id,
            "niche":     item.niche,
            "level":     level,
            "action":    action,
            "views":     row["views"],
            "ctr":       f"{row['ctr'] * 100:.1f}%",
            "conv":      f"{row['conversion_rate'] * 100:.1f}%",
            "days_live": row["days_live"],
        }
        logger.info(
            "Ladder [%s] listing %s → %s (action: %s)",
            item.niche, item.etsy_listing_id, level, action,
        )
        return result

    async def _trigger_remediation(self, item: Any, level: str, action: str, row: Any) -> None:
        """
        Azione correttiva identificata dal Ladder System.

        Flusso per ogni action:
          rewrite_seo    → flag_for_seo_revision (abbassa score niche) +
                           crea nuovo pending_design (AutopilotLoop farà fresh research)
          regen_thumbnail → flag_low_ctr (ChromaDB — DesignAgent eviterà template/colore) +
                            crea nuovo pending_design (DesignAgent genererà thumbnail alternativa)
          update_listing  → solo notifica Telegram (richiede intervento manuale)

        Registra il tentativo in _remediation_log per evitare spam (cooldown 48h).
        """
        template     = row["template"]     if row["template"]     else ""
        color_scheme = row["color_scheme"] if row["color_scheme"] else ""
        title        = (item.listing_title or item.niche)[:60]
        queued_msg   = ""    # appendice al messaggio se item enqueued

        if action == "rewrite_seo":
            # 1. Segnala al LearningLoop: abbassa score per ridurre priorità niche
            if self._learning_loop is not None:
                try:
                    await self._learning_loop.flag_for_seo_revision(
                        item.niche, item.product_type
                    )
                except Exception as exc:
                    logger.warning("flag_for_seo_revision fallito: %s", exc)

            # 2. Enqueue nuovo pending_design — il pipeline farà fresh keyword research
            if self._production_queue is not None:
                try:
                    run_id = f"remediation_seo_{item.id}"
                    new_id = await self._production_queue.create_item(
                        niche=item.niche,
                        product_type=item.product_type,
                        keywords=item.keywords or [],
                        entry_score=0.85,      # alta priorità — listing esistente fallisce
                        loop_run_id=run_id,
                    )
                    queued_msg = f"\n🔄 Nuovo listing enqueued (#{new_id}) per SEO alternativo."
                    logger.info(
                        "Ladder rewrite_seo: enqueued item #%d per niche=%s",
                        new_id, item.niche,
                    )
                except Exception as exc:
                    logger.warning("create_item per rewrite_seo fallito: %s", exc)

            msg = (
                f"🔍 SEO revision needed\n"
                f"📦 {title}\n"
                f"📊 {row['views']} views dopo {row['days_live']} giorni"
                f" — soglia: {VIEWS_MIN_7DAYS}\n\n"
                f"Score niche abbassato — sarà ri-analizzata con meno priorità."
                f"{queued_msg}\n"
                f"#ladder #views_low"
            )

        elif action == "regen_thumbnail":
            # 1. Segnala al LearningLoop: template+color_scheme → low_ctr_signal in ChromaDB
            if self._learning_loop is not None:
                try:
                    await self._learning_loop.flag_low_ctr(
                        item.niche, item.product_type, template, color_scheme
                    )
                except Exception as exc:
                    logger.warning("flag_low_ctr fallito: %s", exc)

            # 2. Enqueue nuovo pending_design — DesignAgent leggerà low_ctr_signal
            #    e genererà thumbnail con template/colore diverso
            if self._production_queue is not None:
                try:
                    run_id = f"remediation_thumbnail_{item.id}"
                    new_id = await self._production_queue.create_item(
                        niche=item.niche,
                        product_type=item.product_type,
                        keywords=item.keywords or [],
                        entry_score=0.90,      # priorità massima — CTR critico
                        loop_run_id=run_id,
                    )
                    queued_msg = f"\n🔄 Nuovo listing enqueued (#{new_id}) con thumbnail alternativa."
                    logger.info(
                        "Ladder regen_thumbnail: enqueued item #%d per niche=%s template=%s/%s",
                        new_id, item.niche, template, color_scheme,
                    )
                except Exception as exc:
                    logger.warning("create_item per regen_thumbnail fallito: %s", exc)

            ctr_pct = f"{row['ctr'] * 100:.1f}%"
            msg = (
                f"🖼 Thumbnail update needed\n"
                f"📦 {title}\n"
                f"📊 CTR {ctr_pct} < soglia {CTR_MIN * 100:.0f}%"
                f" — thumbnail non converte\n\n"
                f"Template '{template}' / colore '{color_scheme}' segnalato come low-CTR."
                f" La prossima generazione lo eviterà."
                f"{queued_msg}\n"
                f"#ladder #ctr_low"
            )

        elif action == "update_listing":
            # Richiede intervento manuale — solo notifica
            msg = (
                f"📝 Listing update needed\n"
                f"📦 {title}\n"
                f"📊 Conv {row['conversion_rate'] * 100:.1f}% < soglia"
                f" {CONV_MIN * 100:.0f}% su {row['clicks']} click\n\n"
                f"Suggerimenti: più foto mockup, description benefits-first,"
                f" controlla prezzo vs competitor.\n"
                f"#ladder #conv_low"
            )

        else:
            return

        await self._notify_telegram(msg)
        self._log_remediation_attempt(item.id, action)

    def _remediation_attempted_recently(self, queue_item_id: int, action: str) -> bool:
        """
        True se lo stesso action è già stato notificato per questo item
        nelle ultime REMEDIATION_COOLDOWN_HOURS ore.
        """
        last_ts  = self._remediation_log.get(queue_item_id, {}).get(action, 0.0)
        cooldown = REMEDIATION_COOLDOWN_HOURS * 3600
        return (_time.time() - last_ts) < cooldown

    def _log_remediation_attempt(self, queue_item_id: int, action: str) -> None:
        """Registra timestamp dell'ultimo tentativo di remediation."""
        if queue_item_id not in self._remediation_log:
            self._remediation_log[queue_item_id] = {}
        self._remediation_log[queue_item_id][action] = _time.time()
