"""Scheduler — Etsy mixin: publish checker, learning loop, ads manager, shop optimizer."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("agentpexi.scheduler")


class _EtsyMixin:
    """Etsy-domain scheduled jobs: publish checker, learning loop, ads, shop optimizer."""

    # ------------------------------------------------------------------
    # Blocco 2 — Publish checker
    # ------------------------------------------------------------------

    async def _run_publish_checker(self) -> None:
        """Pubblica su Etsy tutti gli item scheduled con slot ≤ now.

        Schedulato ogni 15 minuti. Attiva Etsy Ads post-publish se policy lo prevede.
        L'effettiva chiamata API ads è implementata nel Blocco 5 (EtsyAdsManager);
        qui si marca solo ads_activated=1 come preparazione.
        """
        from apps.backend.tools.etsy_api import EtsyAPIError

        queue  = self.production_queue
        policy = self.publication_policy

        if queue is None:
            logger.debug("publish_checker: production_queue non iniettata, skip")
            return

        now = datetime.now(timezone.utc).timestamp()
        due_items = await queue.get_due_scheduled(now)

        if not due_items:
            return

        logger.info("publish_checker: %d item da pubblicare", len(due_items))

        mock = bool(getattr(self.pepe, "mock_mode", False))

        for item in due_items:
            if mock:
                await queue.set_published(item.id, etsy_listing_id="MOCK_ID")
                await self._notify_telegram(
                    f"📦 [MOCK] Pubblicato: {item.listing_title or item.niche}"
                )
                logger.info("publish_checker [MOCK] item %d", item.id)
                continue

            try:
                if self.etsy_client is None:
                    raise EtsyAPIError("etsy_client non iniettato")

                listing_id = await self.etsy_client.publish_listing(item)
                await queue.set_published(item.id, listing_id)
                await self._notify_telegram(
                    f"🎉 Pubblicato: {item.listing_title or item.niche}\n"
                    f"🔗 https://etsy.com/listing/{listing_id}"
                )
                logger.info("publish_checker: item %d → listing %s", item.id, listing_id)

                # 🔴 [video] — attiva Etsy Ads se policy lo prevede
                # Chiamata API ads implementata in Blocco 5 (EtsyAdsManager)
                if policy is not None and await policy.ads_enabled():
                    await queue.set_ads_activated(item.id)
                    logger.info(
                        "Etsy Ads attivazione marcata per listing %s", listing_id
                    )

            except EtsyAPIError as exc:
                await queue.set_failed(item.id, str(exc))
                await self._notify_telegram(
                    f"❌ Errore pubblicazione {item.listing_title or item.niche}: {exc}"
                )
                logger.error("publish_checker: item %d fallito: %s", item.id, exc)

            except Exception as exc:
                await queue.set_failed(item.id, str(exc))
                logger.exception("publish_checker: errore inatteso item %d", item.id)

    # ------------------------------------------------------------------
    # Blocco 4 / 5.3 — Etsy learning loop domenicale
    # ------------------------------------------------------------------

    async def _run_etsy_learning_loop(self) -> None:
        """Domenicale 02:00 — aggiorna segnali ChromaDB e confronta A/B thumbnail.

        Flusso:
        1. AnalyticsAgent.poll_listing_performance() — aggiorna listing_performance
           e diagnostica Ladder System (ctr_low, views_low, conv_low).
        2. LearningLoop.run_full_update() — ricalcola niche_intelligence da snapshot.
        3. LearningLoop.compare_ab_thumbnails(niche) — per ogni niche con ctr_low
           recente: confronta CTR originale vs alternativo, scrivi design_winner
           o rafforza low_ctr_signal.
        4. Invia report Telegram aggregato.
        """
        report_lines: list[str] = []
        errors: list[str] = []

        # 1. Poll listing performance
        if self.analytics_agent is not None:
            try:
                await self.analytics_agent.poll_listing_performance()
                report_lines.append("✅ Poll listing performance completato")
            except Exception as exc:
                errors.append(f"poll_listing_performance: {exc}")
                logger.error("etsy_learning_loop poll_listing: %s", exc)
        else:
            report_lines.append("ℹ️ analytics_agent non disponibile — poll skipped")

        # 2. LearningLoop update
        if self.learning_loop is not None:
            try:
                summary = await self.learning_loop.run_full_update()
                n_updated = summary.get("n_updated", 0)
                top       = summary.get("top_niches", [])
                report_lines.append(
                    f"✅ niche_intelligence: {n_updated} niche aggiornate"
                    + (f" | top: {', '.join(top[:3])}" if top else "")
                )
            except Exception as exc:
                errors.append(f"run_full_update: {exc}")
                logger.error("etsy_learning_loop run_full_update: %s", exc)

            # 3. A/B thumbnail comparison — B5/5.3
            try:
                db     = await self.memory.get_db()
                cursor = await db.execute(
                    """
                    SELECT DISTINCT pq.niche
                    FROM listing_performance lp
                    JOIN production_queue pq ON lp.production_queue_id = pq.id
                    WHERE lp.ladder_level = 'ctr_low'
                      AND lp.snapshot_at > unixepoch() - 7 * 86400
                    """
                )
                ctr_low_rows = await cursor.fetchall()
                ab_compared  = 0
                ab_skipped   = 0

                for row in ctr_low_rows:
                    niche = row["niche"]
                    try:
                        result = await self.learning_loop.compare_ab_thumbnails(niche)
                        if result.get("status") == "compared":
                            ab_compared += 1
                        else:
                            ab_skipped += 1
                    except Exception as exc:
                        logger.warning("etsy_learning_loop compare_ab [%s]: %s", niche, exc)
                        ab_skipped += 1

                if ctr_low_rows:
                    report_lines.append(
                        f"✅ A/B thumbnail: {ab_compared} confrontati, {ab_skipped} skipped"
                    )
            except Exception as exc:
                errors.append(f"compare_ab_thumbnails: {exc}")
                logger.error("etsy_learning_loop compare_ab: %s", exc)

        # 4. Report Telegram
        if errors:
            report_lines.append(f"⚠️ Errori: {'; '.join(errors[:3])}")

        if report_lines:
            msg = "📈 *Etsy learning loop* (domenica 02:00)\n\n" + "\n".join(report_lines)
            await self._notify_telegram(msg)

        logger.info(
            "etsy_learning_loop completato — %d step, %d errori",
            len(report_lines), len(errors),
        )

    # ------------------------------------------------------------------
    # Blocco 4 — polling performance listing
    # ------------------------------------------------------------------

    async def _run_poll_listing_performance(self) -> None:
        """Esegue il polling delle performance listing ogni 6 ore.

        Chiama AnalyticsAgent.poll_listing_performance() che:
        - inserisce snapshot in listing_performance
        - esegue diagnostica Ladder System
        - aggiorna LearningLoop (quando disponibile, step 4.5)
        """
        if self.analytics_agent is None:
            return
        try:
            await self.analytics_agent.poll_listing_performance()
        except Exception as exc:
            logger.error("poll_listing_performance fallito: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Blocco 5 — Shop profile optimizer
    # ------------------------------------------------------------------

    async def _run_shop_optimizer_job(self) -> None:
        """Lunedì 07:00 — aggiorna il profilo shop Etsy se le top niches sono cambiate.

        Chiama ShopProfileOptimizer.apply_shop_profile() che:
        - legge top niches da LearningLoop.get_top_niches()
        - confronta con l'ultima applicazione (config DB)
        - se cambiate: genera titolo SEO + about via Haiku e applica via Etsy API
        - se invariate: skip silenzioso

        Notifica Telegram solo se il profilo è stato effettivamente aggiornato.
        """
        if self.shop_optimizer is None:
            return
        try:
            result = await self.shop_optimizer.apply_shop_profile()
            status = result.get("status", "unknown")

            if status == "applied":
                title = result.get("title", "—")
                niches = ", ".join(result.get("niches", [])) or "—"
                await self._notify_telegram(
                    f"🏪 Shop profile aggiornato\n"
                    f"📝 Titolo: {title}\n"
                    f"📊 Niches: {niches}"
                )
                logger.info("shop_optimizer_job: profilo applicato — %s", title)

            elif status == "mock":
                logger.info("shop_optimizer_job: mock mode — nessuna chiamata API")

            elif status == "skipped":
                logger.info("shop_optimizer_job: niches invariate, skip")

            elif status in ("no_api", "error"):
                err = result.get("error", status)
                logger.warning("shop_optimizer_job: status=%s err=%s", status, err)
                await self._notify_telegram(
                    f"⚠️ Shop optimizer: {status} — {err}"
                )

        except Exception as exc:
            logger.error("shop_optimizer_job fallito: %s", exc)

    async def _run_etsy_ads_manager(self) -> None:
        """Ogni 6h — gestione automatica campagne Etsy Ads.

        Attiva ads sui listing nuovi (< 14 giorni) se policy.ads_enabled.
        Pausa ads se CTR < 1.5% dopo 7+ giorni di attività.
        Notifica Telegram solo se ci sono state azioni (attivazioni o pause).
        """
        if self.etsy_ads_manager is None:
            return
        try:
            await self.etsy_ads_manager.auto_manage_ads()
        except Exception as exc:
            logger.error("etsy_ads_manager job fallito: %s", exc)
