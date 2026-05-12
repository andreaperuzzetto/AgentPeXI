"""Coverage tests for bundle_strategy, shop_optimizer and learning_loop (gap).

asyncio_mode = auto (pytest.ini).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from apps.backend.core.bundle_strategy import BundleStrategy
from apps.backend.core.learning_loop import LearningLoop
from apps.backend.core.shop_optimizer import ShopProfileOptimizer

# ---------------------------------------------------------------------------
# Extended schema used by LearningLoop gap tests
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

CREATE TABLE IF NOT EXISTS listing_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT NOT NULL,
    production_queue_id INTEGER,
    niche TEXT,
    product_type TEXT,
    ctr REAL DEFAULT 0.0,
    conversion_rate REAL DEFAULT 0.0,
    favorite_rate REAL DEFAULT 0.0,
    orders INTEGER DEFAULT 0,
    revenue_eur REAL DEFAULT 0.0,
    template TEXT,
    color_scheme TEXT,
    ladder_level TEXT,
    days_live INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    snapshot_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT,
    product_type TEXT,
    status TEXT DEFAULT 'planned',
    published_at REAL,
    etsy_listing_id TEXT,
    listing_title TEXT
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER
);
"""


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


# ---------------------------------------------------------------------------
# Cursor / DB mock helpers
# ---------------------------------------------------------------------------

def _cursor(fetchone=None, fetchall=None):
    c = AsyncMock()
    c.fetchone = AsyncMock(return_value=fetchone)
    c.fetchall = AsyncMock(return_value=fetchall if fetchall is not None else [])
    return c


