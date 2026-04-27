"""MarketDataAgent — raccolta dati di mercato strutturata (Tier 1 + Tier 2).

Tier 1: Etsy listing search + autocomplete suggestions.
Tier 2: Google Trends — segnale di domanda esterna, blendato nello score.

Entry point consigliato: collect_full() → esegue Tier 1 + Tier 2.
Se Trends non disponibile (timeout, rate limit), il sistema usa solo Tier 1.

Responsabilità:
- Interroga Etsy API pubblica (solo x-api-key, nessun OAuth)
- Raccoglie Google Trends via pytrends (sincrono → thread executor)
- Calcola entry_score blendando domanda Etsy + segnale Trends
- Persiste i risultati in market_signals (cache 24h, append-only)
- In mock_mode: restituisce dati realistici simulati, zero chiamate HTTP/pytrends

Nessun LLM. Solo dati strutturati.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.market_data")

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
ETSY_AUTOCOMPLETE_URL = (
    "https://www.etsy.com/api/v3/ajax/bespoke/public/fetch/listings/search/suggestions"
)

# Stagionalità per niche keyword — fonte di verità per tutto il sistema.
# Boost moltiplicativo applicato a entry_score: 1.0 = neutro, 1.3 = picco stagionale.
# Formato: {keyword_fragment: {month_1_based: boost, ...}}
SEASONAL_MAP: dict[str, dict[int, float]] = {
    "wedding":      {3: 1.2, 4: 1.3, 5: 1.3, 6: 1.2, 9: 1.1, 10: 1.1},
    "christmas":    {10: 1.1, 11: 1.3, 12: 1.3},
    "valentine":    {12: 1.1, 1: 1.3, 2: 1.3},
    "halloween":    {8: 1.1, 9: 1.2, 10: 1.3},
    "thanksgiving": {10: 1.2, 11: 1.3},
    "easter":       {2: 1.1, 3: 1.3, 4: 1.2},
    "mother":       {4: 1.2, 5: 1.3},
    "father":       {5: 1.1, 6: 1.3},
    "graduation":   {4: 1.1, 5: 1.3, 6: 1.2},
    "birthday":     {},   # sempre stabile — nessun boost
    "baby":         {},   # stabile
    "planner":      {12: 1.1, 1: 1.3},   # inizio anno
    "resume":       {1: 1.2, 8: 1.1, 9: 1.2},
    "back to school": {7: 1.2, 8: 1.3},
    "new year":     {12: 1.2, 1: 1.2},
}

# Timeout HTTP per scraping pubblico
_HTTP_TIMEOUT = 15.0

# Soglie per normalizzazione entry_score
_MAX_RESULT_COUNT = 50_000   # oltre → saturazione massima
_MAX_AVG_REVIEWS  = 200      # oltre → domanda massima

# Peso Google Trends nel blending demand Tier 2
# demand = (1 - _TRENDS_WEIGHT) * etsy_demand + _TRENDS_WEIGHT * trends_demand
# Se Trends non disponibile il peso ricade tutto su Etsy.
_TRENDS_WEIGHT = 0.35


# ---------------------------------------------------------------------------
# Dataclass risultato
# ---------------------------------------------------------------------------

@dataclass
class MarketSignals:
    """Segnali di mercato raccolti per una niche."""

    niche: str
    product_type: str | None = None

    # Tier 1 — Etsy
    etsy_result_count: int   = 0
    avg_reviews: float       = 0.0
    avg_price_eur: float     = 0.0
    autocomplete_hits: int   = 0     # quante suggestions includono la keyword

    # Tier 2 — Google Trends (popolato in step 1.3)
    google_trend_score: float  = 0.0
    erank_search_volume: int   = 0

    # Scoring
    entry_score: float   = 0.0
    seasonal_boost: float = 1.0

    # Meta
    tier: int           = 1
    collected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche":               self.niche,
            "product_type":        self.product_type,
            "etsy_result_count":   self.etsy_result_count,
            "avg_reviews":         self.avg_reviews,
            "avg_price_eur":       self.avg_price_eur,
            "autocomplete_hits":   self.autocomplete_hits,
            "google_trend_score":  self.google_trend_score,
            "erank_search_volume": self.erank_search_volume,
            "entry_score":         self.entry_score,
            "seasonal_boost":      self.seasonal_boost,
            "tier":                self.tier,
            "collected_at":        self.collected_at,
        }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class MarketDataAgent:
    """
    Raccoglie dati di mercato strutturati da Etsy + Google Trends.

    Uso tipico (pipeline completa):
        agent = MarketDataAgent(memory=memory, mock_mode=True)
        signals = await agent.collect_full("boho wedding printables")
        print(signals.entry_score, signals.tier)  # score blendato, tier=2

    Uso Tier 1 only (più veloce, nessun pytrends):
        signals = await agent.collect_tier1("boho wedding printables")
    """

    def __init__(
        self,
        memory: MemoryManager,
        mock_mode: bool = False,
    ) -> None:
        self._memory   = memory
        self._mock     = mock_mode
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def collect_tier1(
        self,
        niche: str,
        product_type: str | None = None,
        force_refresh: bool = False,
    ) -> MarketSignals:
        """
        Raccoglie dati Tier 1 per la niche.
        Usa cache DB (24h) a meno che force_refresh=True.
        """
        if not force_refresh:
            cached = await self.get_cached_signals(niche, max_age_hours=24)
            if cached:
                logger.debug("market_data: cache HIT per '%s'", niche)
                return self._dict_to_signals(cached)

        logger.info("market_data: raccolta Tier 1 per '%s' (mock=%s)", niche, self._mock)

        if self._mock:
            signals = self._mock_tier1(niche, product_type)
        else:
            signals = await self._real_tier1(niche, product_type)

        # Calcola staging boost e entry_score
        signals.seasonal_boost = self._get_seasonal_boost(niche)
        signals.entry_score    = self._compute_entry_score(signals)

        # Persiste in DB
        await self._save_signals(signals)

        return signals

    async def get_cached_signals(
        self,
        niche: str,
        max_age_hours: int = 24,
    ) -> dict[str, Any] | None:
        """Ritorna i segnali più recenti dal DB se non più vecchi di max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        db = await self._memory.get_db()
        cursor = await db.execute(
            """
            SELECT * FROM market_signals
            WHERE niche = ? AND collected_at >= ?
            ORDER BY collected_at DESC
            LIMIT 1
            """,
            (niche, cutoff),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_top_candidates(
        self,
        limit: int = 10,
        min_score: float = 0.2,
    ) -> list[dict[str, Any]]:
        """
        Ritorna le niche con entry_score più alto dal DB.
        Usato da AutopilotLoop per selezionare la prossima niche.
        """
        db = await self._memory.get_db()
        cursor = await db.execute(
            """
            SELECT niche, product_type,
                   MAX(entry_score) AS entry_score,
                   MAX(collected_at) AS last_collected
            FROM market_signals
            WHERE entry_score >= ?
            GROUP BY niche
            ORDER BY entry_score DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows] if rows else []

    async def collect_full(
        self,
        niche: str,
        product_type: str | None = None,
        force_refresh: bool = False,
    ) -> MarketSignals:
        """
        Pipeline completa: Tier 1 (Etsy) → Tier 2 (Google Trends).
        Entry point consigliato per il scoring pipeline.

        Se Tier 2 fallisce (timeout, rate limit, pytrends non installato)
        ritorna comunque i segnali Tier 1 — non blocca mai il flusso.
        """
        signals = await self.collect_tier1(niche, product_type, force_refresh)
        signals = await self.collect_tier2(signals)
        return signals

    async def collect_tier2(
        self,
        signals: MarketSignals,
    ) -> MarketSignals:
        """
        Arricchisce un MarketSignals esistente con Google Trends.
        Ricalcola entry_score con il blending Tier 1+2.
        Persiste una nuova riga in market_signals con tier=2.

        Non modifica l'oggetto in-place: ritorna una copia aggiornata.
        """
        logger.info(
            "market_data: raccolta Tier 2 per '%s' (mock=%s)", signals.niche, self._mock
        )

        if self._mock:
            trend_data = self._mock_tier2(signals.niche)
        else:
            trend_data = await self._real_tier2(signals.niche)

        # Copia e arricchisce
        import dataclasses
        enriched = dataclasses.replace(
            signals,
            google_trend_score = trend_data["score"],
            tier               = 2,
        )
        enriched.seasonal_boost = self._get_seasonal_boost(enriched.niche)
        enriched.entry_score    = self._compute_entry_score(enriched)

        await self._save_signals(enriched)
        return enriched

    # ------------------------------------------------------------------
    # Tier 2 reale
    # ------------------------------------------------------------------

    async def _real_tier2(self, niche: str) -> dict[str, float]:
        """
        Chiama Google Trends via pytrends (wrapper sincrono in thread executor).
        Ritorna {"score": float 0-100}.

        Fallisce silenziosamente: se pytrends non è installato o la chiamata
        va in timeout, ritorna score=0 e logga un warning.
        """
        try:
            from apps.backend.tools.trends import get_google_trends
            result = await get_google_trends(niche)
            score  = float(result.get("current_value") or result.get("avg_value") or 0)
            logger.debug(
                "market_data: Trends '%s' → score=%.1f direction=%s",
                niche, score, result.get("trend_direction", "?")
            )
            return {"score": round(score, 1)}
        except Exception as e:
            logger.warning("market_data: Tier 2 fallito per '%s': %s", niche, e)
            return {"score": 0.0}

    # ------------------------------------------------------------------
    # Mock Tier 2
    # ------------------------------------------------------------------

    def _mock_tier2(self, niche: str) -> dict[str, float]:
        """
        Genera un Google Trends score simulato.
        Stesso seed deterministico di _mock_tier1 per coerenza.
        """
        seed  = sum(ord(c) for c in niche)
        rng   = random.Random(seed + 1)   # +1 per diversificare dal Tier 1
        # Score 0-100: distribuito realisticamente (media ~45, coda alta)
        score = round(rng.triangular(10, 100, 45), 1)
        return {"score": score}

    # ------------------------------------------------------------------
    # Tier 1 reale
    # ------------------------------------------------------------------

    async def _real_tier1(
        self,
        niche: str,
        product_type: str | None,
    ) -> MarketSignals:
        """Chiama Etsy API pubblica e autocomplete in parallelo."""
        search_task      = asyncio.create_task(self._search_etsy_listings(niche))
        autocomplete_task = asyncio.create_task(self._get_autocomplete(niche))

        search_data, ac_suggestions = await asyncio.gather(
            search_task, autocomplete_task, return_exceptions=True
        )

        # Se le chiamate falliscono, usa defaults sicuri
        if isinstance(search_data, Exception):
            logger.warning("market_data: Etsy search fallita per '%s': %s", niche, search_data)
            search_data = {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        if isinstance(ac_suggestions, Exception):
            logger.warning("market_data: autocomplete fallita per '%s': %s", niche, ac_suggestions)
            ac_suggestions = []

        # Conta quante suggestions contengono la keyword principale
        kw_root = niche.lower().split()[0] if niche else ""
        ac_hits = sum(1 for s in ac_suggestions if kw_root in s.lower())

        return MarketSignals(
            niche              = niche,
            product_type       = product_type,
            etsy_result_count  = search_data.get("count", 0),
            avg_reviews        = search_data.get("avg_reviews", 0.0),
            avg_price_eur      = search_data.get("avg_price_eur", 0.0),
            autocomplete_hits  = ac_hits,
            tier               = 1,
        )

    async def _search_etsy_listings(self, keyword: str) -> dict[str, Any]:
        """
        Cerca listing attivi su Etsy per keyword.
        Endpoint: GET /v3/application/listings/active
        Autenticazione: solo x-api-key header (no OAuth).

        Estrae: count totale, avg price, avg num_favorers (proxy reviews).
        """
        api_key = settings.ETSY_API_KEY
        if not api_key:
            logger.warning("market_data: ETSY_API_KEY non configurato")
            return {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        client = await self._get_client()

        params = {
            "keywords":  keyword,
            "limit":     100,
            "includes":  "Images",   # non necessario ma standard
            "fields":    "listing_id,price,num_favorers,title,quantity",
            "sort_on":   "score",
            "sort_order": "desc",
        }

        try:
            resp = await client.get(
                f"{ETSY_API_BASE}/listings/active",
                params=params,
                headers={"x-api-key": api_key},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("market_data: Etsy API %d per '%s'", e.response.status_code, keyword)
            return {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}
        except Exception as e:
            logger.error("market_data: Etsy search exception '%s': %s", keyword, e)
            return {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        results = data.get("results", [])
        count   = data.get("count", len(results))

        if not results:
            return {"count": count, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        # Calcola medie sui top 100 risultati
        prices    = []
        favorers  = []

        for listing in results:
            # Prezzo: Etsy restituisce {amount, divisor, currency_code}
            price_obj = listing.get("price", {})
            if price_obj:
                try:
                    price_eur = price_obj["amount"] / price_obj["divisor"]
                    # Conversione approssimativa se valuta non EUR
                    if price_obj.get("currency_code", "EUR") == "USD":
                        price_eur *= settings.USD_EUR_RATE
                    prices.append(price_eur)
                except (KeyError, ZeroDivisionError, TypeError):
                    pass

            # num_favorers = proxy per domanda/reviews su listing
            fav = listing.get("num_favorers", 0)
            if isinstance(fav, int):
                favorers.append(fav)

        avg_price  = round(sum(prices) / len(prices), 2) if prices else 0.0
        avg_favs   = round(sum(favorers) / len(favorers), 1) if favorers else 0.0

        return {
            "count":        count,
            "avg_reviews":  avg_favs,    # num_favorers è il proxy migliore per reviews
            "avg_price_eur": avg_price,
        }

    async def _get_autocomplete(self, keyword: str) -> list[str]:
        """
        Recupera suggerimenti autocomplete dalla ricerca pubblica Etsy.
        Endpoint non ufficiale ma stabile: restituisce fino a 10 suggestions.
        Fallisce silenziosamente (nessun account necessario).
        """
        client = await self._get_client()

        try:
            resp = await client.get(
                ETSY_AUTOCOMPLETE_URL,
                params={"query": keyword, "limit": 10, "locale": "en-US"},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                    "Referer": "https://www.etsy.com/",
                },
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            # La risposta ha struttura variabile — gestisco entrambe le forme note
            if isinstance(data, list):
                suggestions = [str(s) for s in data]
            elif isinstance(data, dict):
                suggestions = data.get("suggestions", data.get("results", []))
                suggestions = [
                    s.get("value", s) if isinstance(s, dict) else str(s)
                    for s in suggestions
                ]
            else:
                suggestions = []

            logger.debug(
                "market_data: autocomplete '%s' → %d suggestions", keyword, len(suggestions)
            )
            return suggestions[:10]

        except Exception as e:
            logger.debug("market_data: autocomplete silently failed for '%s': %s", keyword, e)
            return []

    # ------------------------------------------------------------------
    # Mock Tier 1
    # ------------------------------------------------------------------

    def _mock_tier1(
        self,
        niche: str,
        product_type: str | None,
    ) -> MarketSignals:
        """
        Genera dati Tier 1 simulati realistici per test/sviluppo.
        Usa un seed deterministico sulla niche per risultati stabili.
        """
        seed = sum(ord(c) for c in niche)
        rng  = random.Random(seed)

        # Simula tre tipi di niche: satura, media, nicchia vuota
        scenario = seed % 3
        if scenario == 0:   # niche satura
            count      = rng.randint(40_000, 80_000)
            avg_favs   = rng.uniform(80, 200)
            avg_price  = rng.uniform(3.5, 8.0)
            ac_hits    = rng.randint(6, 10)
        elif scenario == 1: # niche media — sweet spot
            count      = rng.randint(8_000, 30_000)
            avg_favs   = rng.uniform(30, 100)
            avg_price  = rng.uniform(5.0, 15.0)
            ac_hits    = rng.randint(3, 7)
        else:               # niche vuota / emergente
            count      = rng.randint(500, 5_000)
            avg_favs   = rng.uniform(2, 25)
            avg_price  = rng.uniform(4.0, 12.0)
            ac_hits    = rng.randint(0, 3)

        return MarketSignals(
            niche              = niche,
            product_type       = product_type,
            etsy_result_count  = count,
            avg_reviews        = round(avg_favs, 1),
            avg_price_eur      = round(avg_price, 2),
            autocomplete_hits  = ac_hits,
            tier               = 1,
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _compute_entry_score(self, signals: MarketSignals) -> float:
        """
        Entry score — Tier 1 o Tier 1+2 a seconda dei dati disponibili.

        Formula:
            demand_proxy    = etsy_demand                         (Tier 1)
                            | blend(etsy_demand, trends_demand)   (Tier 2)
            competition     = etsy_result_count normalizzato
            entry_score     = (demand_proxy / competition) * seasonal_boost * ac_boost

        Blending Tier 2:
            demand = (1 - _TRENDS_WEIGHT) * etsy_demand + _TRENDS_WEIGHT * trends_demand
            dove trends_demand = google_trend_score / 100

        Score finale in [0.05, 1.0].
        Cold-start (nessun dato) → 0.4 flat.
        """
        if signals.etsy_result_count == 0 and signals.avg_reviews == 0.0:
            return 0.4   # cold-start safe

        # --- demand proxy ---
        etsy_demand = min(signals.avg_reviews / _MAX_AVG_REVIEWS, 1.0)

        if signals.tier >= 2 and signals.google_trend_score > 0:
            trends_demand = signals.google_trend_score / 100.0
            demand = (
                (1 - _TRENDS_WEIGHT) * etsy_demand
                + _TRENDS_WEIGHT * trends_demand
            )
        else:
            demand = etsy_demand

        # --- competition density ---
        competition = min(signals.etsy_result_count / _MAX_RESULT_COUNT, 1.0)
        competition = max(competition, 0.05)   # evita divisione per zero

        # --- formula base ---
        raw = demand / competition

        # autocomplete boost: ogni hit +3%, max +20%
        ac_boost = 1.0 + min(signals.autocomplete_hits * 0.03, 0.20)

        score = raw * signals.seasonal_boost * ac_boost

        return round(max(0.05, min(score, 1.0)), 3)

    def _get_seasonal_boost(self, niche: str) -> float:
        """
        Ritorna il boost stagionale per la niche basato sul mese corrente.
        Default 1.0 se nessuna chiave corrisponde.
        """
        import datetime as _dt
        current_month = _dt.datetime.now().month
        niche_lower   = niche.lower()

        for keyword, monthly_boosts in SEASONAL_MAP.items():
            if keyword in niche_lower:
                return monthly_boosts.get(current_month, 1.0)
        return 1.0

    # ------------------------------------------------------------------
    # Persistenza
    # ------------------------------------------------------------------

    async def _save_signals(self, signals: MarketSignals) -> int:
        """Salva i segnali in market_signals. Ritorna l'id della riga inserita."""
        db = await self._memory.get_db()
        cursor = await db.execute(
            """
            INSERT INTO market_signals (
                niche, product_type,
                etsy_result_count, avg_reviews, avg_price_eur, autocomplete_hits,
                google_trend_score, erank_search_volume,
                entry_score, seasonal_boost, tier, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signals.niche,
                signals.product_type,
                signals.etsy_result_count,
                signals.avg_reviews,
                signals.avg_price_eur,
                signals.autocomplete_hits,
                signals.google_trend_score,
                signals.erank_search_volume,
                signals.entry_score,
                signals.seasonal_boost,
                signals.tier,
                signals.collected_at,
            ),
        )
        await db.commit()
        return cursor.lastrowid

    # ------------------------------------------------------------------
    # HTTP client (lazy)
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=_HTTP_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        """Chiude il client HTTP. Chiamare allo shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _dict_to_signals(row: dict[str, Any]) -> MarketSignals:
        return MarketSignals(
            niche              = row["niche"],
            product_type       = row.get("product_type"),
            etsy_result_count  = row.get("etsy_result_count", 0),
            avg_reviews        = row.get("avg_reviews", 0.0),
            avg_price_eur      = row.get("avg_price_eur", 0.0),
            autocomplete_hits  = row.get("autocomplete_hits", 0),
            google_trend_score = row.get("google_trend_score", 0.0),
            erank_search_volume= row.get("erank_search_volume", 0),
            entry_score        = row.get("entry_score", 0.0),
            seasonal_boost     = row.get("seasonal_boost", 1.0),
            tier               = row.get("tier", 1),
            collected_at       = row.get("collected_at", time.time()),
        )
