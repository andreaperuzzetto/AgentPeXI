"""MarketDataAgent — thin assembler.

Implementation split across apps/backend/agents/_market_data/:
  _models.py          — MarketSignals dataclass
  _mock_mixin.py      — _MockMixin  (_mock_tier1, _mock_tier2)
  _search_mixin.py    — _SearchMixin  (_search_etsy_listings, _get_autocomplete)
  _scoring_mixin.py   — _ScoringMixin  (_compute_entry_score, _get_seasonal_boost)
  _storage_mixin.py   — _StorageMixin  (_save_signals, _get_client, close,
                                        _dict_to_signals, get_cached_signals,
                                        get_top_candidates)
  _collection_mixin.py — _CollectionMixin  (collect_tier1, collect_tier2,
                                            collect_full, _real_tier1, _real_tier2)
"""
from __future__ import annotations

import httpx

from apps.backend.core.memory import MemoryManager
from apps.backend.agents._market_data import (
    MarketSignals,
    _StorageMixin,
    _ScoringMixin,
    _SearchMixin,
    _CollectionMixin,
    _CompetitiveMixin,
    _StyleGuideMixin,
    _MockMixin,
)

__all__ = ["MarketDataAgent", "MarketSignals"]


class MarketDataAgent(
    _StorageMixin,
    _ScoringMixin,
    _SearchMixin,
    _CollectionMixin,
    _CompetitiveMixin,
    _StyleGuideMixin,
    _MockMixin,
    object,
):
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
