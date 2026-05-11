# MOCK CONTRACT — Publisher
#
# _PublishMixin._extra_init_kwargs(self) → dict
#   mock: MagicMock with .storage, .etsy_api, ._telegram_broadcast attributes
#
# _PublishMixin._publish_single(self, file_path, product_type, template, niche,
#     color_scheme, keywords, size, ab_variant, pq_task_id, research_data,
#     thumbnail_paths_input=None, product_tier="core") → dict
#   mock: AsyncMock(return_value={"status":"published","listing_id":"123","images_uploaded":2})
#
# _PublishMixin._check_failure_history(self, niche, research_data) → dict
#   mock: AsyncMock(return_value={}) or AsyncMock(return_value={"failure_constraints_active":[...]})
#
# _PublishMixin._notify_telegram(self, message) → None
#   mock: AsyncMock()
#
# _PublishMixin._resolve_section_id(self, niche) → str | None
#   mock: AsyncMock(return_value="section_123") or AsyncMock(return_value=None)
#
# _PublishMixin._publish_via_etsy_api(self, kwargs, file_path, thumbs) → tuple[str,int]
#   mock: AsyncMock(return_value=("etsy_123", 2))
#
# _ResolveMixin._calculate_publish_confidence(self, results, task_context) → tuple[float, list[str]]
#   mock: MagicMock(return_value=(0.75, []))
#
# _ResolveMixin._calculate_status(self, results) → TaskStatus
#   mock: MagicMock(return_value=TaskStatus.COMPLETED)
#
# _ResolveMixin._resolve_price(self, file_type, research_data, variant="a") → float
#   mock: MagicMock(return_value=4.99)
#
# _SeoMixin._generate_seo(self, niche, template, keywords, color_scheme, size,
#     research_data, product_tier="core") → dict
#   mock: AsyncMock(return_value={"title":"...","description":"...","tags":[...],"seo_validated":True})
#
# _ThumbnailMixin._generate_mock_thumbnail(self, file_path, product_type, niche)
#     → tuple[bool, list[Path]]
#   mock: AsyncMock(return_value=(True, [Path("/tmp/thumb.png")]))
#
# _ThumbnailMixin._check_thumbnails(self, niche, file_type, pdf_path=None,
#     explicit_paths=None) → tuple[bool, list[Path]]
#   mock: AsyncMock(return_value=(True, [Path("/tmp/thumb.png")]))
#
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_publish_stub():
    """Stub for _PublishMixin tests — mocks all side-effect methods."""
    pub = MagicMock()
    pub.etsy_api = MagicMock()
    pub.etsy_api.mock_mode = True
    pub.memory = MagicMock()
    pub.memory.add_etsy_listing = AsyncMock()
    pub.memory.get_db = AsyncMock(return_value=MagicMock())
    pub.memory.query_chromadb_recent = AsyncMock(return_value=[])
    pub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[])
    pub._generate_mock_thumbnail = AsyncMock(return_value=(True, []))
    pub._check_failure_history = AsyncMock(return_value={})
    pub._generate_seo = AsyncMock(return_value={
        "title": "Test Printable Planner for Students | Undated Weekly",
        "description": "A lovely digital planner",
        "tags": ["planner"] * 13,
        "seo_validated": True,
    })
    pub._resolve_price = MagicMock(return_value=4.99)
    pub._resolve_section_id = AsyncMock(return_value=None)
    pub._dispatch_publish = AsyncMock(return_value=("listing_abc123", 2))
    pub._notify_telegram = AsyncMock()
    pub._get_when_made = MagicMock(return_value="made_to_order")
    pub._pinterest_agent = None
    pub._update_cluster_crossrefs = AsyncMock()
    pub.storage = MagicMock()
    pub._telegram_broadcast = AsyncMock()
    return pub


def _small_pdf(tmp_path: Path) -> str:
    p = tmp_path / "product.pdf"
    p.write_bytes(b"fake pdf content" * 100)
    return str(p)


def _valid_seo_response(n_words: int = 155) -> str:
    """Returns a JSON SEO response with enough words in description."""
    desc = "Great planner • item1 • item2 " + " ".join(["quality"] * n_words)
    return json.dumps({
        "title": "Amazing Digital Planner for Students | Study Tracker",
        "description": desc,
        "tags": [f"tag{i}" for i in range(13)],
    })


# ===========================================================================
# _ResolveMixin tests
# ===========================================================================

