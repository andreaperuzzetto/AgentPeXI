"""Tests for LearningLoop pure methods and DB-based read methods."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from apps.backend.core.learning_loop import LearningLoop

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS niche_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT NOT NULL,
    product_type TEXT,
    total_listings INTEGER DEFAULT 0,
    total_orders INTEGER DEFAULT 0,
    total_revenue_eur REAL DEFAULT 0.0,
    avg_ctr REAL DEFAULT 0.0,
    avg_conversion_rate REAL DEFAULT 0.0,
    performance_score REAL NOT NULL DEFAULT 0.5,
    confidence_level TEXT NOT NULL DEFAULT 'low',
    last_updated_at REAL NOT NULL DEFAULT 0,
    UNIQUE(niche, product_type)
);

CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT,
    product_type TEXT,
    status TEXT DEFAULT 'planned',
    published_at REAL
);

CREATE TABLE IF NOT EXISTS listing_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT NOT NULL,
    niche TEXT,
    product_type TEXT,
    ctr REAL DEFAULT 0.0,
    conversion_rate REAL DEFAULT 0.0,
    favorite_rate REAL DEFAULT 0.0,
    orders INTEGER DEFAULT 0,
    revenue_eur REAL DEFAULT 0.0,
    snapshot_at REAL DEFAULT (unixepoch())
);
"""


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def loop(db):
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=db)
    return LearningLoop(memory=memory)


# ---------------------------------------------------------------------------
# Pure instance methods
# ---------------------------------------------------------------------------

def test_calculate_performance_score_zero_listings():
    loop = LearningLoop(memory=MagicMock())
    assert loop._calculate_performance_score(0, 0.0, 0.0, 0.0) == 0.5


def test_calculate_performance_score_perfect():
    loop = LearningLoop(memory=MagicMock())
    # ctr=3%, cr=5%, rev=€20/listing × 5 listings → full weight, score ~1.0
    score = loop._calculate_performance_score(5, 0.03, 0.05, 100.0)
    assert 0.8 <= score <= 1.0


def test_calculate_performance_score_low_values():
    loop = LearningLoop(memory=MagicMock())
    score = loop._calculate_performance_score(10, 0.0, 0.0, 0.0)
    assert score < 0.5


def test_calculate_performance_score_few_listings_blends_toward_half():
    loop = LearningLoop(memory=MagicMock())
    # 1 listing → weight=0.2, result is closer to 0.5 than the raw score
    score_1 = loop._calculate_performance_score(1, 0.03, 0.05, 20.0)
    score_5 = loop._calculate_performance_score(5, 0.03, 0.05, 100.0)
    assert score_1 < score_5  # fewer listings → more blended → lower (since raw > 0.5)


def test_calculate_performance_score_returns_float():
    loop = LearningLoop(memory=MagicMock())
    score = loop._calculate_performance_score(3, 0.02, 0.04, 50.0)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_confidence_level_high():
    loop = LearningLoop(memory=MagicMock())
    assert loop._confidence_level(5) == "high"
    assert loop._confidence_level(10) == "high"


def test_confidence_level_medium():
    loop = LearningLoop(memory=MagicMock())
    assert loop._confidence_level(2) == "medium"
    assert loop._confidence_level(4) == "medium"


def test_confidence_level_low():
    loop = LearningLoop(memory=MagicMock())
    assert loop._confidence_level(0) == "low"
    assert loop._confidence_level(1) == "low"


# ---------------------------------------------------------------------------
# get_top_niches
# ---------------------------------------------------------------------------

async def test_get_top_niches_empty(loop):
    result = await loop.get_top_niches()
    assert result == []


async def test_get_top_niches_returns_sorted(loop, db):
    await db.executemany(
        "INSERT INTO niche_intelligence (niche, product_type, performance_score, confidence_level, last_updated_at) VALUES (?,?,?,?,?)",
        [
            ("planner", "printable_pdf", 0.9, "high", time.time()),
            ("journal", "printable_pdf", 0.7, "medium", time.time()),
            ("tracker", "printable_pdf", 0.5, "low", time.time()),
        ],
    )
    await db.commit()
    result = await loop.get_top_niches(limit=2)
    assert len(result) == 2
    assert result[0] == "planner"  # highest score first


# ---------------------------------------------------------------------------
# get_intel
# ---------------------------------------------------------------------------

async def test_get_intel_missing_returns_none(loop):
    result = await loop.get_intel("nonexistent", "pdf")
    assert result is None


async def test_get_intel_returns_dict(loop, db):
    await db.execute(
        "INSERT INTO niche_intelligence (niche, product_type, performance_score, confidence_level, last_updated_at) VALUES (?,?,?,?,?)",
        ("planner", "printable_pdf", 0.8, "high", time.time()),
    )
    await db.commit()
    result = await loop.get_intel("planner", "printable_pdf")
    assert result is not None
    assert result["performance_score"] == 0.8
    assert result["confidence_level"] == "high"


# ---------------------------------------------------------------------------
# update_niche_intelligence
# ---------------------------------------------------------------------------

async def test_update_niche_intelligence_empty_source(loop):
    n = await loop.update_niche_intelligence()
    assert n == 0


async def test_update_niche_intelligence_with_listings(loop, db):
    await db.executemany(
        "INSERT INTO listing_performance (etsy_listing_id, niche, product_type, ctr, conversion_rate, orders, revenue_eur) VALUES (?,?,?,?,?,?,?)",
        [
            ("listing_1", "planner", "printable_pdf", 0.03, 0.05, 2, 19.98),
            ("listing_2", "planner", "printable_pdf", 0.04, 0.06, 3, 29.97),
        ],
    )
    await db.commit()
    n = await loop.update_niche_intelligence()
    assert n >= 1
    result = await loop.get_intel("planner", "printable_pdf")
    assert result is not None
    assert result["performance_score"] > 0


# ---------------------------------------------------------------------------
# get_unexplored_candidates
# ---------------------------------------------------------------------------

async def test_get_unexplored_candidates_empty_returns_list(loop):
    result = await loop.get_unexplored_candidates()
    assert isinstance(result, list)
