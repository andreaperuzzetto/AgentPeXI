"""Tests for EntryPointScoring — ScoredCandidate, _quality_gap_factor, rank_candidates."""
from __future__ import annotations

import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock

from apps.backend.core.entry_point_scoring import EntryPointScoring, ScoredCandidate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scorer():
    return EntryPointScoring(memory=MagicMock(), market_data=MagicMock())


class _MockSignals:
    def __init__(self, avg_price_eur: float, etsy_result_count: int = 0):
        self.avg_price_eur = avg_price_eur
        self.etsy_result_count = etsy_result_count
        self.entry_score = 0.6


# ---------------------------------------------------------------------------
# ScoredCandidate.to_dict()
# ---------------------------------------------------------------------------

def test_scored_candidate_to_dict_basic():
    c = ScoredCandidate(niche="planner", product_type="printable_pdf")
    d = c.to_dict()
    assert d["niche"] == "planner"
    assert d["product_type"] == "printable_pdf"
    assert "final_score" in d
    assert "eligible" in d


def test_scored_candidate_to_dict_ineligible():
    c = ScoredCandidate(
        niche="planner",
        product_type=None,
        eligible=False,
        exclusion_reason="cooldown (7d)",
    )
    d = c.to_dict()
    assert d["eligible"] is False
    assert d["exclusion_reason"] == "cooldown (7d)"


# ---------------------------------------------------------------------------
# rank_candidates — empty list
# ---------------------------------------------------------------------------

async def test_rank_candidates_empty_returns_empty(scorer):
    result = await scorer.rank_candidates([])
    assert result == []


# ---------------------------------------------------------------------------
# rank_candidates — exception propagation (lines 134-137)
# ---------------------------------------------------------------------------

async def test_rank_candidates_handles_score_single_exception(scorer):
    """If score_single raises, rank_candidates uses fallback score 0.4."""
    exc = RuntimeError("market data unavailable")
    scorer.score_single = AsyncMock(side_effect=exc)
    result = await scorer.rank_candidates([{"niche": "test", "product_type": "pdf"}])
    assert len(result) == 1
    assert result[0].base_score == 0.4
    assert result[0].final_score == 0.4
    assert result[0].eligible is True


# ---------------------------------------------------------------------------
# rank_candidates — ineligible candidates logged (line 152)
# ---------------------------------------------------------------------------

async def test_rank_candidates_ineligible_excluded(scorer):
    ineligible = ScoredCandidate(
        niche="bad_niche",
        product_type=None,
        eligible=False,
        exclusion_reason="cooldown",
        final_score=0.9,
    )
    scorer.score_single = AsyncMock(return_value=ineligible)
    result = await scorer.rank_candidates([{"niche": "bad_niche"}])
    assert result == []


# ---------------------------------------------------------------------------
# _quality_gap_factor — all branches
# ---------------------------------------------------------------------------

def test_qgf_very_high_price_returns_1_15(scorer):
    assert scorer._quality_gap_factor(_MockSignals(avg_price_eur=12.0)) == 1.15


def test_qgf_premium_low_saturation_returns_1_2(scorer):
    # price >= 8.0 and competition_norm < 0.6
    assert scorer._quality_gap_factor(_MockSignals(avg_price_eur=9.0, etsy_result_count=1000)) == 1.2


def test_qgf_very_low_price_returns_0_8(scorer):
    assert scorer._quality_gap_factor(_MockSignals(avg_price_eur=2.5)) == 0.8


def test_qgf_low_price_high_saturation_returns_0_85(scorer):
    # price < 4.0 and competition_norm > 0.6
    assert scorer._quality_gap_factor(_MockSignals(avg_price_eur=3.5, etsy_result_count=40_000)) == 0.85


def test_qgf_neutral_returns_1_0(scorer):
    # price in [4, 8), low saturation
    assert scorer._quality_gap_factor(_MockSignals(avg_price_eur=6.0, etsy_result_count=5000)) == 1.0


def test_qgf_none_price_returns_0_8(scorer):
    # avg_price_eur = None → coerced to 0.0 → < 3.0 → 0.8
    signals = _MockSignals(avg_price_eur=0.0)
    signals.avg_price_eur = None
    assert scorer._quality_gap_factor(signals) == 0.8


# ---------------------------------------------------------------------------
# _performance_multiplier — with real in-memory SQLite
# ---------------------------------------------------------------------------

_NI_SCHEMA = """
CREATE TABLE IF NOT EXISTS niche_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT NOT NULL,
    product_type TEXT,
    performance_score REAL NOT NULL DEFAULT 0.5,
    confidence_level TEXT NOT NULL DEFAULT 'low',
    last_updated_at REAL NOT NULL DEFAULT 0
);
"""


@pytest.fixture
async def ni_db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_NI_SCHEMA)
        await conn.commit()
        yield conn


async def _make_scorer_with_db(db):
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=db)
    return EntryPointScoring(memory=memory, market_data=MagicMock())


