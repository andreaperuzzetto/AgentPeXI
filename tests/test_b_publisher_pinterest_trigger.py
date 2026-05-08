"""B-08: Publisher → PinterestAgent trigger tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import anthropic
import pytest

# Module-level imports prevent post-patch import failures caused by other
# tests stubbing AgentCard before this module is first loaded.
from apps.backend.agents.publisher import PublisherAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_publisher_stub(tmp_path: Path):
    """Minimal publisher stub che bypassa tutti i side-effect di _publish_single."""
    pub = MagicMock()
    pub.etsy_api = MagicMock()
    pub.etsy_api.mock_mode = True
    pub.memory = MagicMock()
    pub.memory.add_etsy_listing = AsyncMock()
    pub.memory.get_db = AsyncMock(return_value=MagicMock())
    pub._generate_mock_thumbnail = AsyncMock(return_value=(True, [str(tmp_path / "thumb.jpg")]))
    pub._check_failure_history = AsyncMock(return_value={})
    pub._generate_seo = AsyncMock(return_value={
        "title": "Test Printable Planner",
        "description": "A lovely digital planner",
        "tags": ["planner", "digital", "printable"],
        "seo_validated": True,
    })
    pub._resolve_price = MagicMock(return_value=4.99)
    pub._resolve_section_id = AsyncMock(return_value=None)
    pub._dispatch_publish = AsyncMock(return_value=("listing_test_abc123", 2))
    pub._notify_telegram = AsyncMock()
    pub._get_when_made = MagicMock(return_value="made_to_order")
    pub._pinterest_agent = None
    return pub


def _make_pdf_file(tmp_path: Path) -> str:
    """Crea un file PDF finto abbastanza piccolo per superare il check 20MB."""
    pdf = tmp_path / "test_product.pdf"
    pdf.write_bytes(b"fake pdf content" * 100)
    return str(pdf)


# ---------------------------------------------------------------------------
# 1. Constructor: PublisherAgent.__init__ accepts pinterest_agent kwarg
# ---------------------------------------------------------------------------

def test_publisher_accepts_pinterest_agent_kwarg():
    """PublisherAgent.__init__ deve accettare il kwarg pinterest_agent."""
    mock_client = MagicMock(spec=anthropic.AsyncAnthropic)
    mock_memory = MagicMock()
    mock_storage = MagicMock()
    mock_etsy = MagicMock()
    mock_pinterest = MagicMock()

    pub = PublisherAgent(
        anthropic_client=mock_client,
        memory=mock_memory,
        storage=mock_storage,
        etsy_api=mock_etsy,
        pinterest_agent=mock_pinterest,
    )
    assert pub._pinterest_agent is mock_pinterest


def test_publisher_pinterest_agent_defaults_to_none():
    """Senza pinterest_agent kwarg, self._pinterest_agent deve essere None."""
    mock_client = MagicMock(spec=anthropic.AsyncAnthropic)
    mock_memory = MagicMock()
    mock_storage = MagicMock()
    mock_etsy = MagicMock()

    pub = PublisherAgent(
        anthropic_client=mock_client,
        memory=mock_memory,
        storage=mock_storage,
        etsy_api=mock_etsy,
    )
    assert pub._pinterest_agent is None


# ---------------------------------------------------------------------------
# 2. Trigger fires after successful publish
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_fires_when_agent_set_and_publish_succeeds(tmp_path):
    """Dopo publish riuscito, asyncio.create_task deve essere chiamato
    quando _pinterest_agent è impostato."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    pub = _make_publisher_stub(tmp_path)
    mock_pinterest_agent = MagicMock()
    mock_pinterest_agent.run = AsyncMock(return_value=MagicMock())
    pub._pinterest_agent = mock_pinterest_agent

    pdf = _make_pdf_file(tmp_path)

    with patch("asyncio.create_task") as mock_create_task:
        await _PublishMixin._publish_single(
            pub,
            file_path=pdf,
            product_type="printable_pdf",
            template="planner_basic",
            niche="wellness_planner",
            color_scheme="pastel_green",
            keywords=["planner", "digital"],
            size="A4",
            ab_variant="A",
            pq_task_id=None,
            research_data={},
        )

    mock_create_task.assert_called_once()
    # Drain the coroutine passed to create_task to avoid "never awaited" leak
    await mock_create_task.call_args[0][0]