class TestCalculatePublishConfidence:
    from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin as _RM

    def setup_method(self):
        from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
        self.agent = _ResolveMixin()

    def test_perfect_research_all_published_with_images_and_seo(self):
        results = [
            {"listing_id": "1", "images_uploaded": 2, "seo_validated": True},
            {"listing_id": "2", "images_uploaded": 1, "seo_validated": True},
        ]
        ctx = {
            "etsy_tags_13": ["tag1"], "selling_signals": ["buy now"],
            "pricing": {"launch_price_usd": 4.99},
        }
        score, missing = self.agent._calculate_publish_confidence(results, ctx)
        assert score > 0.8
        assert missing == []

    def test_missing_all_research_fields(self):
        results = [{"listing_id": "1", "images_uploaded": 0, "seo_validated": False}]
        ctx = {}
        score, missing = self.agent._calculate_publish_confidence(results, ctx)
        assert "etsy_tags_13" in " ".join(missing)
        assert "selling_signals" in " ".join(missing)
        assert "pricing" in " ".join(missing)

    def test_empty_results_no_published(self):
        ctx = {"etsy_tags_13": ["t1"], "selling_signals": [1], "pricing": {"launch_price_usd": 3}}
        score, missing = self.agent._calculate_publish_confidence([], ctx)
        assert score < 0.5
        assert any("Nessun listing" in m for m in missing)

    def test_partial_success_rate(self):
        results = [
            {"listing_id": "1", "images_uploaded": 1, "seo_validated": False},
            {"listing_id": None, "images_uploaded": 0, "seo_validated": False},
        ]
        ctx = {}
        score, missing = self.agent._calculate_publish_confidence(results, ctx)
        assert any("falliti" in m for m in missing)

    def test_partial_thumbnails_adds_missing(self):
        results = [
            {"listing_id": "1", "images_uploaded": 0, "seo_validated": False},
            {"listing_id": "2", "images_uploaded": 0, "seo_validated": False},
        ]
        ctx = {"etsy_tags_13": ["t"], "selling_signals": [1], "pricing": {"launch_price_usd": 2}}
        score, missing = self.agent._calculate_publish_confidence(results, ctx)
        assert any("thumbnail" in m for m in missing)


class TestCalculateStatus:

    def setup_method(self):
        from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
        self.agent = _ResolveMixin()

    def test_empty_results_is_failed(self):
        assert self.agent._calculate_status([]).value == "failed"

    def test_all_success_is_completed(self):
        results = [{"listing_id": "1"}, {"listing_id": "2"}]
        assert self.agent._calculate_status(results).value == "completed"

    def test_exactly_half_is_partial(self):
        results = [{"listing_id": "1"}, {"listing_id": None}]
        assert self.agent._calculate_status(results).value == "partial"

    def test_above_half_is_partial(self):
        results = [{"listing_id": "1"}, {"listing_id": "2"}, {"listing_id": None}]
        assert self.agent._calculate_status(results).value == "partial"

    def test_below_half_is_failed(self):
        results = [{"listing_id": None}, {"listing_id": None}, {"listing_id": "1"}]
        assert self.agent._calculate_status(results).value == "failed"


