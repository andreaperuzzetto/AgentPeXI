"""FinanceAgent — pure calculation helpers."""
from __future__ import annotations

import json
import re

from apps.backend.agents._finance.constants import (
    USD_EUR_RATE,
    ETSY_TRANSACTION_FEE_PCT,
    ETSY_PAYMENT_FEE_PCT,
    ETSY_PAYMENT_FEE_FIXED_EUR,
    ETSY_LISTING_FEE_EUR,
)


class _CalculationsMixin:

    @staticmethod
    def _usd_to_eur(usd: float) -> float:
        return round(usd * USD_EUR_RATE, 6)

    @staticmethod
    def _calculate_etsy_fees(
        revenue_eur: float,
        num_sales: int,
        num_active_listings: int,
    ) -> dict:
        """
        Calcola fee Etsy reali:
        - transaction fee: 6.5% revenue
        - payment processing: 3% + €0.23/transazione
        - listing fee: €0.18/listing pubblicato (one-time nel periodo)

        Nota: listing_fee qui è approssimato al numero di listing attivi nel periodo.
        Il costo reale è per ogni rinnovo (ogni 4 mesi su Etsy).
        """
        transaction_fee = revenue_eur * ETSY_TRANSACTION_FEE_PCT
        payment_fee_pct = revenue_eur * ETSY_PAYMENT_FEE_PCT
        payment_fee_fixed = num_sales * ETSY_PAYMENT_FEE_FIXED_EUR
        listing_fee = num_active_listings * ETSY_LISTING_FEE_EUR

        total_fees = transaction_fee + payment_fee_pct + payment_fee_fixed + listing_fee

        return {
            "transaction_fee_eur": round(transaction_fee, 4),
            "payment_fee_pct_eur": round(payment_fee_pct, 4),
            "payment_fee_fixed_eur": round(payment_fee_fixed, 4),
            "listing_fee_eur": round(listing_fee, 4),
            "total_fees_eur": round(total_fees, 4),
            "effective_fee_pct": round(
                total_fees / revenue_eur * 100 if revenue_eur > 0 else 0.0, 2
            ),
        }

    def _calculate_finance_confidence(
        self,
        costs_eur: float,
        revenue_stats: dict,
        niche_roi: list[dict],
        model_costs: list[dict],
        trend: dict,
    ) -> tuple[float, list[str]]:
        """
        Score:
          45% — costi LLM disponibili e consistenti (no Etsy needed)
          25% — revenue data da listing sincronizzati
          20% — niche ROI calcolabile (≥2 nicchie con dati)
          10% — trend data (entrambi i periodi con dati)

        Target: 45+25+20+10 = 88% pieno. Con soli costi: 45% (PARTIAL).
        """
        missing: list[str] = []
        score = 0.0

        # 45% — costi LLM (sempre disponibili dopo i primi task)
        if costs_eur > 0:
            score += 0.45
        else:
            score += 0.15
            missing.append("Nessun costo LLM registrato nel periodo — DB vuoto o periodo troppo corto")

        if model_costs:
            pass  # bonus già incluso nel 45%

        # 25% — revenue e listing synced
        total_rev = revenue_stats.get("total_revenue_eur", 0.0)
        active = revenue_stats.get("active_count", 0)
        if total_rev > 0 and active > 0:
            score += 0.25
        elif active > 0:
            # Listing attivi ma revenue 0 — Etsy non ancora approvata
            score += 0.10
            missing.append(f"{active} listing attivi ma revenue €0 — sync Etsy non eseguito")
        else:
            missing.append("Nessun listing attivo nel DB locale — in attesa di approvazione Etsy")

        # 20% — niche ROI (almeno 2 nicchie con dati)
        niches_with_data = [n for n in niche_roi if n.get("listing_count", 0) > 0]
        if len(niches_with_data) >= 2:
            score += 0.20
        elif len(niches_with_data) == 1:
            score += 0.12
            missing.append("Solo 1 nicchia con dati — ROI comparison limitata")
        else:
            missing.append("Nessuna nicchia con dati listing — aspettare prime pubblicazioni")

        # 10% — trend data (entrambi 7d e 30d con almeno 1 datapoint)
        has_7d = trend.get("revenue_7d", 0.0) > 0 or trend.get("cost_7d", 0.0) > 0
        has_30d = trend.get("revenue_30d", 0.0) > 0 or trend.get("cost_30d", 0.0) > 0
        if has_7d and has_30d:
            score += 0.10
        elif has_30d:
            score += 0.05
            missing.append("Dati trend solo per periodo 30d — confronto 7d non disponibile")
        else:
            missing.append("Dati trend non disponibili — in attesa di operazioni nel periodo")

        return round(min(score, 1.0), 2), missing

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """Estrae e parsa il primo blocco JSON trovato nella risposta LLM."""
        text = text.strip()
        # Prova parsing diretto
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Prova a estrarre da blocco ```json ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # Prova a estrarre il primo { ... } dalla risposta
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
