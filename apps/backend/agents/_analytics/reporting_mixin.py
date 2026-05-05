"""AnalyticsAgent — reporting mixin."""
from __future__ import annotations

import logging

logger = logging.getLogger("agentpexi.analytics")


class _AnalyticsReportingMixin:

    # ------------------------------------------------------------------
    # Passo 5 — Report aggregato
    # ------------------------------------------------------------------

    async def _build_report(
        self,
        listings: list[dict],
        synced: list[dict],
        failure_counts: dict,
        bestsellers: list[dict],
        today_str: str,
    ) -> dict:
        total_views = sum(s.get("views", 0) for s in synced)
        total_favorites = sum(s.get("favorites", 0) for s in synced)
        total_sales = sum(s.get("sales", 0) for s in synced)
        total_revenue = sum(s.get("revenue_eur", 0) for s in synced)

        # A/B performance
        all_listings = await self.memory.get_etsy_listings()
        ab_perf = {"A": {"count": 0, "views": 0, "sales": 0, "revenue": 0},
                   "B": {"count": 0, "views": 0, "sales": 0, "revenue": 0}}
        for l in all_listings:
            v = l.get("ab_price_variant")
            if v in ab_perf:
                ab_perf[v]["count"] += 1
                ab_perf[v]["views"] += l.get("views", 0)
                ab_perf[v]["sales"] += l.get("sales", 0)
                ab_perf[v]["revenue"] += l.get("revenue_eur", 0)

        for v in ab_perf:
            c = ab_perf[v]["count"]
            if c > 0:
                ab_perf[v]["avg_views"] = ab_perf[v]["views"] / c
                ab_perf[v]["avg_sales"] = ab_perf[v]["sales"] / c
                ab_perf[v]["avg_revenue"] = ab_perf[v]["revenue"] / c

        # Conversion rate per variante
        for v in ("A", "B"):
            v_views = ab_perf[v].get("views", 0)
            v_sales = ab_perf[v].get("sales", 0)
            ab_perf[v]["conversion_rate"] = round(v_sales / v_views, 4) if v_views > 0 else 0.0

        # Winner esplicito (solo se dati sufficienti)
        ab_winner = None
        ab_winner_confidence = "insufficient_data"

        a_conv = ab_perf["A"].get("conversion_rate", 0)
        b_conv = ab_perf["B"].get("conversion_rate", 0)
        a_count = ab_perf["A"].get("count", 0)
        b_count = ab_perf["B"].get("count", 0)

        if a_count >= 3 and b_count >= 3:
            if a_conv > b_conv * 1.1:
                ab_winner = "A"
                ab_winner_confidence = "low" if (a_count + b_count) < 10 else "medium"
            elif b_conv > a_conv * 1.1:
                ab_winner = "B"
                ab_winner_confidence = "low" if (a_count + b_count) < 10 else "medium"
            else:
                ab_winner = "inconclusive"
                ab_winner_confidence = "medium"

        ab_perf["winner"] = ab_winner
        ab_perf["winner_confidence"] = ab_winner_confidence

        # Delta views giornaliero (daily, non cumulativo)
        delta_views_today = 0
        try:
            for synced_item in synced:
                s_lid = synced_item["listing_id"]
                current_views = synced_item.get("views", 0)
                prev_views = await self.memory.get_listing_prev_views(s_lid)
                if prev_views is not None:
                    delta_views_today += max(0, current_views - prev_views)
        except Exception:
            delta_views_today = 0

        # Conteggi per status
        drafts = len([l for l in all_listings if l.get("status") == "draft"])
        active_count = len([l for l in all_listings if l.get("status") == "active"])

        return {
            "date": today_str,
            "total_listings_active": active_count,
            "total_views": total_views,
            "total_favorites": total_favorites,
            "total_sales": total_sales,
            "total_revenue_eur": total_revenue,
            "failures": failure_counts,
            "bestsellers": bestsellers,
            "ab_performance": ab_perf,
            "delta_views_vs_yesterday": delta_views_today,
            "drafts": drafts,
        }

    # ------------------------------------------------------------------
    # Passo 6 — Summary Telegram
    # ------------------------------------------------------------------

    async def _send_daily_summary(self, report: dict, date_str: str) -> None:
        total_views = report["total_views"]
        total_fav = report["total_favorites"]
        total_sales = report["total_sales"]
        total_rev = report["total_revenue_eur"]
        delta = report["delta_views_vs_yesterday"]
        active = report["total_listings_active"]
        drafts = report.get("drafts", 0)
        failures = report["failures"]
        tot_failures = sum(failures.values())

        # Bestseller
        if report["bestsellers"]:
            bs = report["bestsellers"][0]
            bs_line = f"{bs['title'][:40]} ({bs['sales']} vendite)"
        else:
            bs_line = "nessuno"

        # A/B test
        ab = report.get("ab_performance", {})
        ab_winner = ab.get("winner")
        if ab_winner and ab_winner != "inconclusive":
            ab_line = f"A/B: variante {ab_winner} vince ({ab.get('winner_confidence', '')} confidence)\n"
        elif ab_winner == "inconclusive":
            ab_line = "A/B: dati insufficienti\n"
        else:
            ab_line = ""

        # Failures con dettaglio
        failure_detail = ""
        if tot_failures:
            parts = []
            if failures.get("no_views"):
                parts.append(f"{failures['no_views']} senza views >7gg")
            if failures.get("no_conversion"):
                parts.append(f"{failures['no_conversion']} senza conversioni >45gg")
            failure_detail = f"Da ottimizzare: {', '.join(parts)}\n"

        delta_sign = f"+{delta}" if delta >= 0 else str(delta)

        msg = (
            f"Etsy — {date_str}\n"
            f"{'─' * 14}\n"
            f"Views: {total_views} ({delta_sign} vs ieri)  |  Favorites: {total_fav}\n"
            f"Vendite: {total_sales}  |  Revenue: €{total_rev:.2f}\n"
            f"Listing attivi: {active}  |  Bozze: {drafts}\n"
            f"{ab_line}"
            f"Bestseller: {bs_line}\n"
            f"{failure_detail}"
        ).rstrip()
        await self._notify_telegram(msg)

    # ------------------------------------------------------------------
    # Notifica Telegram
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                logger.exception("Unexpected error")

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _calculate_analytics_confidence(
        self,
        listings: list[dict],
        synced: list[dict],
        failure_counts: dict,
    ) -> tuple[float, list[str]]:
        """Calcola confidence score per il report analytics."""
        missing: list[str] = []
        score = 0.0

        # 50% — sync success rate
        if listings:
            sync_rate = len(synced) / len(listings)
            score += 0.50 * sync_rate
            if sync_rate < 1.0:
                missing.append(f"{len(listings) - len(synced)} listing non sincronizzati")
        else:
            score += 0.50

        # 30% — sales data quality
        if synced:
            with_real_sales = sum(1 for s in synced if s.get("sales", 0) > 0)
            if with_real_sales > 0:
                score += 0.30
            else:
                score += 0.10
                missing.append("Tutte le vendite a 0 — verificare endpoint transazioni Etsy")
        else:
            score += 0.30

        # 20% — failure analysis eseguita
        score += 0.20

        return round(score, 2), missing
