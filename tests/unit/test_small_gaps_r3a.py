"""tests/unit/test_small_gaps_r3a.py

Covers residual coverage gaps in 4 files:
  - apps/backend/core/printify_client.py       (0%  → ≥85%)
  - apps/backend/agents/design.py              (77% → lines 89-101, 104)
  - apps/backend/agents/pinterest.py           (80% → lines 53-61, 64, 85-86)
  - apps/backend/core/_startup/_agents.py      (71% → lines 21-46, 51-67, 72-76)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# SEZIONE 1 — PrintifyClient
#
# MOCK CONTRACT:
#   No HTTP client — PrintifyClient is a pure stub.
#   All async methods unconditionally raise NotImplementedError.
#   __init__: sets _api_key; logs WARNING when api_key is empty.
# ═══════════════════════════════════════════════════════════════════════════════

from apps.backend.core.printify_client import PrintifyClient


class TestPrintifyClientInit:
    def test_pc1_init_with_valid_key_sets_attribute(self):
        """Happy path: valid api_key stored, no warning emitted."""
        client = PrintifyClient(api_key="pk_live_abc123")
        assert client._api_key == "pk_live_abc123"

    def test_pc2_init_empty_key_logs_warning(self, caplog):
        """Empty api_key triggers a WARNING about PRINTIFY_API_KEY."""
        import logging

        with caplog.at_level(logging.WARNING, logger="agentpexi.printify_client"):
            client = PrintifyClient(api_key="")

        assert client._api_key == ""
        assert "PRINTIFY_API_KEY" in caplog.text

    def test_pc3_init_default_no_key_logs_warning(self, caplog):
        """Default constructor (no arg) also triggers the same WARNING."""
        import logging

        with caplog.at_level(logging.WARNING, logger="agentpexi.printify_client"):
            client = PrintifyClient()

        assert client._api_key == ""
        assert "PRINTIFY_API_KEY" in caplog.text


class TestPrintifyClientMethods:
    """PC4-PC8 — every async method is a stub; each must raise NotImplementedError."""

    async def test_pc4_create_product_raises(self):
        client = PrintifyClient(api_key="x")
        with pytest.raises(NotImplementedError):
            await asyncio.wait_for(
                client.create_product("wedding", "https://img.example/art.png", "pod_print"),
                timeout=5,
            )

    async def test_pc5_get_product_status_raises(self):
        client = PrintifyClient(api_key="x")
        with pytest.raises(NotImplementedError):
            await asyncio.wait_for(client.get_product_status("prod-001"), timeout=5)

    async def test_pc6_list_products_raises(self):
        client = PrintifyClient(api_key="x")
        with pytest.raises(NotImplementedError):
            await asyncio.wait_for(client.list_products(), timeout=5)

    async def test_pc7_update_product_raises(self):
        client = PrintifyClient(api_key="x")
        with pytest.raises(NotImplementedError):
            await asyncio.wait_for(
                client.update_product("prod-001", {"title": "New title"}), timeout=5
            )

    async def test_pc8_delete_product_raises(self):
        client = PrintifyClient(api_key="x")
        with pytest.raises(NotImplementedError):
            await asyncio.wait_for(client.delete_product("prod-001"), timeout=5)


# ═══════════════════════════════════════════════════════════════════════════════
# SEZIONE 2 — DesignAgent lines 89-101, 104
#
# MOCK CONTRACT:
#   anthropic_client         → AsyncMock()
#   memory                   → AsyncMock()
#   storage                  → MagicMock()
#   apps.backend.agents.design.PDFGenerator          → MagicMock()
#   apps.backend.agents.design.create_image_generator → MagicMock()
#   apps.backend.agents.design.SVGGenerator          → MagicMock()
#
# The three generator patches prevent real I/O during __init__ (lines 98-100).
# ═══════════════════════════════════════════════════════════════════════════════


class TestDesignAgentInit:
    @patch("apps.backend.agents.design.SVGGenerator")
    @patch("apps.backend.agents.design.create_image_generator")
    @patch("apps.backend.agents.design.PDFGenerator")
    def test_da1_init_sets_all_attributes(self, MockPDF, MockImgGen, MockSVG):
        """Lines 89-101: __init__ stores storage, broadcasters and generators."""
        from apps.backend.agents.design import DesignAgent

        mock_storage = MagicMock()
        mock_tg = AsyncMock()
        mock_get_mock = MagicMock(return_value=True)

        agent = DesignAgent(
            anthropic_client=AsyncMock(),
            memory=AsyncMock(),
            storage=mock_storage,
            ws_broadcaster=AsyncMock(),
            telegram_broadcaster=mock_tg,
            get_mock_mode=mock_get_mock,
        )

        assert agent.storage is mock_storage
        assert agent._telegram_broadcast is mock_tg
        assert agent._get_mock_mode is mock_get_mock
        assert agent._pdf_gen is MockPDF.return_value
        assert agent._image_gen is MockImgGen.return_value
        assert agent._svg_gen is MockSVG.return_value

    @patch("apps.backend.agents.design.SVGGenerator")
    @patch("apps.backend.agents.design.create_image_generator")
    @patch("apps.backend.agents.design.PDFGenerator")
    def test_da2_get_mock_mode_defaults_to_false_lambda(self, MockPDF, MockImgGen, MockSVG):
        """Line 101: when get_mock_mode is omitted the default lambda returns False."""
        from apps.backend.agents.design import DesignAgent

        agent = DesignAgent(
            anthropic_client=AsyncMock(),
            memory=AsyncMock(),
            storage=MagicMock(),
        )

        assert agent._get_mock_mode() is False

    @patch("apps.backend.agents.design.SVGGenerator")
    @patch("apps.backend.agents.design.create_image_generator")
    @patch("apps.backend.agents.design.PDFGenerator")
    def test_da3_extra_init_kwargs_returns_storage_and_mock_mode(
        self, MockPDF, MockImgGen, MockSVG
    ):
        """Line 104: _extra_init_kwargs exposes storage and get_mock_mode."""
        from apps.backend.agents.design import DesignAgent

        mock_storage = MagicMock()
        agent = DesignAgent(
            anthropic_client=AsyncMock(),
            memory=AsyncMock(),
            storage=mock_storage,
        )

        kwargs = agent._extra_init_kwargs()

        assert "storage" in kwargs
        assert kwargs["storage"] is mock_storage
        assert "get_mock_mode" in kwargs
        assert callable(kwargs["get_mock_mode"])


# ═══════════════════════════════════════════════════════════════════════════════
# SEZIONE 3 — PinterestAgent lines 53-61, 64, 85-86
#
# MOCK CONTRACT:
#   anthropic_client  → AsyncMock()
#   memory            → AsyncMock()
#   telegram_broadcaster → AsyncMock()
#   pinterest_api     → MagicMock()
#
# Lines 53-61  — PinterestAgent.__init__ body (super() call + attribute stores)
# Line  64     — _extra_init_kwargs return value
# Lines 85-86  — run() fallback branch for unknown action
# ═══════════════════════════════════════════════════════════════════════════════


class TestPinterestAgentInit:
    def test_pa1_init_stores_telegram_and_api(self):
        """Lines 53-61: __init__ stores _telegram_broadcast and pinterest_api."""
        from apps.backend.agents.pinterest import PinterestAgent

        mock_tg = AsyncMock()
        mock_api = MagicMock()

        agent = PinterestAgent(
            anthropic_client=AsyncMock(),
            memory=AsyncMock(),
            ws_broadcaster=AsyncMock(),
            telegram_broadcaster=mock_tg,
            pinterest_api=mock_api,
        )

        assert agent._telegram_broadcast is mock_tg
        assert agent.pinterest_api is mock_api
        assert agent.name == "pinterest"

    def test_pa2_extra_init_kwargs_exposes_pinterest_api(self):
        """Line 64: _extra_init_kwargs returns {'pinterest_api': ...}."""
        from apps.backend.agents.pinterest import PinterestAgent

        mock_api = MagicMock()
        agent = PinterestAgent(
            anthropic_client=AsyncMock(),
            memory=AsyncMock(),
            pinterest_api=mock_api,
        )

        kwargs = agent._extra_init_kwargs()

        assert "pinterest_api" in kwargs
        assert kwargs["pinterest_api"] is mock_api

    async def test_pa3_run_unknown_action_returns_unsupported_message(self):
        """Lines 85-86: unsupported action → COMPLETED with 'non supportata' in output."""
        from apps.backend.agents.pinterest import PinterestAgent
        from apps.backend.core.models import AgentTask, TaskStatus

        agent = PinterestAgent(
            anthropic_client=AsyncMock(),
            memory=AsyncMock(),
        )
        task = AgentTask(agent_name="pinterest", input_data={"action": "fly_to_moon"})

        result = await asyncio.wait_for(agent.run(task), timeout=5)

        assert result.status == TaskStatus.COMPLETED
        assert "non supportata" in result.output_data.get("message", "")
        assert "fly_to_moon" in result.output_data.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════════
# SEZIONE 4 — _startup/_agents.py lines 21-46, 51-67, 72-76
#
# MOCK CONTRACT:
#   apps.backend.core.pepe.Pepe                  → MagicMock class
#     instance.client       = MagicMock()
#     instance.get_mock_mode = MagicMock(return_value=False)
#     instance.register_agent = MagicMock()
#     instance.start        = AsyncMock()
#   apps.backend.agents.research.ResearchAgent   → MagicMock class
#   apps.backend.agents.design.DesignAgent       → MagicMock class
#   apps.backend.core.wiki.WikiManager           → MagicMock class
#     instance.init = AsyncMock()
#   apps.backend.tools.etsy_api.EtsyAPI          → MagicMock class
#
# All classes are imported dynamically inside each init_* function, so the
# patches replace the attribute on the already-loaded module — the local
# `from X import Y` inside the function picks up the mock at call time.
# ═══════════════════════════════════════════════════════════════════════════════

from apps.backend.core._startup._agents import init_etsy, init_pepe, init_wiki
from apps.backend.core._startup._models import _PepeBundle


class TestInitPepe:
    @patch("apps.backend.agents.design.DesignAgent")      # innermost → MockDesign
    @patch("apps.backend.agents.research.ResearchAgent")  # middle    → MockResearch
    @patch("apps.backend.core.pepe.Pepe")                 # outermost → MockPepe
    async def test_ag1_init_pepe_returns_pepebundle(self, MockPepe, MockResearch, MockDesign):
        """Lines 21-46: init_pepe creates Pepe + two agents, starts Pepe, returns _PepeBundle."""
        mock_pepe_inst = MagicMock()
        mock_pepe_inst.client = MagicMock()
        mock_pepe_inst.get_mock_mode = MagicMock(return_value=False)
        mock_pepe_inst.register_agent = MagicMock()
        mock_pepe_inst.start = AsyncMock()
        MockPepe.return_value = mock_pepe_inst

        bundle = await asyncio.wait_for(
            init_pepe(AsyncMock(), MagicMock(), AsyncMock(), AsyncMock()),
            timeout=5,
        )

        assert bundle is not None
        assert isinstance(bundle, _PepeBundle)
        assert bundle.pepe is mock_pepe_inst
        assert bundle.research_agent is MockResearch.return_value
        assert bundle.design_agent is MockDesign.return_value
        mock_pepe_inst.start.assert_called_once()
        assert mock_pepe_inst.register_agent.call_count == 2


class TestInitWiki:
    @patch("apps.backend.core.wiki.WikiManager")
    async def test_ag2_init_wiki_success_assigns_to_pepe(self, MockWikiManager):
        """Lines 51-64: successful init → pepe.wiki = wiki_manager instance."""
        mock_wiki = MagicMock()
        mock_wiki.init = AsyncMock()
        MockWikiManager.return_value = mock_wiki

        mock_pepe = MagicMock()
        mock_settings = MagicMock()
        mock_settings.WIKI_BASE_PATH = "/tmp/test_wiki_agentpexi_r3a"

        await asyncio.wait_for(init_wiki(mock_pepe, mock_settings), timeout=5)

        assert mock_pepe.wiki is mock_wiki
        mock_wiki.init.assert_called_once()

    @patch("apps.backend.core.wiki.WikiManager")
    async def test_ag2b_init_wiki_failsafe_sets_none(self, MockWikiManager):
        """Lines 65-67: WikiManager failure → fail-safe, pepe.wiki = None."""
        MockWikiManager.side_effect = Exception("wiki explosion")

        mock_pepe = MagicMock()
        mock_settings = MagicMock()
        mock_settings.WIKI_BASE_PATH = "/tmp/test_wiki_agentpexi_r3a"

        await asyncio.wait_for(init_wiki(mock_pepe, mock_settings), timeout=5)

        assert mock_pepe.wiki is None


class TestInitEtsy:
    @patch("apps.backend.tools.etsy_api.EtsyAPI")
    async def test_ag3_init_etsy_returns_api_instance(self, MockEtsyAPI):
        """Lines 72-76: init_etsy creates EtsyAPI(memory=..., pepe=...) and returns it."""
        mock_etsy = MagicMock()
        MockEtsyAPI.return_value = mock_etsy

        memory = AsyncMock()
        mock_pepe = MagicMock()

        result = await asyncio.wait_for(init_etsy(memory, mock_pepe), timeout=5)

        assert result is mock_etsy
        MockEtsyAPI.assert_called_once_with(memory=memory, pepe=mock_pepe)