@pytest.mark.asyncio
async def test_trigger_not_fired_when_agent_is_none(tmp_path):
    """asyncio.create_task NON deve essere chiamato quando _pinterest_agent è None."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    pub = _make_publisher_stub(tmp_path)
    pub._pinterest_agent = None

    pdf = _make_pdf_file(tmp_path)

    with patch("asyncio.create_task") as mock_create_task:
        await _PublishMixin._publish_single(
            pub,
            file_path=pdf,
            product_type="printable_pdf",
            template="planner_basic",
            niche="wellness_planner",
            color_scheme="pastel_green",
            keywords=["planner", "digital"],
            size="A4",
            ab_variant="A",
            pq_task_id=None,
            research_data={},
        )

    mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# 3. AgentTask payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_task_has_generate_pins_action(tmp_path):
    """Il coroutine passato a create_task deve provenire da pinterest_agent.run()
    con action='generate_pins' nell'input_data."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin
    from apps.backend.core.models import AgentTask

    pub = _make_publisher_stub(tmp_path)
    mock_pinterest_agent = MagicMock()
    mock_pinterest_agent.run = AsyncMock(return_value=MagicMock())
    pub._pinterest_agent = mock_pinterest_agent

    pdf = _make_pdf_file(tmp_path)

    captured_tasks = []

    def _capture_task(coro):
        captured_tasks.append(coro)
        # Create a real task so the coroutine is properly consumed
        loop = asyncio.get_event_loop()
        return loop.create_task(coro)

    with patch("asyncio.create_task", side_effect=_capture_task):
        await _PublishMixin._publish_single(
            pub,
            file_path=pdf,
            product_type="printable_pdf",
            template="planner_basic",
            niche="wellness_planner",
            color_scheme="pastel_green",
            keywords=["planner", "digital"],
            size="A4",
            ab_variant="A",
            pq_task_id=None,
            research_data={},
        )

    # Give the fire-and-forget task time to run
    await asyncio.sleep(0)

    mock_pinterest_agent.run.assert_called_once()
    call_args = mock_pinterest_agent.run.call_args
    task_arg: AgentTask = call_args[0][0]
    assert task_arg.input_data["action"] == "generate_pins"


@pytest.mark.asyncio
async def test_trigger_task_has_listing_id(tmp_path):
    """Il task passato a pinterest_agent.run deve includere il listing_id corretto."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin
    from apps.backend.core.models import AgentTask

    pub = _make_publisher_stub(tmp_path)
    # _dispatch_publish returns this listing_id
    expected_listing_id = "listing_test_abc123"
    pub._dispatch_publish = AsyncMock(return_value=(expected_listing_id, 2))

    mock_pinterest_agent = MagicMock()
    mock_pinterest_agent.run = AsyncMock(return_value=MagicMock())
    pub._pinterest_agent = mock_pinterest_agent

    pdf = _make_pdf_file(tmp_path)

    def _capture_and_run(coro):
        loop = asyncio.get_event_loop()
        return loop.create_task(coro)

    with patch("asyncio.create_task", side_effect=_capture_and_run):
        await _PublishMixin._publish_single(
            pub,
            file_path=pdf,
            product_type="printable_pdf",
            template="planner_basic",
            niche="wellness_planner",
            color_scheme="pastel_green",
            keywords=["planner", "digital"],
            size="A4",
            ab_variant="A",
            pq_task_id=None,
            research_data={},
        )

    await asyncio.sleep(0)

    mock_pinterest_agent.run.assert_called_once()
    call_args = mock_pinterest_agent.run.call_args
    task_arg: AgentTask = call_args[0][0]
    assert task_arg.input_data["listing_id"] == expected_listing_id


@pytest.mark.asyncio
async def test_trigger_task_id_contains_listing_id(tmp_path):
    """Il task_id dell'AgentTask deve contenere il listing_id (per tracciabilità)."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin
    from apps.backend.core.models import AgentTask

    pub = _make_publisher_stub(tmp_path)
    expected_listing_id = "listing_test_abc123"
    pub._dispatch_publish = AsyncMock(return_value=(expected_listing_id, 1))

    mock_pinterest_agent = MagicMock()
    mock_pinterest_agent.run = AsyncMock(return_value=MagicMock())
    pub._pinterest_agent = mock_pinterest_agent

    pdf = _make_pdf_file(tmp_path)

    created_task = None

    def _capture_and_run(coro):
        nonlocal created_task
        loop = asyncio.get_event_loop()
        created_task = loop.create_task(coro)
        return created_task

    with patch("asyncio.create_task", side_effect=_capture_and_run):
        await _PublishMixin._publish_single(
            pub,
            file_path=pdf,
            product_type="printable_pdf",
            template="planner_basic",
            niche="wellness_planner",
            color_scheme="",
            keywords=[],
            size="A4",
            ab_variant="B",
            pq_task_id=None,
            research_data={},
        )

    # Drain the fire-and-forget task to avoid leaked coroutine warning
    if created_task is not None:
        await created_task

    call_args = mock_pinterest_agent.run.call_args
    task_arg: AgentTask = call_args[0][0]
    assert expected_listing_id in task_arg.task_id


# ---------------------------------------------------------------------------
# 4. AgentBundle + state.py + _agents.py wiring
# ---------------------------------------------------------------------------

def test_agent_bundle_has_pinterest_agent_field():
    """AgentBundle deve avere il campo pinterest_agent."""
    from apps.backend.core._startup._models import AgentBundle
    import dataclasses

    fields = {f.name for f in dataclasses.fields(AgentBundle)}
    assert "pinterest_agent" in fields


def test_state_has_pinterest_agent_attribute():
    """AppState (state.py) deve avere un attributo pinterest_agent."""
    from apps.backend.api import state as state_module

    assert hasattr(state_module, "pinterest_agent"), (
        "apps.backend.api.state deve definire pinterest_agent"
    )
