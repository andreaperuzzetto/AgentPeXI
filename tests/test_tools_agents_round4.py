"""tests/test_tools_agents_round4.py

Coverage round 4 (~61 tests):
  - tools/svg_gen.py          → _generate_* internal methods + generate_bundle edge cases
  - _market_data/_search_mixin.py  → _SearchMixin HTTP success / error / timeout paths
  - _market_data/_shop_analysis_mixin.py → no-key paths + mocked-anthropic happy paths
  - _pinterest/_warmup_mixin.py    → _WarmupMixin stub phase API contracts
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.backend.tools.svg_gen import SVGGenerator, _default_color_variants
from apps.backend.agents._market_data._search_mixin import _SearchMixin
from apps.backend.agents._market_data._shop_analysis_mixin import _ShopAnalysisMixin
from apps.backend.agents._pinterest._warmup_mixin import _WarmupMixin


# ─────────────────────────────────────────────────────────────────────────────
# Shared palette
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def palette():
    return {
        "bg": "#FFFFFF",
        "primary": "#2C2C2C",
        "secondary": "#E8E8E8",
        "accent": "#8B7355",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SVGGenerator — internal generators  (~20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def gen():
    g = SVGGenerator.__new__(SVGGenerator)
    g._llm = AsyncMock()
    return g


@pytest.mark.asyncio
async def test_generate_mandala_complexity1_creates_file(gen, palette, tmp_path):
    out = tmp_path / "mandala_c1.svg"
    result = await asyncio.wait_for(
        gen._generate_mandala({"complexity": 1}, palette, out), timeout=5
    )
    assert out.exists()
    assert result == out


@pytest.mark.asyncio
async def test_generate_mandala_complexity2_creates_file(gen, palette, tmp_path):
    out = tmp_path / "mandala_c2.svg"
    await asyncio.wait_for(gen._generate_mandala({"complexity": 2}, palette, out), timeout=5)
    assert out.exists()


@pytest.mark.asyncio
async def test_generate_mandala_complexity3_creates_file(gen, palette, tmp_path):
    out = tmp_path / "mandala_c3.svg"
    await asyncio.wait_for(gen._generate_mandala({"complexity": 3}, palette, out), timeout=5)
    assert out.exists()


@pytest.mark.asyncio
async def test_generate_mandala_svg_xml_contains_svg_tag(gen, palette, tmp_path):
    out = tmp_path / "mandala_xml.svg"
    await asyncio.wait_for(gen._generate_mandala({"complexity": 2}, palette, out), timeout=5)
    content = out.read_text()
    assert "<svg" in content


@pytest.mark.asyncio
async def test_generate_geometric_complexity1_creates_file(gen, palette, tmp_path):
    out = tmp_path / "geo_c1.svg"
    result = await asyncio.wait_for(
        gen._generate_geometric({"complexity": 1}, palette, out), timeout=5
    )
    assert out.exists()
    assert result == out


@pytest.mark.asyncio
async def test_generate_geometric_complexity2_creates_file(gen, palette, tmp_path):
    out = tmp_path / "geo_c2.svg"
    await asyncio.wait_for(gen._generate_geometric({"complexity": 2}, palette, out), timeout=5)
    assert out.exists()


@pytest.mark.asyncio
async def test_generate_geometric_complexity3_creates_file(gen, palette, tmp_path):
    out = tmp_path / "geo_c3.svg"
    await asyncio.wait_for(gen._generate_geometric({"complexity": 3}, palette, out), timeout=5)
    assert out.exists()


@pytest.mark.asyncio
async def test_generate_geometric_returns_output_path(gen, palette, tmp_path):
    out = tmp_path / "geo_ret.svg"
    result = await asyncio.wait_for(gen._generate_geometric({}, palette, out), timeout=5)
    assert result == out


@pytest.mark.asyncio
async def test_generate_quote_short_text_in_svg_content(gen, palette, tmp_path):
    out = tmp_path / "quote_short.svg"
    await asyncio.wait_for(gen._generate_quote({"quote": "Dream Big"}, palette, out), timeout=5)
    assert out.exists()
    content = out.read_text()
    assert "DREAM BIG" in content


@pytest.mark.asyncio
async def test_generate_quote_long_text_truncated_with_ellipsis(gen, palette, tmp_path):
    out = tmp_path / "quote_long.svg"
    long_quote = "She believed she could so she did and she was absolutely right all along"
    await asyncio.wait_for(gen._generate_quote({"quote": long_quote}, palette, out), timeout=5)
    assert out.exists()
    content = out.read_text()
    assert "..." in content


@pytest.mark.asyncio
async def test_generate_quote_uses_niche_as_fallback_when_no_quote_key(gen, palette, tmp_path):
    out = tmp_path / "quote_niche.svg"
    await asyncio.wait_for(gen._generate_quote({"niche": "yoga life"}, palette, out), timeout=5)
    assert out.exists()
    content = out.read_text()
    assert "YOGA" in content


@pytest.mark.asyncio
async def test_generate_quote_multiline_splits_long_sentence(gen, palette, tmp_path):
    out = tmp_path / "quote_multi.svg"
    brief = {"quote": "Believe in yourself and never give up ever"}
    await asyncio.wait_for(gen._generate_quote(brief, palette, out), timeout=5)
    assert out.exists()
    content = out.read_text()
    assert "BELIEVE" in content


@pytest.mark.asyncio
async def test_generate_floral_frame_creates_file(gen, palette, tmp_path):
    out = tmp_path / "floral.svg"
    result = await asyncio.wait_for(gen._generate_floral_frame({}, palette, out), timeout=5)
    assert out.exists()
    assert result == out


@pytest.mark.asyncio
async def test_generate_floral_frame_svg_content_nonempty(gen, palette, tmp_path):
    out = tmp_path / "floral_nonempty.svg"
    await asyncio.wait_for(gen._generate_floral_frame({}, palette, out), timeout=5)
    content = out.read_text()
    assert len(content) > 200
    assert "<svg" in content


@pytest.mark.asyncio
async def test_generate_bundle_invalid_svg_type_falls_back_to_geometric(gen, tmp_path):
    brief = {"svg_type": "nonexistent_type", "complexity": 1}
    files = await asyncio.wait_for(gen.generate_bundle(brief, tmp_path), timeout=10)
    assert len(files) == 5
    assert all("geometric" in f.name for f in files)


@pytest.mark.asyncio
async def test_generate_bundle_two_color_variants_repeats_to_five(gen, tmp_path):
    brief = {
        "svg_type": "geometric",
        "complexity": 1,
        "color_variants": [
            {"bg": "#FFF", "primary": "#000", "secondary": "#CCC", "accent": "#999"},
            {"bg": "#EEE", "primary": "#111", "secondary": "#DDD", "accent": "#888"},
        ],
    }
    files = await asyncio.wait_for(gen.generate_bundle(brief, tmp_path), timeout=10)
    assert len(files) == 5


@pytest.mark.asyncio
async def test_generate_bundle_niche_in_filename(gen, tmp_path):
    brief = {"svg_type": "geometric", "niche": "Boho Wedding", "complexity": 1}
    files = await asyncio.wait_for(gen.generate_bundle(brief, tmp_path), timeout=10)
    assert all("boho_wedding" in f.name for f in files)


@pytest.mark.asyncio
async def test_generate_bundle_creates_nested_output_dir(gen, tmp_path):
    new_dir = tmp_path / "sub" / "output"
    brief = {"svg_type": "geometric", "complexity": 1}
    files = await asyncio.wait_for(gen.generate_bundle(brief, new_dir), timeout=10)
    assert new_dir.exists()
    assert len(files) == 5


@pytest.mark.asyncio
async def test_generate_bundle_all_files_are_paths_that_exist(gen, tmp_path):
    brief = {"svg_type": "mandala", "complexity": 1}
    files = await asyncio.wait_for(gen.generate_bundle(brief, tmp_path), timeout=10)
    assert all(isinstance(f, Path) for f in files)
    assert all(f.exists() for f in files)


def test_default_color_variants_all_have_secondary_key():
    for v in _default_color_variants():
        assert "secondary" in v
        assert v["secondary"].startswith("#")


# ═══════════════════════════════════════════════════════════════════════════════
# _SearchMixin — HTTP paths  (~15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class FakeSearch(_SearchMixin):
    """Minimal concrete class with _get_client() pointing to a mock HTTP client."""

    async def _get_client(self):  # type: ignore[override]
        return self._mock_http_client


@pytest.fixture
def searcher():
    s = FakeSearch()
    s._mock_http_client = AsyncMock()
    return s


_SETTINGS_PATH = "apps.backend.agents._market_data._search_mixin.settings"


@pytest.mark.asyncio
async def test_search_etsy_no_api_key_returns_zero_dict(searcher):
    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = ""
        result = await asyncio.wait_for(searcher._search_etsy_listings("yoga mat"), timeout=5)
    assert result == {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}


@pytest.mark.asyncio
async def test_search_etsy_200_valid_response_computes_averages(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "count": 42,
        "results": [
            {"price": {"amount": 500, "divisor": 100, "currency_code": "EUR"}, "num_favorers": 10},
            {"price": {"amount": 800, "divisor": 100, "currency_code": "EUR"}, "num_favorers": 20},
        ],
    }
    searcher._mock_http_client.get.return_value = resp

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        ms.USD_EUR_RATE = 0.92
        result = await asyncio.wait_for(searcher._search_etsy_listings("yoga mat"), timeout=5)

    assert result["count"] == 42
    assert result["avg_price_eur"] == pytest.approx(6.50, rel=1e-3)
    assert result["avg_reviews"] == 15.0


@pytest.mark.asyncio
async def test_search_etsy_usd_price_converts_to_eur(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "count": 1,
        "results": [
            {"price": {"amount": 1000, "divisor": 100, "currency_code": "USD"}, "num_favorers": 5},
        ],
    }
    searcher._mock_http_client.get.return_value = resp

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        ms.USD_EUR_RATE = 0.92
        result = await asyncio.wait_for(searcher._search_etsy_listings("wedding print"), timeout=5)

    assert result["avg_price_eur"] == pytest.approx(9.20, rel=1e-2)


@pytest.mark.asyncio
async def test_search_etsy_empty_results_returns_count_with_zero_avgs(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"count": 0, "results": []}
    searcher._mock_http_client.get.return_value = resp

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        ms.USD_EUR_RATE = 0.92
        result = await asyncio.wait_for(searcher._search_etsy_listings("obscure niche"), timeout=5)

    assert result["count"] == 0
    assert result["avg_reviews"] == 0.0
    assert result["avg_price_eur"] == 0.0


@pytest.mark.asyncio
async def test_search_etsy_http_429_returns_zero_dict(searcher):
    resp = MagicMock()
    error_resp = MagicMock()
    error_resp.status_code = 429
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limited", request=MagicMock(), response=error_resp
    )
    searcher._mock_http_client.get.return_value = resp

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        result = await asyncio.wait_for(searcher._search_etsy_listings("yoga mat"), timeout=5)

    assert result == {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}


@pytest.mark.asyncio
async def test_search_etsy_http_500_returns_zero_dict(searcher):
    resp = MagicMock()
    error_resp = MagicMock()
    error_resp.status_code = 500
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=error_resp
    )
    searcher._mock_http_client.get.return_value = resp

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        result = await asyncio.wait_for(searcher._search_etsy_listings("planner"), timeout=5)

    assert result == {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}


@pytest.mark.asyncio
async def test_search_etsy_generic_exception_returns_zero_dict(searcher):
    searcher._mock_http_client.get.side_effect = ConnectionError("network failure")

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        result = await asyncio.wait_for(searcher._search_etsy_listings("yoga mat"), timeout=5)

    assert result == {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}


@pytest.mark.asyncio
async def test_search_etsy_timeout_error_returns_zero_dict(searcher):
    searcher._mock_http_client.get.side_effect = asyncio.TimeoutError()

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        result = await asyncio.wait_for(searcher._search_etsy_listings("yoga mat"), timeout=5)

    assert result == {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}


@pytest.mark.asyncio
async def test_search_etsy_num_favorers_avg_computed(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "count": 3,
        "results": [
            {"num_favorers": 10, "price": {}},
            {"num_favorers": 20, "price": {}},
            {"num_favorers": 30, "price": {}},
        ],
    }
    searcher._mock_http_client.get.return_value = resp

    with patch(_SETTINGS_PATH) as ms:
        ms.ETSY_API_KEY = "test-key"
        ms.USD_EUR_RATE = 0.92
        result = await asyncio.wait_for(searcher._search_etsy_listings("digital art"), timeout=5)

    assert result["avg_reviews"] == 20.0
    assert result["avg_price_eur"] == 0.0  # no valid prices


@pytest.mark.asyncio
async def test_autocomplete_list_response_returns_list(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = ["yoga mat", "yoga pose", "yoga studio"]
    searcher._mock_http_client.get.return_value = resp

    result = await asyncio.wait_for(searcher._get_autocomplete("yoga"), timeout=5)

    assert result == ["yoga mat", "yoga pose", "yoga studio"]


@pytest.mark.asyncio
async def test_autocomplete_dict_with_suggestions_key(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"suggestions": ["boho decor", "boho wedding"]}
    searcher._mock_http_client.get.return_value = resp

    result = await asyncio.wait_for(searcher._get_autocomplete("boho"), timeout=5)

    assert "boho decor" in result
    assert "boho wedding" in result


@pytest.mark.asyncio
async def test_autocomplete_empty_list_returns_empty(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = []
    searcher._mock_http_client.get.return_value = resp

    result = await asyncio.wait_for(searcher._get_autocomplete("xyz12345"), timeout=5)

    assert result == []


@pytest.mark.asyncio
async def test_autocomplete_exception_returns_empty(searcher):
    searcher._mock_http_client.get.side_effect = RuntimeError("connection refused")
    result = await asyncio.wait_for(searcher._get_autocomplete("yoga"), timeout=5)
    assert result == []


@pytest.mark.asyncio
async def test_autocomplete_http_error_returns_empty(searcher):
    resp = MagicMock()
    error_resp = MagicMock()
    error_resp.status_code = 403
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "forbidden", request=MagicMock(), response=error_resp
    )
    searcher._mock_http_client.get.return_value = resp

    result = await asyncio.wait_for(searcher._get_autocomplete("yoga"), timeout=5)

    assert result == []


@pytest.mark.asyncio
async def test_autocomplete_dict_with_results_key(searcher):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"results": [{"value": "party favor"}, {"value": "party print"}]}
    searcher._mock_http_client.get.return_value = resp

    result = await asyncio.wait_for(searcher._get_autocomplete("party"), timeout=5)

    assert "party favor" in result
    assert "party print" in result


# ═══════════════════════════════════════════════════════════════════════════════
# _ShopAnalysisMixin — no-key + mocked-anthropic paths  (~12 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class FakeShopAnalysis(_ShopAnalysisMixin):
    pass


@pytest.fixture
def analyzer():
    a = FakeShopAnalysis()
    a._mock = False
    a._memory = AsyncMock()
    a._memory.query_chromadb = AsyncMock(return_value=[])
    a._memory.store_insight = AsyncMock()
    a._client = AsyncMock()
    return a


_SHOP_SETTINGS_PATH = "apps.backend.agents._market_data._shop_analysis_mixin.settings"


@pytest.mark.asyncio
async def test_get_competitor_shop_analysis_mock_mode_returns_none(analyzer):
    analyzer._mock = True
    result = await asyncio.wait_for(
        analyzer._get_competitor_shop_analysis("yoga", "yoga_key"), timeout=5
    )
    assert result is None


@pytest.mark.asyncio
async def test_call_haiku_shop_analysis_no_api_key_returns_none(analyzer):
    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = ""
        result = await asyncio.wait_for(
            analyzer._call_haiku_shop_analysis("TestShop", {}, "home decor"), timeout=5
        )
    assert result is None


@pytest.mark.asyncio
async def test_synthesize_shop_gaps_no_api_key_returns_empty_string(analyzer):
    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = ""
        result = await asyncio.wait_for(
            analyzer._synthesize_shop_gaps([{"shop_name": "A"}], "yoga"), timeout=5
        )
    assert result == ""


@pytest.mark.asyncio
async def test_call_haiku_shop_analysis_with_mock_anthropic_returns_dict(analyzer):
    shop_result = {"shop_name": "TestShop", "threat_level": "low"}
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(shop_result))]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = "test-key"
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await asyncio.wait_for(
                analyzer._call_haiku_shop_analysis("TestShop", {}, "home decor"), timeout=5
            )

    assert result is not None
    assert result.get("shop_name") == "TestShop"
    assert result.get("threat_level") == "low"


@pytest.mark.asyncio
async def test_synthesize_shop_gaps_with_mock_anthropic_returns_string(analyzer):
    gap_text = "No bundle for ADHD moms. Gap: price €2-3."
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=gap_text)]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = "test-key"
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await asyncio.wait_for(
                analyzer._synthesize_shop_gaps([{"shop_name": "ShopA"}], "yoga"), timeout=5
            )

    assert gap_text in result


@pytest.mark.asyncio
async def test_get_competitor_shop_analysis_valid_cache_hit(analyzer):
    future = datetime.now(timezone.utc) + timedelta(days=10)
    cached_data = {"niche": "home decor", "shops": [], "gap_to_exploit": "test gap"}
    analyzer._memory.query_chromadb = AsyncMock(
        return_value=[{
            "document": json.dumps(cached_data),
            "metadata": {"cache_until": future.isoformat(), "type": "competitor_shop_analysis"},
        }]
    )

    result = await asyncio.wait_for(
        analyzer._get_competitor_shop_analysis("home decor", "home_decor"), timeout=5
    )

    assert result == cached_data


@pytest.mark.asyncio
async def test_get_competitor_shop_analysis_no_shop_names_returns_none(analyzer):
    analyzer._memory.query_chromadb = AsyncMock(return_value=[])

    with patch(
        "apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool"
    ) as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": []})
        result = await asyncio.wait_for(
            analyzer._get_competitor_shop_analysis("totally_unknown_niche_xyz", "key"), timeout=5
        )

    assert result is None


@pytest.mark.asyncio
async def test_call_haiku_shop_analysis_anthropic_exception_returns_none(analyzer):
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("API rate limit"))

    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = "test-key"
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await asyncio.wait_for(
                analyzer._call_haiku_shop_analysis("FailShop", {}, "yoga"), timeout=5
            )

    assert result is None


@pytest.mark.asyncio
async def test_synthesize_shop_gaps_anthropic_exception_returns_empty(analyzer):
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("network error"))

    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = "test-key"
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await asyncio.wait_for(
                analyzer._synthesize_shop_gaps([{"shop_name": "A"}], "yoga"), timeout=5
            )

    assert result == ""


@pytest.mark.asyncio
async def test_call_haiku_shop_analysis_invalid_json_returns_none(analyzer):
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="This is NOT valid JSON!!!")]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = "test-key"
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await asyncio.wait_for(
                analyzer._call_haiku_shop_analysis("TestShop", {}, "niche"), timeout=5
            )

    assert result is None


@pytest.mark.asyncio
async def test_synthesize_shop_gaps_result_truncated_to_300_chars(analyzer):
    long_text = "G" * 500
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=long_text)]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    with patch(_SHOP_SETTINGS_PATH) as ms:
        ms.ANTHROPIC_API_KEY = "test-key"
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await asyncio.wait_for(
                analyzer._synthesize_shop_gaps([{"shop_name": "A"}], "yoga"), timeout=5
            )

    assert len(result) <= 300


@pytest.mark.asyncio
async def test_get_competitor_shop_analysis_expired_cache_proceeds_to_refetch(analyzer):
    past = datetime.now(timezone.utc) - timedelta(days=5)
    stale_data = {"niche": "yoga", "shops": [], "gap_to_exploit": "old gap"}
    # First call: expired cache; second call: no research data
    analyzer._memory.query_chromadb = AsyncMock(
        side_effect=[
            [{"document": json.dumps(stale_data), "metadata": {"cache_until": past.isoformat()}}],
            [],
        ]
    )

    with patch(
        "apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool"
    ) as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": []})
        result = await asyncio.wait_for(
            analyzer._get_competitor_shop_analysis("yoga", "yoga_key"), timeout=5
        )

    assert result is None  # expired cache → no shops found → None


# ═══════════════════════════════════════════════════════════════════════════════
# _WarmupMixin — stub phase API contracts  (~14 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class FakeWarmup(_WarmupMixin):
    pass


@pytest.fixture
def warmup():
    w = FakeWarmup()
    w.memory = AsyncMock()
    w._llm = MagicMock()
    w._llm.ainvoke = AsyncMock(
        return_value=MagicMock(content='{"trends": [], "score": 0.5}')
    )
    w._pepe = AsyncMock()
    w.logger = logging.getLogger("test")
    return w


@pytest.mark.asyncio
async def test_run_warmup_returns_dict_with_style_guide(warmup):
    result = await asyncio.wait_for(warmup.run_warmup("party_printable"), timeout=5)
    assert isinstance(result, dict)
    assert "style_guide" in result


@pytest.mark.asyncio
async def test_run_warmup_style_guide_section_key_matches_input(warmup):
    result = await asyncio.wait_for(warmup.run_warmup("wedding_printable"), timeout=5)
    assert result["style_guide"]["section_key"] == "wedding_printable"


@pytest.mark.asyncio
async def test_run_warmup_style_guide_has_variant_priority(warmup):
    result = await asyncio.wait_for(warmup.run_warmup("party_printable"), timeout=5)
    assert "variant_priority" in result["style_guide"]


@pytest.mark.asyncio
async def test_run_warmup_style_guide_palettes_is_list(warmup):
    result = await asyncio.wait_for(warmup.run_warmup("party_printable"), timeout=5)
    assert isinstance(result["style_guide"]["palettes"], list)


@pytest.mark.asyncio
async def test_run_warmup_style_guide_cta_phrases_is_list(warmup):
    result = await asyncio.wait_for(warmup.run_warmup("party_printable"), timeout=5)
    assert isinstance(result["style_guide"]["cta_phrases"], list)


@pytest.mark.asyncio
async def test_run_warmup_style_guide_posting_frequency_is_positive_int(warmup):
    result = await asyncio.wait_for(warmup.run_warmup("party_printable"), timeout=5)
    freq = result["style_guide"]["posting_frequency_per_week"]
    assert isinstance(freq, int)
    assert freq > 0


@pytest.mark.asyncio
async def test_phase1_trends_returns_keywords_and_trending_topics(warmup):
    result = await asyncio.wait_for(warmup._phase1_trends("party_printable"), timeout=5)
    assert "keywords" in result
    assert "trending_topics" in result


@pytest.mark.asyncio
async def test_phase1_trends_keywords_and_topics_are_lists(warmup):
    result = await asyncio.wait_for(warmup._phase1_trends("yoga_printable"), timeout=5)
    assert isinstance(result["keywords"], list)
    assert isinstance(result["trending_topics"], list)


@pytest.mark.asyncio
async def test_phase2_competitor_pins_returns_pins_and_scoring(warmup):
    result = await asyncio.wait_for(
        warmup._phase2_competitor_pins("party_printable"), timeout=5
    )
    assert "pins" in result
    assert "scoring" in result


@pytest.mark.asyncio
async def test_phase2_competitor_pins_scoring_has_all_required_keys(warmup):
    result = await asyncio.wait_for(
        warmup._phase2_competitor_pins("party_printable"), timeout=5
    )
    scoring = result["scoring"]
    assert "lifestyle_pct" in scoring
    assert "flat_lay_pct" in scoring
    assert "palette" in scoring


@pytest.mark.asyncio
async def test_phase3_board_analysis_returns_boards_and_benchmark(warmup):
    result = await asyncio.wait_for(
        warmup._phase3_board_analysis("party_printable"), timeout=5
    )
    assert "boards" in result
    assert "benchmark" in result


@pytest.mark.asyncio
async def test_phase3_board_analysis_benchmark_has_required_keys(warmup):
    result = await asyncio.wait_for(
        warmup._phase3_board_analysis("wedding_printable"), timeout=5
    )
    benchmark = result["benchmark"]
    assert "avg_posts_per_week" in benchmark
    assert "avg_save_ratio" in benchmark


@pytest.mark.asyncio
async def test_phase4_test_pin_returns_image_path_score_and_variant(warmup):
    result = await asyncio.wait_for(
        warmup._phase4_test_pin("party_printable"), timeout=5
    )
    assert "image_path" in result
    assert "aesthetic_score" in result
    assert "variant" in result


@pytest.mark.asyncio
async def test_phase5_synthesize_returns_style_guide_with_correct_section_key(warmup):
    phases_data = {
        "trends": {"keywords": ["party"], "trending_topics": []},
        "competitor_pins": {
            "pins": [],
            "scoring": {"lifestyle_pct": 0.5, "flat_lay_pct": 0.5, "palette": []},
        },
        "board_analysis": {
            "boards": [],
            "benchmark": {"avg_posts_per_week": 3.0, "avg_save_ratio": 0.1},
        },
        "test_pin": {"image_path": "", "aesthetic_score": 0.8, "variant": "A"},
    }
    result = await asyncio.wait_for(
        warmup._phase5_synthesize("party_printable", phases_data), timeout=5
    )
    assert "style_guide" in result
    assert result["style_guide"]["section_key"] == "party_printable"