class TestResolvePrice:

    def setup_method(self):
        from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
        self.agent = _ResolveMixin()

    def test_variant_a_uses_research_launch_price(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.settings") as s:
            s.ETSY_SHOP_CURRENCY = "EUR"
            s.USD_EUR_RATE = 0.92
            price = self.agent._resolve_price(
                "printable_pdf",
                {"pricing": {"launch_price_usd": "5.00"}},
                variant="a",
            )
        assert abs(price - round(5.0 * 0.92, 2)) < 0.001

    def test_variant_b_uses_research_mature_price(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.settings") as s:
            s.ETSY_SHOP_CURRENCY = "EUR"
            s.USD_EUR_RATE = 0.90
            price = self.agent._resolve_price(
                "printable_pdf",
                {"pricing": {"mature_price_usd": "8.00"}},
                variant="b",
            )
        assert abs(price - round(8.0 * 0.90, 2)) < 0.001

    def test_variant_a_fallback_when_no_research_pricing(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.settings") as s:
            s.ETSY_SHOP_CURRENCY = "EUR"
            price = self.agent._resolve_price("printable_pdf", {}, variant="a")
        assert price == 2.99  # AB_PRICES["printable_pdf"]["A"]

    def test_variant_b_fallback(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.settings") as s:
            s.ETSY_SHOP_CURRENCY = "EUR"
            price = self.agent._resolve_price("printable_pdf", {}, variant="b")
        assert price == 4.99

    def test_non_eur_currency_logs_warning(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.settings") as s, \
             patch("apps.backend.agents._publisher._resolve_mixin.logger") as mock_logger:
            s.ETSY_SHOP_CURRENCY = "USD"
            self.agent._resolve_price("printable_pdf", {}, variant="a")
        mock_logger.warning.assert_called_once()

    def test_unknown_file_type_falls_back_to_pdf(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.settings") as s:
            s.ETSY_SHOP_CURRENCY = "EUR"
            price = self.agent._resolve_price("unknown_type", {}, variant="a")
        assert price == 2.99  # printable_pdf fallback


def test_get_when_made_returns_made_to_order():
    from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
    agent = _ResolveMixin()
    assert agent._get_when_made() == "made_to_order"


class TestGetSeasonalContext:

    def setup_method(self):
        from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
        self.agent = _ResolveMixin()

    def test_january_is_new_year(self):
        import datetime
        with patch("apps.backend.agents._publisher._resolve_mixin.datetime") as mock_dt:
            mock_dt.datetime.now.return_value.month = 1
            result = self.agent._get_seasonal_context()
        assert result["season"] == "New Year"
        assert "planner" in " ".join(result["keywords"]).lower()

    def test_december_is_christmas(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.datetime") as mock_dt:
            mock_dt.datetime.now.return_value.month = 12
            result = self.agent._get_seasonal_context()
        assert result["season"] == "Christmas/Year End"

    def test_august_is_back_to_school(self):
        with patch("apps.backend.agents._publisher._resolve_mixin.datetime") as mock_dt:
            mock_dt.datetime.now.return_value.month = 8
            result = self.agent._get_seasonal_context()
        assert result["season"] == "Back to School"


# ===========================================================================
# _SeoMixin tests
# ===========================================================================

class FakeSeoAgent:
    """Real composition for SEO testing (avoids MagicMock attribute shadowing)."""
    def __init__(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin
        from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
        self.__class__ = type("_FakeSeoAgent", (_SeoMixin, _ResolveMixin), {})
        _SeoMixin.__init__ = lambda _: None  # type: ignore
        self._call_llm: AsyncMock = AsyncMock()


def _make_seo_agent():
    from apps.backend.agents._publisher._seo_mixin import _SeoMixin
    from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin

    class _FakeSeoAgent(_SeoMixin, _ResolveMixin):
        pass

    agent = _FakeSeoAgent()
    agent._call_llm = AsyncMock(return_value=_valid_seo_response())
    return agent


class TestBuildSeoSystemPrompt:

    def setup_method(self):
        self.agent = _make_seo_agent()

    def test_with_conversion_triggers_includes_language(self):
        result = self.agent._build_seo_system_prompt(
            selling_signals={"conversion_triggers": ["instant download", "limited time"],
                             "bundle_vs_single": "single",
                             "thumbnail_style": "clean mockup"},
            product_tier="core",
        )
        assert "instant download" in result

    def test_bundle_mode_includes_bundle_instruction(self):
        result = self.agent._build_seo_system_prompt(
            selling_signals={"conversion_triggers": [],
                             "bundle_vs_single": "bundle",
                             "thumbnail_style": "flat lay"},
            product_tier="bundle",
        )
        assert "bundle" in result.lower()

    def test_seasonal_keywords_included_when_present(self):
        with patch.object(self.agent, "_get_seasonal_context",
                          return_value={"season": "Spring", "keywords": ["spring refresh", "spring goals"]}):
            result = self.agent._build_seo_system_prompt(
                selling_signals={},
                product_tier="core",
            )
        assert "spring refresh" in result

    def test_no_conversion_triggers_no_language_block(self):
        result = self.agent._build_seo_system_prompt(
            selling_signals={"conversion_triggers": [], "bundle_vs_single": "single"},
            product_tier="core",
        )
        assert "LINGUAGGIO DI CONVERSIONE" not in result


class TestGenerateSeo:

    @pytest.mark.asyncio
    async def test_happy_path_returns_parsed_dict(self):
        agent = _make_seo_agent()
        agent._call_llm = AsyncMock(return_value=_valid_seo_response())
        result = await agent._generate_seo(
            niche="wellness planner",
            template="basic",
            keywords=["planner", "digital"],
            color_scheme="pastel",
            size="A4",
            research_data={},
        )
        assert "title" in result
        assert "description" in result
        assert "tags" in result

    @pytest.mark.asyncio
    async def test_with_etsy_tags_uses_them(self):
        agent = _make_seo_agent()
        tags = [f"etsy_tag_{i}" for i in range(13)]
        agent._call_llm = AsyncMock(return_value=_valid_seo_response())
        result = await agent._generate_seo(
            niche="study planner",
            template="focus",
            keywords=[],
            color_scheme="blue",
            size="Letter",
            research_data={"etsy_tags_13": tags, "selling_signals": {}},
        )
        assert result["tags"] == tags

    @pytest.mark.asyncio
    async def test_retry_on_bad_json_then_succeeds(self):
        agent = _make_seo_agent()
        agent._call_llm = AsyncMock(side_effect=[
            "not valid json at all",
            _valid_seo_response(),
        ])
        result = await agent._generate_seo(
            niche="fitness tracker",
            template="planner",
            keywords=[],
            color_scheme="green",
            size="A4",
            research_data={},
        )
        assert "title" in result

    @pytest.mark.asyncio
    async def test_raises_after_two_bad_json_attempts(self):
        agent = _make_seo_agent()
        agent._call_llm = AsyncMock(return_value="not json at all")
        with pytest.raises(RuntimeError, match="JSON SEO valido"):
            await agent._generate_seo(
                niche="budget planner",
                template="simple",
                keywords=[],
                color_scheme="gray",
                size="A4",
                research_data={},
            )


class TestParseSeoJson:

    def setup_method(self):
        self.agent = _make_seo_agent()

    def _call(self, text, etsy_tags_13=None):
        return self.agent._parse_seo_json(text, etsy_tags_13=etsy_tags_13)

    def test_valid_json_returns_dict(self):
        result = self._call(_valid_seo_response())
        assert result is not None
        assert result["seo_validated"] is True

    def test_invalid_json_returns_none(self):
        assert self._call("not json at all") is None

    def test_missing_required_fields_returns_none(self):
        assert self._call('{"title": "only title"}') is None

    def test_title_over_140_truncated(self):
        long_title = "A" * 150
        desc = "Good planner • item1 " + " ".join(["w"] * 155)
        text = json.dumps({"title": long_title, "description": desc,
                           "tags": [f"t{i}" for i in range(13)]})
        result = self._call(text)
        assert result is not None
        assert len(result["title"]) == 140
        assert "troncato" in " ".join(result["seo_issues"])

    def test_title_under_30_adds_issue(self):
        short_title = "Too short"
        desc = "Good planner • item1 " + " ".join(["w"] * 155)
        text = json.dumps({"title": short_title, "description": desc,
                           "tags": [f"t{i}" for i in range(13)]})
        result = self._call(text)
        assert result is not None
        assert result["seo_validated"] is False
        assert any("corto" in i for i in result["seo_issues"])

    def test_description_without_bullets_adds_issue(self):
        good_title = "A" * 50
        desc = " ".join(["word"] * 160)  # no bullet points
        text = json.dumps({"title": good_title, "description": desc,
                           "tags": [f"t{i}" for i in range(13)]})
        result = self._call(text)
        assert result is not None
        assert result["seo_validated"] is False
        assert any("bullet" in i for i in result["seo_issues"])

    def test_description_too_short_adds_issue(self):
        good_title = "A" * 50
        desc = "Short • desc"
        text = json.dumps({"title": good_title, "description": desc,
                           "tags": [f"t{i}" for i in range(13)]})
        result = self._call(text)
        assert result is not None
        assert result["seo_validated"] is False
        assert any("corta" in i for i in result["seo_issues"])

    def test_description_too_long_adds_issue_but_valid(self):
        good_title = "A" * 50
        desc = "Intro • item " + " ".join(["word"] * 310)
        text = json.dumps({"title": good_title, "description": desc,
                           "tags": [f"t{i}" for i in range(13)]})
        result = self._call(text)
        assert result is not None
        assert any("lunga" in i for i in result["seo_issues"])

    def test_etsy_tags_13_override_llm_tags(self):
        research_tags = [f"r_tag_{i}" for i in range(13)]
        result = self._call(_valid_seo_response(), etsy_tags_13=research_tags)
        assert result is not None
        assert result["tags"] == research_tags

    def test_long_etsy_tags_truncated_to_20_chars(self):
        long_tags = ["x" * 25 for _ in range(13)]
        result = self._call(_valid_seo_response(), etsy_tags_13=long_tags)
        assert result is not None
        for tag in result["tags"]:
            assert len(tag) <= 20
        assert any("troncati" in i for i in result["seo_issues"])

    def test_fewer_than_10_tags_without_research_adds_issue(self):
        good_title = "A" * 50
        desc = "Intro • item " + " ".join(["word"] * 155)
        text = json.dumps({"title": good_title, "description": desc,
                           "tags": ["t1", "t2", "t3"]})
        result = self._call(text, etsy_tags_13=None)
        assert result is not None
        assert result["seo_validated"] is False
        assert any("tag" in i for i in result["seo_issues"])

    def test_json_wrapped_in_code_block_extracted(self):
        payload = json.dumps({
            "title": "A" * 50,
            "description": "Intro • item " + " ".join(["word"] * 155),
            "tags": [f"t{i}" for i in range(13)],
        })
        wrapped = f"```json\n{payload}\n```"
        result = self._call(wrapped)
        assert result is not None


# ===========================================================================
# _ThumbnailMixin tests
# ===========================================================================

def _make_thumbnail_agent():
    from apps.backend.agents._publisher._thumbnail_mixin import _ThumbnailMixin

    class _FakeThumbAgent(_ThumbnailMixin):
        pass

    return _FakeThumbAgent()


class TestGenerateMockThumbnail:

    @pytest.mark.asyncio
    async def test_digital_art_png_existing_large_file_returned_directly(self, tmp_path):
        agent = _make_thumbnail_agent()
        png = tmp_path / "art.png"
        png.write_bytes(b"\x89PNG" + b"x" * 11_000)
        ok, paths = await agent._generate_mock_thumbnail(str(png), "digital_art_png", "abstract art")
        assert ok is True
        assert len(paths) == 1
        assert paths[0] == png

    @pytest.mark.asyncio
    async def test_digital_art_png_small_file_creates_mock(self, tmp_path):
        agent = _make_thumbnail_agent()
        png = tmp_path / "small.png"
        png.write_bytes(b"\x89PNG")  # too small
        ok, paths = await agent._generate_mock_thumbnail(str(png), "digital_art_png", "tiny art")
        assert ok is True
        assert len(paths) == 1
        assert paths[0].stat().st_size > 10_000

    @pytest.mark.asyncio
    async def test_printable_pdf_with_pillow_creates_thumbnail(self, tmp_path):
        agent = _make_thumbnail_agent()
        pdf = tmp_path / "product.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        ok, paths = await agent._generate_mock_thumbnail(str(pdf), "printable_pdf", "planner niche")
        assert ok is True
        assert len(paths) == 1
        assert paths[0].stat().st_size > 10_000

    @pytest.mark.asyncio
    async def test_printable_pdf_raw_png_fallback_when_pillow_fails(self, tmp_path):
        agent = _make_thumbnail_agent()
        pdf = tmp_path / "product.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None,
                                        "PIL.ImageDraw": None, "PIL.ImageFont": None}):
            ok, paths = await agent._generate_mock_thumbnail(str(pdf), "printable_pdf", "fallback test")
        assert ok is True
        assert len(paths) == 1
        assert paths[0].stat().st_size > 10_000

    @pytest.mark.asyncio
    async def test_no_file_path_creates_thumbnail_in_tmp(self, tmp_path, monkeypatch):
        """When file_path is empty string the else branch runs with /tmp fallback."""
        agent = _make_thumbnail_agent()
        ok, paths = await agent._generate_mock_thumbnail("", "printable_pdf", "no file niche")
        assert ok is True
        assert len(paths) == 1


class TestCheckThumbnails:

    @pytest.mark.asyncio
    async def test_explicit_paths_valid_returned(self, tmp_path):
        agent = _make_thumbnail_agent()
        thumb = tmp_path / "thumbnail_1.png"
        thumb.write_bytes(b"x" * 11_000)
        ok, paths = await agent._check_thumbnails(
            "my_niche", "printable_pdf", explicit_paths=[str(thumb)]
        )
        assert ok is True
        assert paths[0] == thumb

    @pytest.mark.asyncio
    async def test_explicit_paths_invalid_falls_through(self, tmp_path):
        """If explicit_paths are provided but too small, fall through to pdf_dir search."""
        agent = _make_thumbnail_agent()
        small_thumb = tmp_path / "thumbnail_small.png"
        small_thumb.write_bytes(b"tiny")
        # Also place a valid thumbnail in the pdf dir
        pdf = tmp_path / "product.pdf"
        pdf.write_bytes(b"%PDF")
        good_thumb = tmp_path / "thumbnail_valid.png"
        good_thumb.write_bytes(b"x" * 11_000)
        ok, paths = await agent._check_thumbnails(
            "my_niche", "printable_pdf",
            pdf_path=str(pdf),
            explicit_paths=[str(small_thumb)],
        )
        assert ok is True
        assert good_thumb in paths

    @pytest.mark.asyncio
    async def test_pdf_dir_thumbnails_found(self, tmp_path):
        agent = _make_thumbnail_agent()
        pdf = tmp_path / "product.pdf"
        pdf.write_bytes(b"%PDF")
        thumb = tmp_path / "thumbnail_preview.png"
        thumb.write_bytes(b"x" * 11_000)
        ok, paths = await agent._check_thumbnails(
            "my_niche", "printable_pdf", pdf_path=str(pdf)
        )
        assert ok is True
        assert thumb in paths

    @pytest.mark.asyncio
    async def test_no_thumbnails_returns_false(self, tmp_path):
        agent = _make_thumbnail_agent()
        pdf = tmp_path / "product.pdf"
        pdf.write_bytes(b"%PDF")
        ok, paths = await agent._check_thumbnails(
            "obscure_niche_xyz_abc", "printable_pdf", pdf_path=str(pdf)
        )
        assert ok is False
        assert paths == []


# ===========================================================================
# _PublishMixin tests
# ===========================================================================

def test_extra_init_kwargs_returns_expected_keys():
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin
    stub = MagicMock()
    stub.storage = MagicMock()
    stub.etsy_api = MagicMock()
    stub._telegram_broadcast = AsyncMock()
    result = _PublishMixin._extra_init_kwargs(stub)
    assert "storage" in result
    assert "etsy_api" in result
    assert "telegram_broadcaster" in result
    assert result["storage"] is stub.storage


class TestPublishSinglePaths:

    @pytest.mark.asyncio
    async def test_file_too_large_returns_skipped(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pdf = tmp_path / "huge.pdf"
        pdf.write_bytes(b"x")

        with patch("pathlib.Path.stat") as mock_stat:
            stat_res = MagicMock()
            stat_res.st_size = 21 * 1024 * 1024  # 21 MB
            mock_stat.return_value = stat_res
            result = await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=str(pdf), product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

        assert result["status"] == "skipped_file_too_large"
        assert "20MB" in result["error"]

    @pytest.mark.asyncio
    async def test_non_mock_thumbnail_fail_returns_skipped(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub.etsy_api.mock_mode = False
        pub._check_thumbnails = AsyncMock(return_value=(False, []))
        pdf = _small_pdf(tmp_path)

        result = await asyncio.wait_for(
            _PublishMixin._publish_single(
                pub, file_path=pdf, product_type="printable_pdf",
                template="basic", niche="planner", color_scheme="blue",
                keywords=[], size="A4", ab_variant="A",
                pq_task_id=None, research_data={},
            ), timeout=10,
        )

        assert result["status"] == "skipped_no_thumbnails"

    @pytest.mark.asyncio
    async def test_non_mock_mode_success_path(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub.etsy_api.mock_mode = False
        good_thumb = tmp_path / "thumb.png"
        good_thumb.write_bytes(b"x" * 11_000)
        pub._check_thumbnails = AsyncMock(return_value=(True, [good_thumb]))
        pdf = _small_pdf(tmp_path)

        result = await asyncio.wait_for(
            _PublishMixin._publish_single(
                pub, file_path=pdf, product_type="printable_pdf",
                template="basic", niche="planner", color_scheme="blue",
                keywords=[], size="A4", ab_variant="A",
                pq_task_id=None, research_data={},
            ), timeout=10,
        )

        assert result["status"] == "published"

    @pytest.mark.asyncio
    async def test_failure_adjustments_stored_in_result(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._check_failure_history = AsyncMock(return_value={
            "failure_constraints_active": ["price too high"]
        })
        pdf = _small_pdf(tmp_path)

        result = await asyncio.wait_for(
            _PublishMixin._publish_single(
                pub, file_path=pdf, product_type="printable_pdf",
                template="basic", niche="planner", color_scheme="blue",
                keywords=[], size="A4", ab_variant="A",
                pq_task_id=None, research_data={},
            ), timeout=10,
        )

        assert "failure_adjustments" in result
        assert result["failure_adjustments"]["failure_constraints_active"] == ["price too high"]

    @pytest.mark.asyncio
    async def test_seo_issues_stored_in_result(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._generate_seo = AsyncMock(return_value={
            "title": "Test Planner for Students | Weekly Undated",
            "description": "A lovely planner",
            "tags": ["p"] * 13,
            "seo_validated": False,
            "seo_issues": ["title too short", "no bullets"],
        })
        pdf = _small_pdf(tmp_path)

        result = await asyncio.wait_for(
            _PublishMixin._publish_single(
                pub, file_path=pdf, product_type="printable_pdf",
                template="basic", niche="planner", color_scheme="blue",
                keywords=[], size="A4", ab_variant="A",
                pq_task_id=None, research_data={},
            ), timeout=10,
        )

        assert "seo_issues" in result

    @pytest.mark.asyncio
    async def test_taxonomy_id_zero_raises_runtime_error(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pdf = _small_pdf(tmp_path)

        with pytest.raises(RuntimeError, match="TAXONOMY_IDS"):
            await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="svg_bundle",
                    template="basic", niche="svg_niche", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

    @pytest.mark.asyncio
    async def test_section_id_set_in_listing_kwargs(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._resolve_section_id = AsyncMock(return_value="42")
        pdf = _small_pdf(tmp_path)

        with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_s:
            mock_s.PUBLISHER_DELIVERY_METHOD = "csv_export"
            result = await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

        assert result.get("section_id") == "42"
        # Verify _dispatch_publish received shop_section_id
        call_kwargs = pub._dispatch_publish.call_args[0][0]
        assert call_kwargs.get("shop_section_id") == 42  # converted via int()

    @pytest.mark.asyncio
    async def test_section_id_non_digit_stays_as_string(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._resolve_section_id = AsyncMock(return_value="section-abc")
        pdf = _small_pdf(tmp_path)

        with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_s:
            mock_s.PUBLISHER_DELIVERY_METHOD = "csv_export"
            result = await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

        call_kwargs = pub._dispatch_publish.call_args[0][0]
        assert call_kwargs.get("shop_section_id") == "section-abc"

    @pytest.mark.asyncio
    async def test_missing_listing_id_raises_runtime_error(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._dispatch_publish = AsyncMock(return_value=(None, 0))
        pdf = _small_pdf(tmp_path)

        with pytest.raises(RuntimeError, match="listing_id"):
            await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

    @pytest.mark.asyncio
    async def test_etsy_api_delivery_calls_update_section_count(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._resolve_section_id = AsyncMock(return_value="99")
        mock_ess = MagicMock()
        mock_ess.update_section_listing_count = AsyncMock()
        pdf = _small_pdf(tmp_path)

        with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_s, \
             patch("apps.backend.agents._publisher._publish_mixin.EtsySectionsService",
                   return_value=mock_ess):
            mock_s.PUBLISHER_DELIVERY_METHOD = "etsy_api"
            await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

        mock_ess.update_section_listing_count.assert_called_once_with("99", "listing_abc123")

    @pytest.mark.asyncio
    async def test_etsy_section_update_exception_is_swallowed(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._resolve_section_id = AsyncMock(return_value="99")
        mock_ess = MagicMock()
        mock_ess.update_section_listing_count = AsyncMock(side_effect=Exception("db error"))
        pdf = _small_pdf(tmp_path)

        with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_s, \
             patch("apps.backend.agents._publisher._publish_mixin.EtsySectionsService",
                   return_value=mock_ess):
            mock_s.PUBLISHER_DELIVERY_METHOD = "etsy_api"
            result = await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id=None, research_data={},
                ), timeout=10,
            )

        assert result["status"] == "published"

    @pytest.mark.asyncio
    async def test_seo_issues_appear_in_telegram_message(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pub._generate_seo = AsyncMock(return_value={
            "title": "Test Planner for Students | Weekly",
            "description": "A great product",
            "tags": ["p"] * 13,
            "seo_validated": False,
            "seo_issues": ["title too short", "no bullets"],
        })
        pdf = _small_pdf(tmp_path)

        await asyncio.wait_for(
            _PublishMixin._publish_single(
                pub, file_path=pdf, product_type="printable_pdf",
                template="basic", niche="planner", color_scheme="blue",
                keywords=[], size="A4", ab_variant="A",
                pq_task_id=None, research_data={},
            ), timeout=10,
        )

        call_args = pub._notify_telegram.call_args[0][0]
        assert "SEO issues" in call_args

    @pytest.mark.asyncio
    async def test_cross_ref_exception_is_logged_not_raised(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        pub = _make_publish_stub()
        pdf = _small_pdf(tmp_path)

        with patch("apps.backend.agents._publisher._publish_mixin._PQService") as mock_pqs:
            mock_pqs.return_value.get_item_by_task_id = AsyncMock(
                side_effect=Exception("DB unavailable")
            )
            result = await asyncio.wait_for(
                _PublishMixin._publish_single(
                    pub, file_path=pdf, product_type="printable_pdf",
                    template="basic", niche="planner", color_scheme="blue",
                    keywords=[], size="A4", ab_variant="A",
                    pq_task_id="task_xyz_001", research_data={},
                ), timeout=10,
            )

        assert result["status"] == "published"


class TestCheckFailureHistory:

    @pytest.mark.asyncio
    async def test_empty_research_empty_chromadb_returns_empty_dict(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(return_value=[])
        stub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[])

        result = await _PublishMixin._check_failure_history(stub, "planner_niche", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_failure_analysis_in_research_added_to_adjustments(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(return_value=[])
        stub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[])

        result = await _PublishMixin._check_failure_history(stub, "fitness_planner", {
            "failure_analysis_applied": True,
            "failure_reasons": ["price_too_high", "weak_seo"],
        })

        assert "failure_constraints_active" in result
        assert "price_too_high" in result["failure_constraints_active"]

    @pytest.mark.asyncio
    async def test_chromadb_failures_included(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(side_effect=[
            [  # first call: failures
                {"document": "listing failed", "metadata": {"failure_type": "zero_sales"}},
            ],
            [],  # second call: successes
        ])
        stub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[])

        result = await _PublishMixin._check_failure_history(stub, "wedding_planner", {})

        assert "chromadb_failures" in result
        assert result["chromadb_failures"][0]["failure_type"] == "zero_sales"

    @pytest.mark.asyncio
    async def test_chromadb_successes_included(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(side_effect=[
            [],  # failures
            [  # successes
                {"document": "top seller", "metadata": {"niche": "wedding_planner"}},
            ],
        ])
        stub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[])

        result = await _PublishMixin._check_failure_history(stub, "wedding_planner", {})

        assert "chromadb_successes" in result
        assert result["chromadb_successes"][0]["niche"] == "wedding_planner"

    @pytest.mark.asyncio
    async def test_similar_stale_listings_trigger_warning(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(return_value=[])
        stub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[
            {"niche": "fitness planner daily", "price_eur": 3.99, "views": 120},
            {"niche": "unrelated cooking book", "price_eur": 5.99, "views": 80},
        ])

        result = await _PublishMixin._check_failure_history(
            stub, "fitness planner weekly", {}
        )

        assert "similar_failures" in result
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_chromadb_exception_handled_gracefully(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(
            side_effect=Exception("ChromaDB unavailable")
        )
        stub.memory.get_stale_listings_without_sales = AsyncMock(return_value=[])

        # Should not raise
        result = await _PublishMixin._check_failure_history(stub, "planner", {})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_analytics_exception_handled_gracefully(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.query_chromadb_recent = AsyncMock(return_value=[])
        stub.memory.get_stale_listings_without_sales = AsyncMock(
            side_effect=Exception("Analytics DB error")
        )

        result = await _PublishMixin._check_failure_history(stub, "planner", {})
        assert isinstance(result, dict)


class TestNotifyTelegram:

    @pytest.mark.asyncio
    async def test_sends_message_when_broadcast_callable(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        mock_broadcast = AsyncMock()
        stub._telegram_broadcast = mock_broadcast

        await _PublishMixin._notify_telegram(stub, "Hello Telegram!")
        mock_broadcast.assert_called_once_with("Hello Telegram!")

    @pytest.mark.asyncio
    async def test_exception_in_broadcast_is_swallowed(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub._telegram_broadcast = AsyncMock(side_effect=Exception("network error"))

        # Should not raise
        await _PublishMixin._notify_telegram(stub, "Will fail silently")

    @pytest.mark.asyncio
    async def test_noop_when_broadcast_is_none(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub._telegram_broadcast = None

        # Should not raise, nothing happens
        await _PublishMixin._notify_telegram(stub, "No-op message")


class TestResolveSectionIdException:

    @pytest.mark.asyncio
    async def test_exception_in_section_service_returns_none(self):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub.memory.get_db = AsyncMock(side_effect=Exception("DB unavailable"))

        result = await asyncio.wait_for(
            _PublishMixin._resolve_section_id(stub, "some_niche"),
            timeout=5,
        )

        assert result is None


class TestPublishViaEtsyApi:

    @pytest.mark.asyncio
    async def test_happy_path_returns_listing_id_and_upload_count(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub._call_tool = AsyncMock(side_effect=[
            {"listing_id": "etsy_9876"},  # create_listing
            None,                          # upload_file
            None,                          # upload_image thumb 1
        ])

        thumb = tmp_path / "thumb.png"
        thumb.write_bytes(b"x" * 100)

        listing_id, count = await asyncio.wait_for(
            _PublishMixin._publish_via_etsy_api(
                stub,
                {"title": "Test", "price": 3.99, "tags": ["t1"]},
                str(tmp_path / "product.pdf"),
                [thumb],
            ), timeout=5,
        )

        assert listing_id == "etsy_9876"
        assert count == 1

    @pytest.mark.asyncio
    async def test_empty_listing_id_raises_runtime_error(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub._call_tool = AsyncMock(return_value={"listing_id": ""})

        with pytest.raises(RuntimeError, match="listing_id"):
            await asyncio.wait_for(
                _PublishMixin._publish_via_etsy_api(
                    stub,
                    {"title": "Test", "price": 3.99, "tags": []},
                    str(tmp_path / "product.pdf"),
                    [],
                ), timeout=5,
            )

    @pytest.mark.asyncio
    async def test_thumbnail_upload_failure_continues_and_logs(self, tmp_path):
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin
        stub = MagicMock()
        stub._call_tool = AsyncMock(side_effect=[
            {"listing_id": "etsy_1234"},  # create_listing
            None,                          # upload_file
            Exception("upload failed"),    # first thumbnail fails
            None,                          # second thumbnail ok
        ])

        thumb1 = tmp_path / "thumb1.png"
        thumb1.write_bytes(b"x" * 100)
        thumb2 = tmp_path / "thumb2.png"
        thumb2.write_bytes(b"x" * 100)

        listing_id, count = await asyncio.wait_for(
            _PublishMixin._publish_via_etsy_api(
                stub,
                {"title": "Test", "price": 3.99, "tags": []},
                str(tmp_path / "product.pdf"),
                [thumb1, thumb2],
            ), timeout=5,
        )

        assert listing_id == "etsy_1234"
        assert count == 1  # only second thumb succeeded