def _make_mock_db(*cursors):
    """Mock DB whose execute() returns *cursors* in sequence."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(cursors))
    db.commit = AsyncMock()
    return db


# ===========================================================================
# BundleStrategy
# ===========================================================================

class TestBundleStrategy:

    def _make_bs(self, db_mock, learning_loop=None):
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db_mock)
        return BundleStrategy(memory=memory, learning_loop=learning_loop)

    # --- should_create_bundle ---

    async def test_should_create_bundle_insufficient_listings(self):
        db = _make_mock_db(_cursor(fetchone={"cnt": 2}))
        bs = self._make_bs(db)
        result = await asyncio.wait_for(bs.should_create_bundle("planners"), timeout=5)
        assert result is False

    async def test_should_create_bundle_existing_bundle_in_pipeline(self):
        db = _make_mock_db(
            _cursor(fetchone={"cnt": 5}),
            _cursor(fetchone={"product_type": "bundle"}),
        )
        bs = self._make_bs(db)
        result = await asyncio.wait_for(bs.should_create_bundle("planners"), timeout=5)
        assert result is False

    async def test_should_create_bundle_low_performance_score(self):
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.4})
        db = _make_mock_db(
            _cursor(fetchone={"cnt": 5}),
            _cursor(fetchone=None),
        )
        bs = self._make_bs(db, learning_loop=ll)
        result = await asyncio.wait_for(bs.should_create_bundle("planners"), timeout=5)
        assert result is False

    async def test_should_create_bundle_success(self):
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.8})
        db = _make_mock_db(
            _cursor(fetchone={"cnt": 5}),
            _cursor(fetchone=None),
        )
        bs = self._make_bs(db, learning_loop=ll)
        result = await asyncio.wait_for(bs.should_create_bundle("planners"), timeout=5)
        assert result is True

    # --- generate_bundle_spec ---

    async def test_generate_bundle_spec_basic(self):
        """Two components, verify price formula: (4.99+5.99)*0.70 → 7.99."""
        rows = [
            {
                "listing_title": "Planner A",
                "keywords": '["planner","weekly"]',
                "listing_price": 4.99,
                "image_url": "http://a.com/img1.jpg",
            },
            {
                "listing_title": "Planner B",
                "keywords": '["planner","monthly"]',
                "listing_price": 5.99,
                "image_url": "http://a.com/img2.jpg",
            },
        ]
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.75})
        db = _make_mock_db(_cursor(fetchall=rows))
        bs = self._make_bs(db, learning_loop=ll)
        spec = await asyncio.wait_for(bs.generate_bundle_spec("planners"), timeout=5)

        assert spec["product_type"] == "bundle"
        assert spec["niche"] == "planners"
        assert spec["n_components"] == 2
        assert len(spec["component_titles"]) == 2
        assert len(spec["keywords"]) <= 13
        assert "bundle" in spec["keywords"]
        # (4.99+5.99)*0.70=7.686 → round to 8.0 → 8.0-0.01=7.99
        assert spec["suggested_price"] == pytest.approx(7.99)

    async def test_generate_bundle_spec_empty_rows(self):
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.5})
        db = _make_mock_db(_cursor(fetchall=[]))
        bs = self._make_bs(db, learning_loop=ll)
        spec = await asyncio.wait_for(bs.generate_bundle_spec("empty_niche"), timeout=5)

        assert spec["n_components"] == 0
        assert spec["suggested_price"] == 0.0
        assert spec["component_titles"] == []
        assert "bundle" in spec["keywords"]

    async def test_generate_bundle_spec_price_minimum(self):
        """Very low price → capped at 0.99."""
        rows = [{"listing_title": "T", "keywords": None, "listing_price": 0.50, "image_url": None}]
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.5})
        db = _make_mock_db(_cursor(fetchall=rows))
        bs = self._make_bs(db, learning_loop=ll)
        spec = await asyncio.wait_for(bs.generate_bundle_spec("cheap"), timeout=5)
        assert spec["suggested_price"] == pytest.approx(0.99)

    async def test_generate_bundle_spec_invalid_keywords_json(self):
        """Malformed keywords JSON → skipped gracefully."""
        rows = [{"listing_title": "T", "keywords": "{bad json}", "listing_price": 5.0, "image_url": None}]
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.5})
        db = _make_mock_db(_cursor(fetchall=rows))
        bs = self._make_bs(db, learning_loop=ll)
        spec = await asyncio.wait_for(bs.generate_bundle_spec("niche"), timeout=5)
        # Just the bundle seeds, no extra keywords from bad JSON
        assert spec["n_components"] == 1

    # --- check_all_niches ---

    async def test_check_all_niches_empty(self):
        db = _make_mock_db(_cursor(fetchall=[]))
        bs = self._make_bs(db)
        result = await asyncio.wait_for(bs.check_all_niches(), timeout=5)
        assert result == []

    async def test_check_all_niches_with_candidate(self):
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.8})
        # execute calls in order:
        # 1. check_all_niches: candidates fetchall
        # 2. should_create_bundle: count fetchone
        # 3. should_create_bundle: bundle check fetchone
        # 4. generate_bundle_spec: component fetchall
        db = _make_mock_db(
            _cursor(fetchall=[{"niche": "planners", "cnt": 5}]),
            _cursor(fetchone={"cnt": 5}),
            _cursor(fetchone=None),
            _cursor(fetchall=[
                {"listing_title": "P", "keywords": "[]", "listing_price": 5.99, "image_url": None}
            ]),
        )
        bs = self._make_bs(db, learning_loop=ll)
        result = await asyncio.wait_for(bs.check_all_niches(), timeout=5)
        assert len(result) == 1
        assert result[0]["niche"] == "planners"

    # --- _merge_keywords ---

    def test_merge_keywords_seeds_always_present(self):
        bs = BundleStrategy(memory=MagicMock())
        merged = bs._merge_keywords([])
        assert merged[:3] == ["digital bundle", "bundle", "printable bundle"]

    def test_merge_keywords_dedup_case_insensitive(self):
        bs = BundleStrategy(memory=MagicMock())
        merged = bs._merge_keywords(["Bundle", "BUNDLE", "planner", "Planner"])
        bundle_count = sum(1 for k in merged if k.lower() == "bundle")
        assert bundle_count == 1
        planner_count = sum(1 for k in merged if k.lower() == "planner")
        assert planner_count == 1

    def test_merge_keywords_cap_at_13(self):
        bs = BundleStrategy(memory=MagicMock())
        merged = bs._merge_keywords([f"kw{i}" for i in range(20)])
        assert len(merged) <= 13

    def test_merge_keywords_skips_invalid(self):
        bs = BundleStrategy(memory=MagicMock())
        merged = bs._merge_keywords([None, 42, "", "  ", "good"])  # type: ignore[list-item]
        assert "good" in merged

    # --- _get_performance_score ---

    async def test_get_performance_score_via_learning_loop(self):
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.85})
        bs = BundleStrategy(memory=MagicMock(), learning_loop=ll)
        score = await asyncio.wait_for(bs._get_performance_score("planners"), timeout=5)
        assert score == pytest.approx(0.85)

    async def test_get_performance_score_ll_none_intel_fallback(self):
        """LearningLoop returns None → fallback to DB."""
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value=None)
        db = _make_mock_db(_cursor(fetchone={"performance_score": 0.72}))
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        bs = BundleStrategy(memory=memory, learning_loop=ll)
        score = await asyncio.wait_for(bs._get_performance_score("planners"), timeout=5)
        assert score == pytest.approx(0.72)

    async def test_get_performance_score_ll_exception_fallback_db(self):
        """LearningLoop raises → fallback to DB row."""
        ll = MagicMock()
        ll.get_intel = AsyncMock(side_effect=RuntimeError("LL down"))
        db = _make_mock_db(_cursor(fetchone={"performance_score": 0.72}))
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        bs = BundleStrategy(memory=memory, learning_loop=ll)
        score = await asyncio.wait_for(bs._get_performance_score("planners"), timeout=5)
        assert score == pytest.approx(0.72)

    async def test_get_performance_score_db_no_row_returns_half(self):
        db = _make_mock_db(_cursor(fetchone=None))
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        bs = BundleStrategy(memory=memory)
        score = await asyncio.wait_for(bs._get_performance_score("unknown"), timeout=5)
        assert score == pytest.approx(0.5)

    async def test_get_performance_score_db_exception_returns_half(self):
        memory = MagicMock()
        memory.get_db = AsyncMock(side_effect=RuntimeError("DB down"))
        bs = BundleStrategy(memory=memory)
        score = await asyncio.wait_for(bs._get_performance_score("broken"), timeout=5)
        assert score == pytest.approx(0.5)


# ===========================================================================
# ShopProfileOptimizer
# ===========================================================================

class TestShopProfileOptimizer:

    def _make_optimizer(
        self,
        learning_loop=None,
        etsy_client=None,
        mock_mode: bool = False,
        llm_mock=None,
        db_cursor_fetchone=None,
    ):
        memory = MagicMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchone = AsyncMock(return_value=db_cursor_fetchone)
        db_mock.execute = AsyncMock(return_value=cursor_mock)
        db_mock.commit = AsyncMock()
        memory.get_db = AsyncMock(return_value=db_mock)

        with patch("anthropic.AsyncAnthropic"):
            opt = ShopProfileOptimizer(
                memory=memory,
                etsy_client=etsy_client,
                learning_loop=learning_loop,
                mock_mode=mock_mode,
            )

        if llm_mock is not None:
            opt._llm = llm_mock

        return opt, memory, db_mock, cursor_mock

    def _make_llm(self, text: str = "Generated about text"):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = [MagicMock(text=f"  {text}  ")]
        llm.messages = MagicMock()
        llm.messages.create = AsyncMock(return_value=resp)
        return llm

    # --- _static_about ---

    def test_static_about_contains_niches(self):
        text = ShopProfileOptimizer._static_about(["planners", "wall art"])
        assert "planners" in text
        assert "wall art" in text
        assert "printable" in text.lower()

    def test_static_about_empty_niches(self):
        text = ShopProfileOptimizer._static_about([])
        assert isinstance(text, str)
        assert len(text) > 0

    # --- generate_shop_title ---

    async def test_generate_shop_title_within_limit(self):
        opt, *_ = self._make_optimizer()
        title = await asyncio.wait_for(
            opt.generate_shop_title(["planners", "wall art", "journals"]), timeout=5
        )
        assert len(title) <= 55

    async def test_generate_shop_title_fallback_niches_when_empty(self):
        opt, *_ = self._make_optimizer()
        title = await asyncio.wait_for(opt.generate_shop_title([]), timeout=5)
        assert isinstance(title, str)
        assert len(title) <= 55

    async def test_generate_shop_title_long_niches_truncated(self):
        opt, *_ = self._make_optimizer()
        long_niches = [
            "a very long niche name here",
            "another extremely long niche name here",
            "yet one more extremely long niche",
        ]
        title = await asyncio.wait_for(opt.generate_shop_title(long_niches), timeout=5)
        assert len(title) <= 55
        assert title.endswith("…")

    # --- generate_shop_about ---

    async def test_generate_shop_about_uses_llm(self):
        llm = self._make_llm("Great shop!")
        opt, *_ = self._make_optimizer(llm_mock=llm)
        about = await asyncio.wait_for(opt.generate_shop_about(["planners"]), timeout=5)
        assert about == "Great shop!"
        llm.messages.create.assert_called_once()

    async def test_generate_shop_about_llm_failure_returns_fallback(self):
        llm = MagicMock()
        llm.messages = MagicMock()
        llm.messages.create = AsyncMock(side_effect=RuntimeError("LLM down"))
        opt, *_ = self._make_optimizer(llm_mock=llm)
        about = await asyncio.wait_for(opt.generate_shop_about(["planners"]), timeout=5)
        assert "planners" in about
        assert "printable" in about.lower()

    # --- _get_top_niches ---

    async def test_get_top_niches_no_learning_loop(self):
        opt, *_ = self._make_optimizer()
        niches = await asyncio.wait_for(opt._get_top_niches(), timeout=5)
        assert len(niches) > 0

    async def test_get_top_niches_with_learning_loop(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners", "journals"])
        opt, *_ = self._make_optimizer(learning_loop=ll)
        niches = await asyncio.wait_for(opt._get_top_niches(), timeout=5)
        assert "planners" in niches

    async def test_get_top_niches_ll_exception_fallback(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(side_effect=RuntimeError("LL error"))
        opt, *_ = self._make_optimizer(learning_loop=ll)
        niches = await asyncio.wait_for(opt._get_top_niches(), timeout=5)
        assert len(niches) > 0

    async def test_get_top_niches_with_focus_niche_prepended(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners", "journals"])
        opt, *_ = self._make_optimizer(learning_loop=ll)
        niches = await asyncio.wait_for(opt._get_top_niches(focus_niche="wall art"), timeout=5)
        assert niches[0] == "wall art"

    async def test_get_top_niches_focus_niche_deduped(self):
        """focus_niche already in list → not duplicated."""
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners", "journals"])
        opt, *_ = self._make_optimizer(learning_loop=ll)
        niches = await asyncio.wait_for(opt._get_top_niches(focus_niche="planners"), timeout=5)
        assert niches.count("planners") == 1
        assert niches[0] == "planners"

    # --- _has_niches_changed ---

    async def test_has_niches_changed_no_config(self):
        """No stored config → True (never applied)."""
        opt, _, db_mock, cursor_mock = self._make_optimizer()
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt._has_niches_changed(["planners"]), timeout=5)
        assert result is True

    async def test_has_niches_changed_same_niches(self):
        saved = ["planners", "journals"]
        opt, _, db_mock, cursor_mock = self._make_optimizer()
        cursor_mock.fetchone = AsyncMock(return_value={"value": json.dumps(saved)})
        result = await asyncio.wait_for(opt._has_niches_changed(saved), timeout=5)
        assert result is False

    async def test_has_niches_changed_different_niches(self):
        opt, _, db_mock, cursor_mock = self._make_optimizer()
        cursor_mock.fetchone = AsyncMock(
            return_value={"value": json.dumps(["old_niche"])}
        )
        result = await asyncio.wait_for(opt._has_niches_changed(["planners"]), timeout=5)
        assert result is True

    async def test_has_niches_changed_invalid_json(self):
        opt, _, db_mock, cursor_mock = self._make_optimizer()
        cursor_mock.fetchone = AsyncMock(return_value={"value": "{bad json}"})
        result = await asyncio.wait_for(opt._has_niches_changed(["planners"]), timeout=5)
        assert result is True

    # --- apply_shop_profile ---

    async def test_apply_shop_profile_mock_mode(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners"])
        llm = self._make_llm("About text")
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, mock_mode=True, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt.apply_shop_profile(), timeout=5)
        assert result["status"] == "mock"
        assert "title" in result
        assert "about" in result

    async def test_apply_shop_profile_no_api(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners"])
        llm = self._make_llm("About")
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, etsy_client=None, mock_mode=False, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt.apply_shop_profile(), timeout=5)
        assert result["status"] == "no_api"

    async def test_apply_shop_profile_niches_unchanged(self):
        niches = ["planners"]
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=niches)
        llm = self._make_llm("About")
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(
            return_value={"value": json.dumps(niches)}
        )
        result = await asyncio.wait_for(opt.apply_shop_profile(), timeout=5)
        assert result["status"] == "skipped"
        assert result["changed"] is False

    async def test_apply_shop_profile_applied(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners"])
        llm = self._make_llm("About")
        etsy = AsyncMock()
        etsy.update_shop = AsyncMock()
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, etsy_client=etsy, mock_mode=False, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt.apply_shop_profile(), timeout=5)
        assert result["status"] == "applied"
        etsy.update_shop.assert_called_once()

    async def test_apply_shop_profile_error(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners"])
        llm = self._make_llm("About")
        etsy = AsyncMock()
        etsy.update_shop = AsyncMock(side_effect=RuntimeError("API Error"))
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, etsy_client=etsy, mock_mode=False, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt.apply_shop_profile(), timeout=5)
        assert result["status"] == "error"
        assert "API Error" in result["error"]

    async def test_apply_shop_profile_force(self):
        """force=True → applies even when niches are unchanged."""
        niches = ["planners"]
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=niches)
        llm = self._make_llm("About")
        etsy = AsyncMock()
        etsy.update_shop = AsyncMock()
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, etsy_client=etsy, mock_mode=False, llm_mock=llm
        )
        # Same niches stored, but force=True
        cursor_mock.fetchone = AsyncMock(
            return_value={"value": json.dumps(niches)}
        )
        result = await asyncio.wait_for(opt.apply_shop_profile(force=True), timeout=5)
        assert result["status"] == "applied"
        assert result["changed"] is True

    # --- preview ---

    async def test_preview_returns_correct_structure(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners"])
        llm = self._make_llm("Preview about")
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt.preview(), timeout=5)
        assert "title" in result
        assert "about" in result
        assert "niches" in result
        assert "changed" in result
        assert "last_applied_title" in result
        assert result["last_applied_title"] == "—"

    async def test_preview_with_focus_niche(self):
        ll = MagicMock()
        ll.get_top_niches = AsyncMock(return_value=["planners", "journals"])
        llm = self._make_llm("About")
        opt, _, db_mock, cursor_mock = self._make_optimizer(
            learning_loop=ll, llm_mock=llm
        )
        cursor_mock.fetchone = AsyncMock(return_value=None)
        result = await asyncio.wait_for(opt.preview(focus_niche="wall art"), timeout=5)
        assert result["niches"][0] == "wall art"


# ===========================================================================
# LearningLoop gap tests
# ===========================================================================

class TestLearningLoopGap:
    """Covers lines not reached by the existing tests/test_learning_loop.py."""

    # --- flag_low_ctr ---

    async def test_flag_low_ctr_calls_store_insight(self):
        memory = MagicMock()
        memory.store_insight = AsyncMock()
        loop = LearningLoop(memory=memory)

        await asyncio.wait_for(
            loop.flag_low_ctr("planners", "pdf", "minimal", "navy"),
            timeout=5,
        )

        memory.store_insight.assert_called_once()
        text, meta = memory.store_insight.call_args[0]
        assert "planners" in text
        assert "minimal" in text
        assert meta["type"] == "low_ctr_signal"
        assert meta["niche"] == "planners"
        assert meta["template"] == "minimal"

    async def test_flag_low_ctr_fail_safe_on_exception(self):
        """store_insight raising must NOT propagate."""
        memory = MagicMock()
        memory.store_insight = AsyncMock(side_effect=RuntimeError("ChromaDB down"))
        loop = LearningLoop(memory=memory)

        # Must complete without raising
        await asyncio.wait_for(
            loop.flag_low_ctr("planners", "pdf", "minimal", "navy"),
            timeout=5,
        )

    # --- flag_for_seo_revision ---

    async def test_flag_for_seo_revision_lowers_score(self, db):
        await db.execute(
            "INSERT INTO niche_intelligence "
            "(niche, product_type, performance_score, confidence_level, last_updated_at) "
            "VALUES (?,?,?,?,?)",
            ("planners", "pdf", 0.8, "high", time.time()),
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        new_score = await asyncio.wait_for(
            loop.flag_for_seo_revision("planners", "pdf"), timeout=5
        )
        assert new_score == pytest.approx(0.7)

    async def test_flag_for_seo_revision_capped_at_min(self, db):
        await db.execute(
            "INSERT INTO niche_intelligence "
            "(niche, product_type, performance_score, confidence_level, last_updated_at) "
            "VALUES (?,?,?,?,?)",
            ("planners", "pdf", 0.25, "low", time.time()),
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        new_score = await asyncio.wait_for(
            loop.flag_for_seo_revision("planners", "pdf"), timeout=5
        )
        assert new_score == pytest.approx(0.2)

    async def test_flag_for_seo_revision_no_row_uses_default(self, db):
        """No existing row → default score 0.5, result = 0.4."""
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        new_score = await asyncio.wait_for(
            loop.flag_for_seo_revision("nonexistent", "pdf"), timeout=5
        )
        assert new_score == pytest.approx(0.4)

    # --- compare_ab_thumbnails ---

    async def test_compare_ab_thumbnails_skipped_too_few_listings(self):
        """< 2 listing rows → status='skipped'."""
        memory = MagicMock()
        memory.store_insight = AsyncMock()
        db_mock = AsyncMock()

        c1 = AsyncMock()
        c1.fetchone = AsyncMock(return_value={"product_type": "pdf"})

        c2 = AsyncMock()
        c2.fetchall = AsyncMock(return_value=[
            {
                "queue_id": 1, "etsy_listing_id": "e1", "listing_title": "T1",
                "published_at": 1000.0, "ctr": 0.02, "views": 100, "clicks": 2,
                "template": "minimal", "color_scheme": "navy",
                "ladder_level": "ctr_low", "snapshot_at": 2000.0,
            }
        ])

        db_mock.execute = AsyncMock(side_effect=[c1, c2])
        memory.get_db = AsyncMock(return_value=db_mock)

        loop = LearningLoop(memory=memory)
        result = await asyncio.wait_for(
            loop.compare_ab_thumbnails("planners"), timeout=5
        )
        assert result["status"] == "skipped"
        assert result["niche"] == "planners"

    async def test_compare_ab_thumbnails_skipped_no_pair(self):
        """2 rows but no ctr_low original → skipped."""
        memory = MagicMock()
        memory.store_insight = AsyncMock()
        db_mock = AsyncMock()

        c1 = AsyncMock()
        c1.fetchone = AsyncMock(return_value={"product_type": "pdf"})

        rows = [
            {
                "queue_id": 1, "etsy_listing_id": "e1", "listing_title": "T1",
                "published_at": 1000.0, "ctr": 0.03, "views": 100, "clicks": 3,
                "template": "modern", "color_scheme": "blue",
                "ladder_level": "normal", "snapshot_at": 2000.0,
            },
            {
                "queue_id": 2, "etsy_listing_id": "e2", "listing_title": "T2",
                "published_at": 2000.0, "ctr": 0.04, "views": 100, "clicks": 4,
                "template": "clean", "color_scheme": "green",
                "ladder_level": "normal", "snapshot_at": 3000.0,
            },
        ]
        c2 = AsyncMock()
        c2.fetchall = AsyncMock(return_value=rows)
        db_mock.execute = AsyncMock(side_effect=[c1, c2])
        memory.get_db = AsyncMock(return_value=db_mock)

        loop = LearningLoop(memory=memory)
        result = await asyncio.wait_for(
            loop.compare_ab_thumbnails("planners"), timeout=5
        )
        assert result["status"] == "skipped"

    async def test_compare_ab_thumbnails_alternative_wins(self):
        """Alternative CTR > original CTR → alt is winner."""
        memory = MagicMock()
        memory.store_insight = AsyncMock()
        db_mock = AsyncMock()

        c1 = AsyncMock()
        c1.fetchone = AsyncMock(return_value={"product_type": "pdf"})

        rows = [
            {
                "queue_id": 1, "etsy_listing_id": "e1", "listing_title": "T1",
                "published_at": 1000.0, "ctr": 0.01, "views": 100, "clicks": 1,
                "template": "old_tpl", "color_scheme": "red",
                "ladder_level": "ctr_low", "snapshot_at": 2000.0,
            },
            {
                "queue_id": 2, "etsy_listing_id": "e2", "listing_title": "T2",
                "published_at": 2000.0, "ctr": 0.05, "views": 100, "clicks": 5,
                "template": "new_tpl", "color_scheme": "blue",
                "ladder_level": "normal", "snapshot_at": 3000.0,
            },
        ]
        c2 = AsyncMock()
        c2.fetchall = AsyncMock(return_value=rows)
        db_mock.execute = AsyncMock(side_effect=[c1, c2])
        memory.get_db = AsyncMock(return_value=db_mock)

        loop = LearningLoop(memory=memory)
        result = await asyncio.wait_for(
            loop.compare_ab_thumbnails("planners"), timeout=5
        )

        assert result["status"] == "compared"
        assert result["winner"]["template"] == "new_tpl"
        assert result["loser"]["template"] == "old_tpl"
        # store_insight called for design_winner + flag_low_ctr
        memory.store_insight.assert_called()

    async def test_compare_ab_thumbnails_original_wins(self):
        """Original CTR > alternative → original is winner."""
        memory = MagicMock()
        memory.store_insight = AsyncMock()
        db_mock = AsyncMock()

        c1 = AsyncMock()
        c1.fetchone = AsyncMock(return_value={"product_type": "pdf"})

        rows = [
            {
                "queue_id": 1, "etsy_listing_id": "e1", "listing_title": "T1",
                "published_at": 1000.0, "ctr": 0.08, "views": 100, "clicks": 8,
                "template": "orig_tpl", "color_scheme": "gold",
                "ladder_level": "ctr_low", "snapshot_at": 2000.0,
            },
            {
                "queue_id": 2, "etsy_listing_id": "e2", "listing_title": "T2",
                "published_at": 2000.0, "ctr": 0.02, "views": 100, "clicks": 2,
                "template": "alt_tpl", "color_scheme": "silver",
                "ladder_level": "normal", "snapshot_at": 3000.0,
            },
        ]
        c2 = AsyncMock()
        c2.fetchall = AsyncMock(return_value=rows)
        db_mock.execute = AsyncMock(side_effect=[c1, c2])
        memory.get_db = AsyncMock(return_value=db_mock)

        loop = LearningLoop(memory=memory)
        result = await asyncio.wait_for(
            loop.compare_ab_thumbnails("planners"), timeout=5
        )

        assert result["status"] == "compared"
        assert result["winner"]["template"] == "orig_tpl"
        assert result["loser"]["template"] == "alt_tpl"

    async def test_compare_ab_thumbnails_store_insight_exception_swallowed(self):
        """store_insight raising must not break compare_ab_thumbnails."""
        memory = MagicMock()
        memory.store_insight = AsyncMock(side_effect=RuntimeError("ChromaDB down"))
        db_mock = AsyncMock()

        c1 = AsyncMock()
        c1.fetchone = AsyncMock(return_value={"product_type": "pdf"})

        rows = [
            {
                "queue_id": 1, "etsy_listing_id": "e1", "listing_title": "T1",
                "published_at": 1000.0, "ctr": 0.01, "views": 100, "clicks": 1,
                "template": "old", "color_scheme": "red",
                "ladder_level": "ctr_low", "snapshot_at": 2000.0,
            },
            {
                "queue_id": 2, "etsy_listing_id": "e2", "listing_title": "T2",
                "published_at": 2000.0, "ctr": 0.05, "views": 100, "clicks": 5,
                "template": "new", "color_scheme": "blue",
                "ladder_level": "normal", "snapshot_at": 3000.0,
            },
        ]
        c2 = AsyncMock()
        c2.fetchall = AsyncMock(return_value=rows)
        db_mock.execute = AsyncMock(side_effect=[c1, c2])
        memory.get_db = AsyncMock(return_value=db_mock)

        loop = LearningLoop(memory=memory)
        # Must not raise
        result = await asyncio.wait_for(
            loop.compare_ab_thumbnails("planners"), timeout=5
        )
        assert result["status"] == "compared"

    # --- run_full_update ---

    async def test_run_full_update_returns_summary(self, db):
        await db.executemany(
            "INSERT INTO listing_performance "
            "(etsy_listing_id, niche, product_type, ctr, conversion_rate, orders, revenue_eur) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                ("l1", "planners", "pdf", 0.03, 0.05, 2, 20.0),
                ("l2", "planners", "pdf", 0.04, 0.06, 3, 30.0),
                ("l3", "journals", "pdf", 0.02, 0.04, 1, 10.0),
            ],
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        result = await asyncio.wait_for(loop.run_full_update(), timeout=5)
        assert result["n_updated"] == 2  # planners + journals
        assert "top_niches" in result
        assert "updated_at" in result

    async def test_run_full_update_empty_db(self, db):
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        result = await asyncio.wait_for(loop.run_full_update(), timeout=5)
        assert result["n_updated"] == 0
        assert isinstance(result["top_niches"], list)


# ===========================================================================
# Additional gap tests — fill remaining uncovered lines
# ===========================================================================

class TestBundleStrategyGap:
    """Covers bundle_strategy.py lines 212-213 (POD_ENABLED import exception)."""

    async def test_generate_bundle_spec_pod_import_exception(self):
        """Config import failure → pod_companion_type defaults to None."""
        ll = MagicMock()
        ll.get_intel = AsyncMock(return_value={"performance_score": 0.6})
        db = _make_mock_db(_cursor(fetchall=[]))
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        bs = BundleStrategy(memory=memory, learning_loop=ll)

        with patch.dict(sys.modules, {"apps.backend.core.config": None}):
            spec = await asyncio.wait_for(bs.generate_bundle_spec("niche"), timeout=5)

        assert spec["pod_companion_type"] is None


class TestShopOptimizerGap:
    """Covers shop_optimizer.py lines 291-292 and 303-304."""

    def _make_llm(self, text: str = "About"):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        llm.messages = MagicMock()
        llm.messages.create = AsyncMock(return_value=resp)
        return llm

    async def test_save_applied_niches_db_exception_swallowed(self):
        """DB exception inside _save_applied_niches must not propagate."""
        memory = MagicMock()
        db_mock = AsyncMock()
        db_mock.execute = AsyncMock(side_effect=RuntimeError("DB write error"))
        memory.get_db = AsyncMock(return_value=db_mock)

        with patch("anthropic.AsyncAnthropic"):
            opt = ShopProfileOptimizer(memory=memory)

        # Must complete without raising
        await asyncio.wait_for(
            opt._save_applied_niches(["planners"], "My Shop"), timeout=5
        )

    async def test_get_config_exception_returns_none(self):
        """get_db raising → _get_config returns None → _has_niches_changed True."""
        memory = MagicMock()
        memory.get_db = AsyncMock(side_effect=RuntimeError("DB error"))

        with patch("anthropic.AsyncAnthropic"):
            opt = ShopProfileOptimizer(memory=memory)

        result = await asyncio.wait_for(opt._has_niches_changed(["planners"]), timeout=5)
        assert result is True  # _get_config returned None


class TestLearningLoopGap2:
    """Covers remaining uncovered lines in learning_loop.py."""

    # --- _calculate_performance_score line 164 ---

    def test_calculate_performance_score_zero_listings_returns_half(self):
        loop = LearningLoop(memory=MagicMock())
        assert loop._calculate_performance_score(0, 0.0, 0.0, 0.0) == 0.5

    # --- _confidence_level line 182 ---

    def test_confidence_level_high(self):
        loop = LearningLoop(memory=MagicMock())
        assert loop._confidence_level(5) == "high"
        assert loop._confidence_level(10) == "high"

    # --- get_intel lines 320-346 ---

    async def test_get_intel_with_explicit_product_type(self, db):
        await db.execute(
            "INSERT INTO niche_intelligence "
            "(niche, product_type, performance_score, confidence_level, last_updated_at) "
            "VALUES (?,?,?,?,?)",
            ("planners", "pdf", 0.77, "medium", time.time()),
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        result = await asyncio.wait_for(
            loop.get_intel("planners", "pdf"), timeout=5
        )
        assert result is not None
        assert result["performance_score"] == pytest.approx(0.77)
        assert result["niche"] == "planners"

    async def test_get_intel_product_type_none_returns_best(self, db):
        """product_type=None → row with highest score."""
        await db.executemany(
            "INSERT INTO niche_intelligence "
            "(niche, product_type, performance_score, confidence_level, last_updated_at) "
            "VALUES (?,?,?,?,?)",
            [
                ("planners", "pdf", 0.6, "medium", time.time()),
                ("planners", "svg", 0.9, "high", time.time()),
            ],
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        result = await asyncio.wait_for(
            loop.get_intel("planners", None), timeout=5
        )
        assert result is not None
        assert result["performance_score"] == pytest.approx(0.9)

    async def test_get_intel_missing_returns_none(self, db):
        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)
        result = await asyncio.wait_for(
            loop.get_intel("nonexistent", "pdf"), timeout=5
        )
        assert result is None

    # --- get_unexplored_candidates lines 368-387 ---

    async def test_get_unexplored_candidates_returns_list(self, db):
        await db.execute(
            "INSERT INTO niche_intelligence "
            "(niche, product_type, performance_score, confidence_level, last_updated_at) "
            "VALUES (?,?,?,?,?)",
            ("unexplored_niche", "pdf", 0.5, "low", time.time()),
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        result = await asyncio.wait_for(loop.get_unexplored_candidates(), timeout=5)
        assert isinstance(result, list)
        niches = [r["niche"] for r in result]
        assert "unexplored_niche" in niches

    async def test_get_unexplored_candidates_excludes_recently_published(self, db):
        """Niche with a recent published listing → excluded from candidates."""
        await db.execute(
            "INSERT INTO niche_intelligence "
            "(niche, product_type, performance_score, confidence_level, last_updated_at) "
            "VALUES (?,?,?,?,?)",
            ("active_niche", "pdf", 0.8, "high", time.time()),
        )
        await db.execute(
            "INSERT INTO production_queue (niche, product_type, status, published_at) "
            "VALUES (?,?,?,?)",
            ("active_niche", "pdf", "published", time.time()),
        )
        await db.commit()

        memory = MagicMock()
        memory.get_db = AsyncMock(return_value=db)
        loop = LearningLoop(memory=memory)

        result = await asyncio.wait_for(loop.get_unexplored_candidates(), timeout=5)
        niches = [r["niche"] for r in result]
        assert "active_niche" not in niches

    # --- compare_ab_thumbnails lines 549-550 ---

    async def test_compare_ab_thumbnails_flag_low_ctr_raises_swallowed(self):
        """flag_low_ctr raising directly → caught by outer except, not propagated."""
        memory = MagicMock()
        memory.store_insight = AsyncMock()
        db_mock = AsyncMock()

        c1 = AsyncMock()
        c1.fetchone = AsyncMock(return_value={"product_type": "pdf"})

        rows = [
            {
                "queue_id": 1, "etsy_listing_id": "e1", "listing_title": "T1",
                "published_at": 1000.0, "ctr": 0.01, "views": 100, "clicks": 1,
                "template": "old", "color_scheme": "red",
                "ladder_level": "ctr_low", "snapshot_at": 2000.0,
            },
            {
                "queue_id": 2, "etsy_listing_id": "e2", "listing_title": "T2",
                "published_at": 2000.0, "ctr": 0.05, "views": 100, "clicks": 5,
                "template": "new", "color_scheme": "blue",
                "ladder_level": "normal", "snapshot_at": 3000.0,
            },
        ]
        c2 = AsyncMock()
        c2.fetchall = AsyncMock(return_value=rows)
        db_mock.execute = AsyncMock(side_effect=[c1, c2])
        memory.get_db = AsyncMock(return_value=db_mock)

        loop = LearningLoop(memory=memory)
        # Patch flag_low_ctr to raise directly (not just store_insight)
        loop.flag_low_ctr = AsyncMock(side_effect=RuntimeError("flag_low_ctr crashed"))

        result = await asyncio.wait_for(
            loop.compare_ab_thumbnails("planners"), timeout=5
        )
        # Exception from flag_low_ctr is swallowed; result still valid
        assert result["status"] == "compared"
