"""EtsyAdsManager — gestione automatica campagne Etsy Ads.

Blocco 5 / step 5.2

Motivazione:
    Etsy Ads ($1-3/giorno per listing) accelerano l'indicizzazione nei primi
    30 giorni, quando il ranking organico è ancora basso. Utile per verificare
    il CTR prima di aspettare mesi di traffico organico.

Flusso principale (auto_manage_ads, ogni 6h):
    1. Carica listing pubblicati negli ultimi 30 giorni dalla ProductionQueue.
    2. Per ogni listing senza ads attive (ads_activated=0) e pubblicato da < 14gg:
       → attiva campagna se PublicationPolicy.ads_enabled() == True
       → usa PublicationPolicy.ads_daily_budget() come budget giornaliero
    3. Per ogni listing con ads attive da ≥ 7 giorni:
       → legge stats via EtsyAPI.get_listing_ad_stats()
       → se CTR ads < 1.5% → pausa campagna + notifica Telegram
    4. Ritorna summary {activated, paused, errors}

Comandi Telegram:
    Non previsti in B5/5.2 — gestione totalmente automatica.
    Le notifiche vengono inviate via Scheduler._notify_telegram.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.publication_policy import PublicationPolicy
    from apps.backend.tools.etsy_api import EtsyAPI

logger = logging.getLogger("agentpexi.etsy_ads")

# CTR sotto questa soglia → pausa ads (1.5% = 0.015)
_ADS_CTR_PAUSE_THRESHOLD: float = 0.015

# Giorni di attività ads prima di valutare il CTR
_ADS_EVAL_DAYS: int = 7

# Finestra di attivazione automatica (listing < N giorni)
_ADS_ACTIVATE_WINDOW_DAYS: int = 14

# Finestra di ricerca listing recenti
_ADS_HISTORY_DAYS: int = 30


class EtsyAdsManager:
    """
    Gestisce le campagne Etsy Ads per i listing pubblicati.

    - Attiva automaticamente ads al publish se PublicationPolicy.ads_enabled() == True.
    - Auto-pausa dopo _ADS_EVAL_DAYS giorni se CTR < _ADS_CTR_PAUSE_THRESHOLD.

    Nota mock_mode:
        In mock mode non vengono effettuate chiamate all'API Etsy.
        Le operazioni vengono loggate e il summary riporta status="mock".
    """

    def __init__(
        self,
        etsy_client: "EtsyAPI | None",
        production_queue: "ProductionQueueService | None",
        publication_policy: "PublicationPolicy | None",
        telegram_broadcaster: Any | None = None,
        mock_mode: bool = False,
    ) -> None:
        self.etsy_client        = etsy_client
        self.production_queue   = production_queue
        self.publication_policy = publication_policy
        self._telegram_broadcast = telegram_broadcaster
        self.mock_mode          = mock_mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def activate_ad(
        self,
        listing_id: str | int,
        daily_budget_eur: float,
    ) -> bool:
        """
        Attiva una campagna Etsy Ads per un listing.

        Args:
            listing_id:       ID del listing Etsy.
            daily_budget_eur: Budget giornaliero in EUR.

        Returns:
            True se l'attivazione è riuscita (o mock), False in caso di errore.
        """
        if self.etsy_client is None:
            logger.warning("activate_ad: etsy_client non disponibile — skip (listing %s)", listing_id)
            return False
        try:
            await self.etsy_client.create_ad_campaign(
                listing_id=listing_id,
                daily_budget_eur=daily_budget_eur,
            )
            logger.info(
                "EtsyAds: campagna attivata — listing %s, budget €%.2f/day%s",
                listing_id,
                daily_budget_eur,
                " [MOCK]" if self.mock_mode else "",
            )
            return True
        except Exception as exc:
            logger.warning("activate_ad: listing %s — %s", listing_id, exc)
            return False

    async def pause_ad(self, listing_id: str | int) -> bool:
        """
        Mette in pausa la campagna ads per un listing.

        Returns:
            True se l'operazione è riuscita (o mock), False in caso di errore.
        """
        if self.etsy_client is None:
            logger.warning("pause_ad: etsy_client non disponibile — skip (listing %s)", listing_id)
            return False
        try:
            await self.etsy_client.pause_ad_campaign(listing_id=listing_id)
            logger.info(
                "EtsyAds: campagna in pausa — listing %s%s",
                listing_id,
                " [MOCK]" if self.mock_mode else "",
            )
            return True
        except Exception as exc:
            logger.warning("pause_ad: listing %s — %s", listing_id, exc)
            return False

    async def get_ad_stats(self, listing_id: str | int) -> dict:
        """
        Statistiche ads per un listing.

        Returns:
            dict con chiavi: listing_id, impressions, clicks, spend_eur, orders
            In caso di errore: zero-stats senza raise.
        """
        if self.etsy_client is None:
            return {
                "listing_id":  str(listing_id),
                "impressions": 0,
                "clicks":      0,
                "spend_eur":   0.0,
                "orders":      0,
            }
        try:
            return await self.etsy_client.get_listing_ad_stats(listing_id=listing_id)
        except Exception as exc:
            logger.warning("get_ad_stats: listing %s — %s", listing_id, exc)
            return {
                "listing_id":  str(listing_id),
                "impressions": 0,
                "clicks":      0,
                "spend_eur":   0.0,
                "orders":      0,
            }

    async def auto_manage_ads(self) -> dict:
        """
        Gestione automatica ads — chiamato dallo Scheduler ogni 6h.

        Algoritmo:
        1. Carica listing pubblicati negli ultimi _ADS_HISTORY_DAYS giorni.
        2. Listing nuovi (< _ADS_ACTIVATE_WINDOW_DAYS) senza ads → attiva se policy ok.
        3. Listing con ads (ads_activated=1) da ≥ _ADS_EVAL_DAYS → controlla CTR.
           CTR < _ADS_CTR_PAUSE_THRESHOLD → pausa + notifica.
        4. Notifica Telegram se ci sono state attivazioni o pause.

        Returns:
            dict: {activated: int, paused: int, errors: int, mock: bool}
        """
        if self.production_queue is None:
            logger.debug("auto_manage_ads: production_queue non disponibile, skip")
            return {"activated": 0, "paused": 0, "errors": 0, "mock": self.mock_mode}

        activated = 0
        paused    = 0
        errors    = 0
        now       = time.time()

        # Determina budget dalla policy
        ads_enabled    = False
        daily_budget   = 1.00
        if self.publication_policy is not None:
            try:
                ads_enabled  = await self.publication_policy.ads_enabled()
                daily_budget = await self.publication_policy.ads_daily_budget()
            except Exception as exc:
                logger.warning("auto_manage_ads: errore lettura policy — %s", exc)

        # Carica listing pubblicati negli ultimi 30 giorni
        try:
            items = await self.production_queue.get_recent(
                status="published",
                days=_ADS_HISTORY_DAYS,
                limit=200,
            )
        except Exception as exc:
            logger.error("auto_manage_ads: get_recent fallito — %s", exc)
            return {"activated": 0, "paused": 0, "errors": 1, "mock": self.mock_mode}

        paused_titles: list[str] = []
        activated_titles: list[str] = []

        for item in items:
            listing_id = item.etsy_listing_id
            if not listing_id:
                continue  # listing non ancora su Etsy

            published_at = item.published_at or 0.0
            days_live    = (now - published_at) / 86400

            # ── Auto-attiva: listing nuovo + ads non ancora attive + policy ok ─────
            if (
                days_live < _ADS_ACTIVATE_WINDOW_DAYS
                and not item.ads_activated
                and ads_enabled
            ):
                ok = await self.activate_ad(listing_id, daily_budget)
                if ok:
                    activated += 1
                    activated_titles.append(item.listing_title or str(listing_id))
                    try:
                        await self.production_queue.set_ads_activated(item.id)
                    except Exception as exc:
                        logger.warning(
                            "auto_manage_ads: set_ads_activated(%d) fallito: %s", item.id, exc
                        )
                else:
                    errors += 1
                continue

            # ── Auto-pausa: ads attive da ≥ 7gg, CTR basso ──────────────────────
            if item.ads_activated and days_live >= _ADS_EVAL_DAYS:
                try:
                    stats      = await self.get_ad_stats(listing_id)
                    impressions = stats.get("impressions", 0)
                    clicks      = stats.get("clicks", 0)

                    if impressions < 10:
                        # Troppo pochi dati — non valutare ancora
                        continue

                    ads_ctr = clicks / max(impressions, 1)

                    if ads_ctr < _ADS_CTR_PAUSE_THRESHOLD:
                        ok = await self.pause_ad(listing_id)
                        if ok:
                            paused += 1
                            title   = item.listing_title or str(listing_id)
                            paused_titles.append(
                                f"{title} (CTR ads {ads_ctr * 100:.1f}%)"
                            )
                        else:
                            errors += 1
                except Exception as exc:
                    logger.warning(
                        "auto_manage_ads: valutazione CTR listing %s: %s",
                        listing_id, exc,
                    )
                    errors += 1

        # Notifica Telegram se ci sono state azioni
        await self._send_summary(activated, paused, activated_titles, paused_titles)

        logger.info(
            "auto_manage_ads completato: activated=%d paused=%d errors=%d mock=%s",
            activated, paused, errors, self.mock_mode,
        )
        return {
            "activated": activated,
            "paused":    paused,
            "errors":    errors,
            "mock":      self.mock_mode,
        }

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    async def _send_summary(
        self,
        activated: int,
        paused: int,
        activated_titles: list[str],
        paused_titles: list[str],
    ) -> None:
        """Invia notifica Telegram se ci sono state azioni rilevanti."""
        if not activated and not paused:
            return

        mock_badge = " _(mock)_" if self.mock_mode else ""
        lines = [f"📢 *Etsy Ads update*{mock_badge}"]

        if activated:
            lines.append(f"\n✅ *Attivate {activated} campagne:*")
            for t in activated_titles[:5]:
                lines.append(f"  • {t}")
            if len(activated_titles) > 5:
                lines.append(f"  …e altri {len(activated_titles) - 5}")

        if paused:
            lines.append(f"\n⏸ *Messe in pausa {paused} campagne* (CTR < 1.5%):")
            for t in paused_titles[:5]:
                lines.append(f"  • {t}")
            if len(paused_titles) > 5:
                lines.append(f"  …e altri {len(paused_titles) - 5}")

        msg = "\n".join(lines)
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(msg)
            except Exception as exc:
                logger.warning("_send_summary: broadcast fallito: %s", exc)
