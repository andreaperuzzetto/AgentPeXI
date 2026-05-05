"""FinanceAgent — Etsy fee structure and budget constants."""
from __future__ import annotations

from apps.backend.core.config import settings

USD_EUR_RATE: float = settings.USD_EUR_RATE  # centralizzato in config.py / .env
ETSY_TRANSACTION_FEE_PCT: float = 0.065    # 6.5% su prezzo di vendita
ETSY_PAYMENT_FEE_PCT: float = 0.030        # 3% Etsy Payments
ETSY_PAYMENT_FEE_FIXED_EUR: float = 0.23  # ~€0.25 per transazione (fisso)
ETSY_LISTING_FEE_EUR: float = 0.18        # ~€0.20 per listing pubblicato (one-time)

# Budget alert: legge da settings, fallback 70 €
BUDGET_ALERT_EUR: float = getattr(settings, "COST_ALERT_THRESHOLD_EUR", 70.0)