async def test_performance_multiplier_no_data_returns_1(ni_db):
    scorer = await _make_scorer_with_db(ni_db)
    result = await scorer._performance_multiplier("planner", "printable_pdf")
    assert result == 1.0


async def test_performance_multiplier_low_confidence_returns_1(ni_db):
    await ni_db.execute(
        "INSERT INTO niche_intelligence (niche, product_type, performance_score, confidence_level, last_updated_at) VALUES (?,?,?,?,?)",
        ("planner", "printable_pdf", 0.8, "low", 0),
    )
    await ni_db.commit()
    scorer = await _make_scorer_with_db(ni_db)
    result = await scorer._performance_multiplier("planner", "printable_pdf")
    assert result == 1.0


async def test_performance_multiplier_high_confidence(ni_db):
    await ni_db.execute(
        "INSERT INTO niche_intelligence (niche, product_type, performance_score, confidence_level, last_updated_at) VALUES (?,?,?,?,?)",
        ("planner", "printable_pdf", 0.8, "high", 0),
    )
    await ni_db.commit()
    scorer = await _make_scorer_with_db(ni_db)
    result = await scorer._performance_multiplier("planner", "printable_pdf")
    assert result == pytest.approx(1.3, abs=0.01)  # 0.5 + 0.8 = 1.3


async def test_performance_multiplier_medium_confidence(ni_db):
    await ni_db.execute(
        "INSERT INTO niche_intelligence (niche, product_type, performance_score, confidence_level, last_updated_at) VALUES (?,?,?,?,?)",
        ("planner", None, 0.5, "medium", 0),
    )
    await ni_db.commit()
    scorer = await _make_scorer_with_db(ni_db)
    result = await scorer._performance_multiplier("planner", None)
    assert result == pytest.approx(1.0, abs=0.01)


async def test_performance_multiplier_db_exception_returns_1(scorer):
    scorer._memory.get_db = AsyncMock(side_effect=RuntimeError("DB error"))
    result = await scorer._performance_multiplier("planner", None)
    assert result == 1.0


# ---------------------------------------------------------------------------
# PA-3 — ScoredCandidate.metadata + rank_candidates embeds original candidate
# ---------------------------------------------------------------------------

def test_scored_candidate_metadata_defaults_to_empty():
    """ScoredCandidate must have a metadata field that defaults to {}."""
    sc = ScoredCandidate(niche="planner", product_type="printable_pdf")
    assert hasattr(sc, "metadata")
    assert sc.metadata == {}


def test_scored_candidate_to_dict_includes_metadata():
    sc = ScoredCandidate(
        niche="planner",
        product_type="printable_pdf",
        metadata={"source": "discovery", "section": "planners"},
    )
    d = sc.to_dict()
    assert d["metadata"] == {"source": "discovery", "section": "planners"}


async def test_rank_candidates_embeds_original_metadata(scorer):
    """rank_candidates must embed the original candidate dict into sc.metadata."""
    original = {"niche": "wedding planner", "product_type": "printable_pdf", "source": "tavily"}
    scored_result = ScoredCandidate(
        niche="wedding planner",
        product_type="printable_pdf",
        base_score=0.7,
        final_score=0.7,
        eligible=True,
    )
    scorer.score_single = AsyncMock(return_value=scored_result)
    result = await scorer.rank_candidates([original])
    assert len(result) == 1
    assert result[0].metadata == original


async def test_rank_candidates_no_desync_when_candidate_dropped(scorer):
    """
    If candidate A is ineligible (dropped) and candidate B is eligible,
    B.metadata must be B's original dict — NOT A's dict.
    This is the core PA-3 bug: zip() caused B to pick up A's metadata.
    """
    candidate_a = {"niche": "niche_a", "product_type": "printable_pdf", "source": "src_a"}
    candidate_b = {"niche": "niche_b", "product_type": "printable_pdf", "source": "src_b"}

    ineligible_a = ScoredCandidate(niche="niche_a", product_type="printable_pdf", eligible=False, exclusion_reason="cooldown", final_score=0.0)
    eligible_b   = ScoredCandidate(niche="niche_b", product_type="printable_pdf", eligible=True, final_score=0.8)

    async def _mock_score(niche, product_type=None):
        if niche == "niche_a":
            return ineligible_a
        return eligible_b

    scorer.score_single = _mock_score
    result = await scorer.rank_candidates([candidate_a, candidate_b])
    assert len(result) == 1
    # niche_b must carry niche_b's original dict, not niche_a's
    assert result[0].niche == "niche_b"
    assert result[0].metadata["source"] == "src_b"


async def test_rank_candidates_fallback_embeds_metadata(scorer):
    """If score_single raises, the fallback ScoredCandidate must still carry metadata."""
    original = {"niche": "test_niche", "product_type": "pdf", "source": "origin"}
    scorer.score_single = AsyncMock(side_effect=RuntimeError("boom"))
    result = await scorer.rank_candidates([original])
    assert len(result) == 1
    assert result[0].metadata == original
