"""FinanceTracker — P&L per listing, fee model Etsy corretto, goal €500/mese.

Fee model (fonte: Etsy Seller Handbook 2026 + video Alfie):
  - Listing fee: $0.20 per listing (fatturata al publish, non ammortizzata per ordine)
  - Transaction fee: 6.5% del prezzo di vendita
  - Payment processing: ~4% del prezzo di vendita
  - Rinnovo listing: ogni 4 mesi o ad ogni vendita

Blocco 4 — step 4.4
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone
from typing import Any

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.finance_tracker")

# ---------------------------------------------------------------------------
# Costanti fee Etsy (aggiornabili via config DB in futuro — B5)
# ---------------------------------------------------------------------------
ETSY_LISTING_FEE_USD  = 0.20    # al publish + rinnovo ogni 4 mesi
ETSY_TRANSACTION_PCT  = 0.065   # 6.5% del prezzo di vendita
ETSY_PAYMENT_PCT      = 0.04    # ~4% processing
EUR_USD_RATE          = 1.08    # usato per conversione USD → EUR nei calcoli
GOAL_EUR_DEFAULT      = 500.0   # target mensile (configurabile via /goal set)


# ---------------------------------------------------------------------------
# Utility pura — usata anche da AnalyticsAgent
# ---------------------------------------------------------------------------

def calculate_net(
    gross_eur: float,
    design_cost_usd: float,
    listing_fee_usd: float = ETSY_LISTING_FEE_USD,
) -> dict:
    """
    Calcola netto per una singola vendita.

    🔴 listing_fee_usd è $0.20 per listing — fatturata UNA VOLTA al publish,
    non ammortizzata per ordine. Viene inclusa qui solo se passata esplicitamente
    (es. prima vendita di un listing). Le vendite successive passano 0.0.

    Ritorna: gross_eur, transaction_fee, listing_fee_eur, design_cost_eur,
             net_eur, margin_pct
    """
    gross_usd       = gross_eur * EUR_USD_RATE
    transaction_fee = (gross_usd * ETSY_TRANSACTION_PCT) / EUR_USD_RATE
    payment_fee     = (gross_usd * ETSY_PAYMENT_PCT) / EUR_USD_RATE
    listing_fee_eur = listing_fee_usd / EUR_USD_RATE
    design_eur      = design_cost_usd / EUR_USD_RATE

    total_fees_eur  = transaction_fee + payment_fee
    net_eur         = gross_eur - total_fees_eur - design_eur

    margin_pct = round((net_eur / gross_eur) * 100, 1) if gross_eur > 0 else 0.0

    return {
        "gross_eur":        round(gross_eur, 4),
        "transaction_fee":  round(total_fees_eur, 4),
        "listing_fee_eur":  round(listing_fee_eur, 4),
        "design_cost_eur":  round(design_eur, 4),
        "net_eur":          round(net_eur, 4),
        "margin_pct":       margin_pct,
    }


def break_even_price(design_cost_usd: float, listing_fee_usd: float = ETSY_LISTING_FEE_USD) -> float:
    """
    Prezzo minimo (EUR) per coprire fee Etsy + costo design.
    Solve: gross = gross*(tx+pay) + design + listing_fee  →  gross*(1-fee_pct) = costs
    """
    fee_pct     = ETSY_TRANSACTION_PCT + ETSY_PAYMENT_PCT     # 10.5%
    costs_eur   = (design_cost_usd + listing_fee_usd) / EUR_USD_RATE
    if fee_pct >= 1.0:
        return costs_eur
    return round(costs_eur / (1.0 - fee_pct), 2)


# ---------------------------------------------------------------------------
# FinanceTracker
# ---------------------------------------------------------------------------

class FinanceTracker:
    """
    Traccia vendite, calcola P&L e monitora il goal mensile.

    Tutte le scritture e letture passano per aiosqlite via memory.get_db().
    Non fa chiamate LLM — è un service di dominio puro.
    """

    def __init__(self, memory: Any) -> None:
        self._memory = memory

    async def _db(self):
        return await self._memory.get_db()

    # ------------------------------------------------------------------
    # Scrittura — record_sale
    # ------------------------------------------------------------------

    async def record_sale(
        self,
        listing_id: str,
        order_id: str,
        gross_eur: float,
        niche: str = "",
        product_type: str = "",
        design_cost_usd: float = 0.0,
        is_first_sale: bool = False,
    ) -> dict:
        """
        Registra una vendita in revenue_events.

        listing_fee_eur viene inclusa solo se is_first_sale=True
        (la fee è pagata al publish, non ad ogni ordine).
        Idempotente: order_id UNIQUE previene duplicati.

        Ritorna il calcolo net per questa vendita.
        """
        listing_fee_usd = ETSY_LISTING_FEE_USD if is_first_sale else 0.0
        net_data = calculate_net(
            gross_eur=gross_eur,
            design_cost_usd=design_cost_usd,
            listing_fee_usd=listing_fee_usd,
        )

        db = await self._db()
        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO revenue_events
                    (etsy_listing_id, order_id, niche, product_type,
                     gross_eur, etsy_fee_eur, net_eur,
                     design_cost_eur, listing_fee_eur, sold_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch())
                """,
                (
                    str(listing_id), order_id, niche, product_type,
                    net_data["gross_eur"],
                    net_data["transaction_fee"],
                    net_data["net_eur"],
                    net_data["design_cost_eur"],
                    net_data["listing_fee_eur"],
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.error("record_sale fallito listing=%s order=%s: %s", listing_id, order_id, exc)
            raise

        logger.info(
            "Vendita registrata: listing=%s order=%s gross=%.2f€ net=%.2f€",
            listing_id, order_id, gross_eur, net_data["net_eur"],
        )
        return net_data

    # ------------------------------------------------------------------
    # Lettura — monthly_summary
    # ------------------------------------------------------------------

    async def monthly_summary(self, year: int | None = None, month: int | None = None) -> dict:
        """
        P&L aggregato per mese (default: mese corrente).

        Ritorna: year, month, gross_eur, etsy_fees_eur, listing_fees_eur,
                 design_costs_eur, net_eur, n_sales, margin_pct
        """
        now = datetime.now(timezone.utc)
        year  = year  or now.year
        month = month or now.month

        # Intervallo timestamp per il mese richiesto
        start = datetime(year, month, 1, tzinfo=timezone.utc).timestamp()
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()

        db = await self._db()
        cursor = await db.execute(
            """
            SELECT
                COUNT(*)            AS n_sales,
                SUM(gross_eur)      AS gross,
                SUM(etsy_fee_eur)   AS fees,
                SUM(listing_fee_eur) AS listing_fees,
                SUM(design_cost_eur) AS design_costs,
                SUM(net_eur)        AS net
            FROM revenue_events
            WHERE sold_at >= ? AND sold_at < ?
            """,
            (start, end),
        )
        row = await cursor.fetchone()

        gross         = float(row["gross"]         or 0.0)
        fees          = float(row["fees"]          or 0.0)
        listing_fees  = float(row["listing_fees"]  or 0.0)
        design_costs  = float(row["design_costs"]  or 0.0)
        net           = float(row["net"]           or 0.0)
        n_sales       = int(row["n_sales"]         or 0)
        margin_pct    = round((net / gross) * 100, 1) if gross > 0 else 0.0

        return {
            "year":             year,
            "month":            month,
            "n_sales":          n_sales,
            "gross_eur":        round(gross, 2),
            "etsy_fees_eur":    round(fees, 2),
            "listing_fees_eur": round(listing_fees, 2),
            "design_costs_eur": round(design_costs, 2),
            "net_eur":          round(net, 2),
            "margin_pct":       margin_pct,
        }

    # ------------------------------------------------------------------
    # Goal progress
    # ------------------------------------------------------------------

    async def goal_progress(self, goal_eur: float | None = None) -> dict:
        """
        Stato avanzamento verso il goal mensile.

        Ritorna: current_net_eur, goal_eur, pct, days_elapsed,
                 days_left, daily_rate, daily_needed
        """
        summary = await self.monthly_summary()
        current_net = summary["net_eur"]

        # Legge goal da config DB, fallback su default
        _goal = goal_eur
        if _goal is None:
            try:
                db = await self._db()
                cursor = await db.execute(
                    "SELECT value FROM config WHERE key = 'finance.goal_eur'"
                )
                row = await cursor.fetchone()
                _goal = float(row["value"]) if row else GOAL_EUR_DEFAULT
            except Exception:
                _goal = GOAL_EUR_DEFAULT

        now         = datetime.now(timezone.utc)
        days_in_month = (
            datetime(now.year, now.month + 1 if now.month < 12 else 1,
                     1, tzinfo=timezone.utc)
            - datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        ).days
        days_elapsed = now.day
        days_left    = max(0, days_in_month - days_elapsed)

        daily_rate   = round(current_net / max(days_elapsed, 1), 2)
        remaining    = max(0.0, _goal - current_net)
        daily_needed = round(remaining / max(days_left, 1), 2) if days_left > 0 else 0.0
        pct          = round((current_net / _goal) * 100, 1) if _goal > 0 else 0.0

        return {
            "current_net_eur": round(current_net, 2),
            "goal_eur":        round(_goal, 2),
            "pct":             pct,
            "days_elapsed":    days_elapsed,
            "days_left":       days_left,
            "daily_rate":      daily_rate,
            "daily_needed":    daily_needed,
            "on_track":        daily_rate >= daily_needed,
        }

    # ------------------------------------------------------------------
    # Top earners
    # ------------------------------------------------------------------

    async def top_earners(self, limit: int = 5, days: int = 30) -> list[dict]:
        """
        Listing con più net revenue negli ultimi `days` giorni.
        Ritorna lista di {listing_id, niche, n_sales, gross_eur, net_eur}.
        """
        cutoff = _time.time() - days * 86400
        db     = await self._db()
        cursor = await db.execute(
            """
            SELECT
                etsy_listing_id,
                niche,
                product_type,
                COUNT(*)        AS n_sales,
                SUM(gross_eur)  AS gross,
                SUM(net_eur)    AS net
            FROM revenue_events
            WHERE sold_at >= ?
            GROUP BY etsy_listing_id
            ORDER BY net DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "listing_id":   row["etsy_listing_id"],
                "niche":        row["niche"],
                "product_type": row["product_type"],
                "n_sales":      row["n_sales"],
                "gross_eur":    round(float(row["gross"] or 0), 2),
                "net_eur":      round(float(row["net"]   or 0), 2),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Costo medio design
    # ------------------------------------------------------------------

    async def cost_per_listing_avg(self, days: int = 30) -> dict:
        """
        Media costi design (LLM + immagine) per listing pubblicato.
        Legge da production_queue (llm_cost_usd + image_cost_usd).

        Ritorna: n_listings, avg_llm_usd, avg_image_usd, avg_total_usd, avg_total_eur
        """
        cutoff = _time.time() - days * 86400
        db     = await self._db()
        cursor = await db.execute(
            """
            SELECT
                COUNT(*)                AS n,
                AVG(llm_cost_usd)       AS avg_llm,
                AVG(image_cost_usd)     AS avg_img,
                AVG(llm_cost_usd + image_cost_usd) AS avg_total
            FROM production_queue
            WHERE status = 'published'
              AND published_at >= ?
            """,
            (cutoff,),
        )
        row = await cursor.fetchone()

        avg_total_usd = float(row["avg_total"] or 0.0)
        avg_total_eur = round(avg_total_usd / EUR_USD_RATE, 4)

        return {
            "n_listings":    int(row["n"] or 0),
            "avg_llm_usd":   round(float(row["avg_llm"]   or 0.0), 4),
            "avg_image_usd": round(float(row["avg_img"]   or 0.0), 4),
            "avg_total_usd": round(avg_total_usd, 4),
            "avg_total_eur": avg_total_eur,
        }

    # ------------------------------------------------------------------
    # Break-even price (wrapper della funzione pura)
    # ------------------------------------------------------------------

    async def break_even_price_for_avg(self) -> float:
        """
        Prezzo di break-even usando il costo design medio degli ultimi 30 giorni.
        Convenienza rispetto alla funzione pura `break_even_price(design_cost_usd)`.
        """
        cost_data = await self.cost_per_listing_avg()
        avg_cost_usd = cost_data["avg_total_usd"]
        return break_even_price(avg_cost_usd)
