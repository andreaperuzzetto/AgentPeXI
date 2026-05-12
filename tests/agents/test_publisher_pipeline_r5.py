"""Round-5 coverage — PublisherAgent.run(), PipelineMixin, DispatchMixin.

Covers lines NOT already tested in:
  - tests/agents/test_publisher_coverage.py  (77 tests)
  - tests/core/test_pepe_coverage.py
  - tests/test_aa_pipeline_pure.py
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.agents.publisher import PublisherAgent
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tmp_file(suffix: str = ".pdf") -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.write(b"dummy content")
    f.close()
    return f.name


def make_task(file_paths: list[str], **kwargs) -> AgentTask:
    return AgentTask(
        task_id="t1",
        agent_name="publisher",
        input_data={
            "file_paths": file_paths,
            "niche": "mandala",
            "product_type": "printable_pdf",
            **kwargs,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — PublisherAgent.run()  (~19 tests)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def agent():
    a = PublisherAgent.__new__(PublisherAgent)
    a.name = "publisher"
    a.storage = MagicMock()
    a.storage.is_available.return_value = True
    a.storage.move_to_uploaded = MagicMock()
    a.memory = AsyncMock()
    a.memory.get_etsy_listings_count = AsyncMock(return_value=0)
    a.memory.get_db = AsyncMock(return_value=AsyncMock())
    a._publish_single = AsyncMock(return_value={
        "niche": "mandala",
        "file_type": "printable_pdf",
        "status": "completed",
        "listing_id": "L123",
        "images_uploaded": 3,
        "seo_validated": True,
        "ab_variant": "A",
    })
    a._calculate_publish_confidence = MagicMock(return_value=(0.9, []))
    a._calculate_status = MagicMock(return_value=TaskStatus.COMPLETED)
    a._publish_lock = asyncio.Lock()
    return a


class TestPublisherRun:

    @pytest.mark.asyncio
    async def test_storage_unavailable_raises_runtime_error(self, agent):
        agent.storage.is_available.return_value = False
        with pytest.raises(RuntimeError, match="Storage non disponibile"):
            await asyncio.wait_for(agent.run(make_task([])), timeout=5)

    @pytest.mark.asyncio
    async def test_empty_file_paths_raises_runtime_error(self, agent):
        with pytest.raises(RuntimeError, match="Nessun file valido"):
            await asyncio.wait_for(agent.run(make_task([])), timeout=5)

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises_runtime_error(self, agent):
        with pytest.raises(RuntimeError, match="Nessun file valido"):
            await asyncio.wait_for(
                agent.run(make_task(["/no/such/path_xyz_abc_123.pdf"])), timeout=5
            )

    @pytest.mark.asyncio
    async def test_one_valid_file_returns_agent_result(self, agent):
        fp = _tmp_file()
        try:
            result = await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            assert isinstance(result, AgentResult)
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_three_files_calls_publish_single_three_times(self, agent):
        fps = [_tmp_file() for _ in range(3)]
        try:
            await asyncio.wait_for(agent.run(make_task(fps)), timeout=5)
            assert agent._publish_single.call_count == 3
        finally:
            for fp in fps:
                os.unlink(fp)

    @pytest.mark.asyncio
    async def test_max_five_files_processed_from_seven(self, agent):
        fps = [_tmp_file() for _ in range(7)]
        try:
            await asyncio.wait_for(agent.run(make_task(fps)), timeout=5)
            assert agent._publish_single.call_count == 5
        finally:
            for fp in fps:
                os.unlink(fp)

    @pytest.mark.asyncio
    async def test_listing_id_present_calls_move_to_uploaded(self, agent):
        fp = _tmp_file()
        try:
            await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            agent.storage.move_to_uploaded.assert_called_once()
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_listing_id_none_no_move_called(self, agent):
        fp = _tmp_file()
        try:
            agent._publish_single = AsyncMock(return_value={
                "niche": "mandala", "file_type": "printable_pdf",
                "status": "error", "listing_id": None,
                "images_uploaded": 0, "seo_validated": False, "ab_variant": "A",
            })
            await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            agent.storage.move_to_uploaded.assert_not_called()
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_move_to_uploaded_exception_does_not_propagate(self, agent):
        fp = _tmp_file()
        try:
            agent.storage.move_to_uploaded.side_effect = OSError("disk full")
            result = await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            assert isinstance(result, AgentResult)
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_pq_task_id_with_listing_calls_set_published(self, agent):
        fp = _tmp_file()
        try:
            task = make_task([fp], production_queue_task_id="pq-99")
            with patch("apps.backend.agents.publisher._PQService") as pq_cls:
                pq_inst = AsyncMock()
                pq_inst.get_item_by_task_id = AsyncMock(return_value=MagicMock(id=7))
                pq_inst.set_published = AsyncMock()
                pq_cls.return_value = pq_inst
                await asyncio.wait_for(agent.run(task), timeout=5)
            pq_inst.set_published.assert_called_once_with(7, "L123")
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_pq_item_none_skips_set_published(self, agent):
        fp = _tmp_file()
        try:
            task = make_task([fp], production_queue_task_id="pq-99")
            with patch("apps.backend.agents.publisher._PQService") as pq_cls:
                pq_inst = AsyncMock()
                pq_inst.get_item_by_task_id = AsyncMock(return_value=None)
                pq_inst.set_published = AsyncMock()
                pq_cls.return_value = pq_inst
                await asyncio.wait_for(agent.run(task), timeout=5)
            pq_inst.set_published.assert_not_called()
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_pq_task_id_but_no_listing_skips_pq(self, agent):
        fp = _tmp_file()
        try:
            agent._publish_single = AsyncMock(return_value={
                "niche": "mandala", "file_type": "printable_pdf",
                "status": "error", "listing_id": None,
                "images_uploaded": 0, "seo_validated": False, "ab_variant": "A",
            })
            task = make_task([fp], production_queue_task_id="pq-99")
            with patch("apps.backend.agents.publisher._PQService") as pq_cls:
                await asyncio.wait_for(agent.run(task), timeout=5)
            pq_cls.assert_not_called()
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_publish_single_exception_produces_error_entry(self, agent):
        fp = _tmp_file()
        try:
            agent._publish_single = AsyncMock(side_effect=RuntimeError("etsy down"))
            result = await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            details = result.output_data["publish_details"]
            assert details[0]["status"] == "error"
            assert "etsy down" in details[0]["error"]
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_ab_variant_base_count_zero_first_a_second_b(self, agent):
        fps = [_tmp_file(), _tmp_file()]
        try:
            agent.memory.get_etsy_listings_count = AsyncMock(return_value=0)
            agent._publish_single = AsyncMock(return_value={
                "niche": "m", "file_type": "pdf", "status": "completed",
                "listing_id": "L1", "images_uploaded": 1, "seo_validated": True,
                "ab_variant": "X",
            })
            await asyncio.wait_for(agent.run(make_task(fps)), timeout=5)
            calls = agent._publish_single.call_args_list
            assert calls[0].kwargs["ab_variant"] == "A"
            assert calls[1].kwargs["ab_variant"] == "B"
        finally:
            for fp in fps:
                os.unlink(fp)

    @pytest.mark.asyncio
    async def test_ab_variant_base_count_one_first_is_b(self, agent):
        fp = _tmp_file()
        try:
            agent.memory.get_etsy_listings_count = AsyncMock(return_value=1)
            await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            assert agent._publish_single.call_args_list[0].kwargs["ab_variant"] == "B"
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_output_has_required_keys(self, agent):
        fp = _tmp_file()
        try:
            result = await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            for k in ("listings_created", "listing_ids", "ab_variants", "files_moved_to_uploaded"):
                assert k in result.output_data
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_errors_key_present_when_publish_fails(self, agent):
        fp = _tmp_file()
        try:
            agent._publish_single = AsyncMock(side_effect=ValueError("boom"))
            result = await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            assert "errors" in result.output_data
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_no_errors_key_when_all_succeed(self, agent):
        fp = _tmp_file()
        try:
            result = await asyncio.wait_for(agent.run(make_task([fp])), timeout=5)
            assert "errors" not in result.output_data
        finally:
            os.unlink(fp)

    @pytest.mark.asyncio
    async def test_files_moved_to_uploaded_count_in_output(self, agent):
        fps = [_tmp_file(), _tmp_file()]
        try:
            result = await asyncio.wait_for(agent.run(make_task(fps)), timeout=5)
            assert result.output_data["files_moved_to_uploaded"] == 2
        finally:
            for fp in fps:
                os.unlink(fp)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — PipelineMixin uncovered lines  (~19 tests)
# ─────────────────────────────────────────────────────────────────────────────

from apps.backend.core._pepe._pipeline import PipelineMixin  # noqa: E402


class FakePipeline(PipelineMixin):
    pass


def _make_pipeline() -> FakePipeline:
    obj = FakePipeline()
    obj.memory = AsyncMock()
    obj.memory.save_message = AsyncMock()
    obj.memory.get_production_queue_stats = AsyncMock(return_value=None)
    obj.memory.get_analytics_summary = AsyncMock(return_value=None)
    obj.memory.get_pending_action = AsyncMock(return_value=None)
    obj.memory.upsert_learning = AsyncMock()
    obj.memory.delete_pending_action = AsyncMock()
    obj.memory.get_db = AsyncMock(return_value=AsyncMock())
    obj.memory.resolve_pending_input = AsyncMock()
    obj.notify_telegram = AsyncMock()
    obj.client = MagicMock()
    obj._has_business_domain = MagicMock(return_value=True)
    obj._business_domain = MagicMock()
    obj._business_domain.name = "Etsy"
    obj._build_system_prompt = MagicMock(return_value="system")
    obj._enqueue_and_wait = AsyncMock()
    # close the coroutine to suppress "never awaited" warnings
    obj._fire = MagicMock(side_effect=lambda coro, **kw: coro.close())
    obj._handle_learning_loop = AsyncMock()
    # NOTE: _advance_pipeline_if_autonomous is NOT mocked here so tests can call the real impl.
    # Tests that call _run_design_auto must mock it themselves to avoid pipeline chaining.
    return obj


class TestPipelineMixinUncoveredLines:

    # ── _get_recent_analytics_summary (lines 142, 149-150) ──

    @pytest.mark.asyncio
    async def test_analytics_summary_none_returns_empty_string(self):
        obj = _make_pipeline()
        obj.memory.get_analytics_summary = AsyncMock(return_value=None)
        result = await asyncio.wait_for(obj._get_recent_analytics_summary(), timeout=5)
        assert result == ""

    @pytest.mark.asyncio
    async def test_analytics_summary_empty_dict_returns_empty_string(self):
        obj = _make_pipeline()
        obj.memory.get_analytics_summary = AsyncMock(return_value={})
        result = await asyncio.wait_for(obj._get_recent_analytics_summary(), timeout=5)
        assert result == ""

    @pytest.mark.asyncio
    async def test_analytics_summary_exception_returns_empty_string(self):
        obj = _make_pipeline()
        obj.memory.get_analytics_summary = AsyncMock(side_effect=RuntimeError("db fail"))
        result = await asyncio.wait_for(obj._get_recent_analytics_summary(), timeout=5)
        assert result == ""

    # ── _check_pending_action — upsert_learning exception (lines 187-188) ──

    @pytest.mark.asyncio
    async def test_pending_urgency_upsert_exception_continues_and_returns_reply(self):
        obj = _make_pipeline()
        obj.memory.get_pending_action = AsyncMock(
            return_value={"payload": {"text": "questo testo lungo contiene parole"}}
        )
        obj.memory.upsert_learning = AsyncMock(side_effect=Exception("db write error"))
        obj.memory.delete_pending_action = AsyncMock()
        # Exception inside loop must not propagate; reply still returned
        result = await asyncio.wait_for(
            obj._check_pending_action("sì", "web"), timeout=5
        )
        assert result is not None
        assert "✅" in result

    # ── _advance_pipeline_if_autonomous — design branch (lines 303-318) ──

    @pytest.mark.asyncio
    async def test_advance_design_extracts_pdf_path_from_variants(self):
        """Lines 303-305: pdf_path extracted from variants when file_paths empty."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="design", status=TaskStatus.COMPLETED,
            output_data={"file_paths": [], "variants": [{"pdf_path": "/tmp/d.pdf"}]},
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("design", result, "sess1"), timeout=5
        )
        obj._fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_advance_design_extracts_file_path_field_from_variants(self):
        """Lines 303-305: file_path key (secondary fallback) extracted from variants."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="design", status=TaskStatus.COMPLETED,
            output_data={"file_paths": [], "variants": [{"file_path": "/tmp/d.pdf"}]},
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("design", result, "sess1"), timeout=5
        )
        obj._fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_advance_design_thumbnails_extracted_from_variants(self):
        """Lines 314-318: thumbnail_paths assembled from variants.thumbnails."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="design", status=TaskStatus.COMPLETED,
            output_data={
                "file_paths": ["/tmp/d.pdf"],
                "variants": [
                    {"thumbnails": {"mockup": "/tmp/mock.png", "cover": "/tmp/cov.png"}}
                ],
            },
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("design", result, "sess1"), timeout=5
        )
        obj._fire.assert_called_once()
        assert obj._fire.call_args[1]["name"] == "publisher_auto"

    @pytest.mark.asyncio
    async def test_advance_design_no_files_no_publisher_trigger(self):
        """Line 307: no file_paths and no usable variants → publisher not triggered."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="design", status=TaskStatus.COMPLETED,
            output_data={"file_paths": [], "variants": []},
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("design", result, "sess1"), timeout=5
        )
        obj._fire.assert_not_called()

    # ── Research branch — niche/query fallback (lines 354-356) ──

    @pytest.mark.asyncio
    async def test_advance_research_niche_key_used_as_fallback(self):
        """Lines 354-356: niches=[] but 'niche' present → creates niches list."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="research", status=TaskStatus.COMPLETED,
            output_data={"niches": [], "niche": "yoga planner"},
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("research", result, "sess1"), timeout=5
        )
        obj._fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_advance_research_query_key_used_when_niche_missing(self):
        """Line 354: falls back to 'query' key if 'niche' also missing."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="research", status=TaskStatus.COMPLETED,
            output_data={"niches": [], "query": "bullet journal"},
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("research", result, "sess1"), timeout=5
        )
        obj._fire.assert_called_once()

    # ── Research branch — invalid product_type (line 369) ──

    @pytest.mark.asyncio
    async def test_advance_research_invalid_product_type_falls_back_to_pdf(self):
        """Line 369: product_type not in _VALID_PRODUCT_TYPES → falls back silently."""
        obj = _make_pipeline()
        result = AgentResult(
            task_id="t1", agent_name="research", status=TaskStatus.COMPLETED,
            output_data={"niches": [{"name": "kitchen", "product_type": "invalid_xyz"}]},
        )
        await asyncio.wait_for(
            obj._advance_pipeline_if_autonomous("research", result, "sess1"), timeout=5
        )
        obj._fire.assert_called_once()

    # ── _run_design_auto — research_ctx injection (lines 409-426) ──

    @pytest.mark.asyncio
    async def test_run_design_auto_injects_pricing_from_niches(self):
        """Lines 410-414: pricing from research_ctx.niches[0]."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {"variants": []}
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={
                "niche": "wellness",
                "research_context": {
                    "niches": [{"name": "wellness", "pricing": {"launch_price_usd": "4.99"}}]
                },
            },
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data["pricing"] == {"launch_price_usd": "4.99"}

    @pytest.mark.asyncio
    async def test_run_design_auto_injects_pricing_from_ctx_direct(self):
        """Lines 409-414: research_ctx without 'niches' list → uses ctx directly."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {"variants": []}
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={
                "niche": "fitness",
                "research_context": {"pricing": {"launch_price_usd": "3.99"}},
            },
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data["pricing"] == {"launch_price_usd": "3.99"}

    @pytest.mark.asyncio
    async def test_run_design_auto_existing_pricing_not_overwritten(self):
        """Line 413: output already has 'pricing' → not replaced."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {"variants": [], "pricing": {"keep_me": True}}
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={
                "niche": "wellness",
                "research_context": {
                    "niches": [{"name": "wellness", "pricing": {"overwrite": True}}]
                },
            },
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data["pricing"] == {"keep_me": True}

    @pytest.mark.asyncio
    async def test_run_design_auto_injects_keywords_from_niches(self):
        """Lines 415-416: keywords injected from research_ctx.niches[0]."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {"variants": []}
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={
                "niche": "wellness",
                "research_context": {
                    "niches": [{"name": "wellness", "keywords": ["yoga", "meditation"]}]
                },
            },
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data.get("keywords") == ["yoga", "meditation"]

    @pytest.mark.asyncio
    async def test_run_design_auto_color_schemes_extracted_from_variants(self):
        """Lines 418-422: color_schemes assembled from variants."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {
            "variants": [{"color_scheme": "pastel"}, {"color_scheme": "earth"}],
        }
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={"niche": "mandala", "research_context": {"x": 1}},
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data.get("color_schemes") == ["pastel", "earth"]

    @pytest.mark.asyncio
    async def test_run_design_auto_color_schemes_fallback_from_task_input(self):
        """Lines 424-425: color_schemes from task.input_data when variants yield none."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {"variants": [{"color_scheme": ""}]}  # empty → filtered out
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={
                "niche": "mandala",
                "color_schemes": ["blue", "red"],
                "research_context": {"x": 1},
            },
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data.get("color_schemes") == ["blue", "red"]

    @pytest.mark.asyncio
    async def test_run_design_auto_no_research_ctx_output_unchanged(self):
        """Line 409: no research_context key → output left as-is."""
        obj = _make_pipeline()
        obj._advance_pipeline_if_autonomous = AsyncMock()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.COMPLETED
        mock_result.output_data = {"variants": [], "pricing": {"sentinel": 1}}
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(
            agent_name="design",
            input_data={"niche": "yoga"},  # no research_context
        )
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        assert mock_result.output_data["pricing"] == {"sentinel": 1}

    @pytest.mark.asyncio
    async def test_run_design_auto_failed_result_sends_error_telegram(self):
        """Lines 438-442: result.status != COMPLETED → error message sent."""
        obj = _make_pipeline()
        mock_result = MagicMock()
        mock_result.status = TaskStatus.FAILED
        mock_result.output_data = {"error": "design bombed"}
        mock_result.cost_usd = 0.0
        obj._enqueue_and_wait = AsyncMock(return_value=mock_result)
        task = AgentTask(agent_name="design", input_data={"niche": "flowers"})
        with patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        all_calls = " ".join(str(c) for c in obj.notify_telegram.call_args_list)
        assert "fallito" in all_calls or "bombed" in all_calls

    @pytest.mark.asyncio
    async def test_run_design_auto_exception_handled_gracefully(self):
        """Lines 443-446: _enqueue_and_wait raises → exception caught, telegram notified."""
        obj = _make_pipeline()
        obj._enqueue_and_wait = AsyncMock(side_effect=RuntimeError("worker crashed"))
        task = AgentTask(agent_name="design", input_data={"niche": "nature"})
        with patch("asyncio.sleep", AsyncMock()):
            # Must not raise
            await asyncio.wait_for(obj._run_design_auto(task, "sess1"), timeout=5)
        all_calls = " ".join(str(c) for c in obj.notify_telegram.call_args_list)
        assert "interrotto" in all_calls or "crashed" in all_calls


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — DispatchMixin uncovered lines  (~12 tests)
# ─────────────────────────────────────────────────────────────────────────────

from apps.backend.core._pepe._dispatch import DispatchMixin  # noqa: E402


class FakeDispatch(DispatchMixin):
    pass


def _make_dispatch() -> FakeDispatch:
    obj = FakeDispatch()
    obj.memory = AsyncMock()
    obj.memory.save_message = AsyncMock()
    obj.memory.get_pending_action = AsyncMock(return_value=None)
    obj.memory.get_conversation_history = AsyncMock(return_value=[])
    obj.memory.query_insights = AsyncMock(return_value=[])
    obj.memory.upsert_learning = AsyncMock()
    obj.memory.delete_pending_action = AsyncMock()
    obj.wiki = AsyncMock()
    obj.wiki.query = AsyncMock(return_value="wiki result")
    obj.client = MagicMock()
    obj._has_business_domain = MagicMock(return_value=True)
    obj._business_domain = MagicMock()
    obj._business_domain.name = "Etsy"
    obj._build_system_prompt = MagicMock(return_value="system base")
    obj._wiki = ""
    obj._agents = {}
    obj._last_watcher_app = ""
    # All methods called by handle_user_message
    obj._check_pending_action = AsyncMock(return_value=None)
    obj._get_pipeline_summary = AsyncMock(return_value="")
    obj._get_recent_analytics_summary = AsyncMock(return_value="")
    obj._llm_decide = AsyncMock(return_value=(None, "risposta diretta"))
    obj._apply_confidence_gate = AsyncMock(return_value="risposta agente")
    obj._agent_requires_clarification = MagicMock(return_value=False)
    obj._clarify_if_needed = AsyncMock(return_value=None)
    obj._check_pipeline_duplicate = AsyncMock(return_value=None)
    obj._enrich_task_context = AsyncMock(return_value={})
    obj._enqueue_and_wait = AsyncMock()
    obj._synthesize_error = AsyncMock(return_value="errore sintetizzato")
    obj._voice_error_phrase = MagicMock(return_value="errore vocale")
    return obj


class TestDispatchMixinUncoveredLines:

    # ── wiki.query exception → self._wiki stays "" (lines 108-113) ──

    @pytest.mark.asyncio
    async def test_wiki_query_exception_wiki_stays_empty(self):
        obj = _make_dispatch()
        obj.wiki.query = AsyncMock(side_effect=RuntimeError("wiki down"))
        await asyncio.wait_for(
            obj.handle_user_message("ciao", "web", "sess1"), timeout=5
        )
        assert obj._wiki == ""

    @pytest.mark.asyncio
    async def test_wiki_query_success_sets_wiki_content(self):
        """Line 109: successful wiki.query populates self._wiki."""
        obj = _make_dispatch()
        obj.wiki.query = AsyncMock(return_value="wiki content about Etsy")
        await asyncio.wait_for(
            obj.handle_user_message("come va Etsy?", "web", "sess1"), timeout=5
        )
        assert obj._wiki == "wiki content about Etsy"

    # ── _has_business_domain False → wiki.query not called (line 107) ──

    @pytest.mark.asyncio
    async def test_no_business_domain_wiki_not_called(self):
        obj = _make_dispatch()
        obj._has_business_domain = MagicMock(return_value=False)
        await asyncio.wait_for(
            obj.handle_user_message("ciao", "web", "sess1"), timeout=5
        )
        obj.wiki.query.assert_not_called()

    # ── context_docs non-empty → context_text appended (lines 82, 101) ──

    @pytest.mark.asyncio
    async def test_context_docs_non_empty_appended_to_user_content(self):
        obj = _make_dispatch()
        obj.memory.query_insights = AsyncMock(
            return_value=[{"document": "insight A"}, {"document": "insight B"}]
        )
        captured: list = []

        async def capture_llm_decide(history, system, **kwargs):
            captured.append(history)
            return None, "risposta"

        obj._llm_decide = capture_llm_decide
        await asyncio.wait_for(
            obj.handle_user_message("domanda", "web", "sess1"), timeout=5
        )
        assert captured
        last_user = next((m for m in reversed(captured[0]) if m["role"] == "user"), None)
        assert last_user is not None
        assert "insight A" in last_user["content"]

    # ── history filtering — last user msg popped (lines 90-93, 96) ──

    @pytest.mark.asyncio
    async def test_history_last_user_message_popped_before_appending_current(self):
        obj = _make_dispatch()
        obj.memory.get_conversation_history = AsyncMock(return_value=[
            {"role": "user", "content": "prima domanda"},
            {"role": "assistant", "content": "prima risposta"},
            {"role": "user", "content": "corrente salvata"},  # last user → popped
        ])
        captured: list = []

        async def capture_llm_decide(history, system, **kwargs):
            captured.append(list(history))
            return None, "ok"

        obj._llm_decide = capture_llm_decide
        await asyncio.wait_for(
            obj.handle_user_message("nuova domanda", "web", "sess1"), timeout=5
        )
        assert captured
        user_msgs = [m for m in captured[0] if m["role"] == "user"]
        # "prima domanda" kept, "corrente salvata" popped, "nuova domanda" appended
        contents = [m["content"] for m in user_msgs]
        assert "prima domanda" in contents
        assert any("nuova domanda" in c for c in contents)
        assert "corrente salvata" not in " ".join(contents)

    @pytest.mark.asyncio
    async def test_history_pepe_role_mapped_to_assistant(self):
        """Lines 92-93: role 'pepe' → 'assistant' in history."""
        obj = _make_dispatch()
        obj.memory.get_conversation_history = AsyncMock(return_value=[
            {"role": "pepe", "content": "risposta di pepe"},
        ])
        captured: list = []

        async def capture_llm_decide(history, system, **kwargs):
            captured.append(list(history))
            return None, "ok"

        obj._llm_decide = capture_llm_decide
        await asyncio.wait_for(
            obj.handle_user_message("query", "web", "sess1"), timeout=5
        )
        assert captured
        assistant_msgs = [m for m in captured[0] if m["role"] == "assistant"]
        assert any("risposta di pepe" in m["content"] for m in assistant_msgs)

    # ── orb_voice → voice instructions appended to system prompt (lines 119-135) ──

    @pytest.mark.asyncio
    async def test_orb_voice_system_prompt_contains_voice_instructions(self):
        obj = _make_dispatch()
        captured_systems: list = []

        async def capture_llm_decide(history, system, **kwargs):
            captured_systems.append(system)
            return None, "breve risposta"

        obj._llm_decide = capture_llm_decide
        await asyncio.wait_for(
            obj.handle_user_message("dimmi qualcosa", "orb_voice", "sess1"), timeout=5
        )
        assert captured_systems
        system = captured_systems[0]
        assert "MODALITÀ VOCALE" in system
        assert "TTS" in system

    # ── clarification returned → short-circuits (line 171) ──

    @pytest.mark.asyncio
    async def test_delegation_clarification_short_circuits_enqueue(self):
        obj = _make_dispatch()
        # "remind" triggers the _needs_clarify fallback path
        obj._llm_decide = AsyncMock(return_value=(
            {"delegate": "remind", "input": {"task": "something"}}, None
        ))
        obj._clarify_if_needed = AsyncMock(return_value="Dimmi quando esattamente?")
        result = await asyncio.wait_for(
            obj.handle_user_message("ricordami qualcosa", "web", "sess1"), timeout=5
        )
        assert result == "Dimmi quando esattamente?"
        obj._enqueue_and_wait.assert_not_called()

    # ── delegation error → _synthesize_error (line 196) ──

    @pytest.mark.asyncio
    async def test_delegation_enqueue_error_calls_synthesize_error(self):
        obj = _make_dispatch()
        obj._llm_decide = AsyncMock(return_value=(
            {"delegate": "design", "input": {"niche": "flowers"}}, None
        ))
        obj._enqueue_and_wait = AsyncMock(side_effect=RuntimeError("agent crashed"))
        result = await asyncio.wait_for(
            obj.handle_user_message("crea un prodotto", "web", "sess1"), timeout=5
        )
        obj._synthesize_error.assert_called_once()
        assert result == "errore sintetizzato"

    # ── orb_voice delegation error → voice phrase (lines 193-194) ──

    @pytest.mark.asyncio
    async def test_delegation_enqueue_error_orb_voice_uses_voice_phrase(self):
        obj = _make_dispatch()
        obj._llm_decide = AsyncMock(return_value=(
            {"delegate": "design", "input": {"niche": "nature"}}, None
        ))
        obj._enqueue_and_wait = AsyncMock(side_effect=RuntimeError("crash"))
        result = await asyncio.wait_for(
            obj.handle_user_message("crea qualcosa", "orb_voice", "sess1"), timeout=5
        )
        obj._voice_error_phrase.assert_called_once()
        assert result == "errore vocale"
        obj._synthesize_error.assert_not_called()

    # ── _enqueue_and_wait BaseException path (lines 248-250) ──

    @pytest.mark.asyncio
    async def test_enqueue_and_wait_cancellation_removes_future(self):
        """Lines 248-250: CancelledError (BaseException) cleans up _pending_futures."""
        obj = FakeDispatch()
        obj._pending_futures = {}
        obj._queue = asyncio.Queue()
        obj._AGENT_TIMEOUTS = {}
        obj._AGENT_TIMEOUT_DEFAULT = 30.0

        task_obj = AgentTask(agent_name="recall", input_data={})
        enqueue_task = asyncio.create_task(
            DispatchMixin._enqueue_and_wait(obj, task_obj)
        )
        # Let the coroutine advance to where it's suspended awaiting the future
        await asyncio.sleep(0)
        assert task_obj.task_id in obj._pending_futures

        # Cancel → CancelledError is a BaseException; triggers the except BaseException: block
        enqueue_task.cancel()
        try:
            await enqueue_task
        except BaseException:
            pass

        assert task_obj.task_id not in obj._pending_futures
