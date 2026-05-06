"""B-05 — PinterestAgent skeleton + 5-phase warmup pipeline.

TDD: questi test devono essere RED prima dell'implementazione.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_agent():
    """Costruisce un PinterestAgent senza dipendenze reali."""
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    agent = PinterestAgent.__new__(PinterestAgent)
    agent.name = "pinterest"
    agent.model = "claude-haiku-4-5-20251001"
    agent.client = MagicMock()
    agent.memory = MagicMock()
    agent._ws_broadcast = None
    agent._task_id = ""
    agent._step_counter = 0
    agent._llm_call_count = 0
    agent._tool_call_count = 0
    agent._total_cost = 0.0
    agent._total_tokens = 0
    return agent


# ---------------------------------------------------------------------------
# AgentCard tests
# ---------------------------------------------------------------------------

def test_agent_card_name_is_pinterest():
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    assert PinterestAgent.card.name == "pinterest"


def test_agent_card_layer_is_business():
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    assert PinterestAgent.card.layer == "business"


def test_agent_card_pipeline_position_is_4():
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    assert PinterestAgent.card.pipeline_position == 4


def test_pinterest_agent_inherits_from_agent_base():
    from apps.backend.agents.base import AgentBase  # noqa: PLC0415
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    assert issubclass(PinterestAgent, AgentBase)


# ---------------------------------------------------------------------------
# Warmup — phases called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_warmup_calls_phase1_with_section_key():
    agent = _make_agent()
    agent._phase1_trends = AsyncMock(return_value={"keywords": []})
    agent._phase2_competitor_pins = AsyncMock(return_value={"pins": []})
    agent._phase3_board_analysis = AsyncMock(return_value={"boards": []})
    agent._phase4_test_pin = AsyncMock(return_value={"image_path": ""})
    agent._phase5_synthesize = AsyncMock(return_value={"style_guide": {}})

    await agent.run_warmup("party_printable")

    agent._phase1_trends.assert_called_once_with("party_printable")


@pytest.mark.asyncio
async def test_run_warmup_calls_phase2_with_section_key():
    agent = _make_agent()
    agent._phase1_trends = AsyncMock(return_value={"keywords": []})
    agent._phase2_competitor_pins = AsyncMock(return_value={"pins": []})
    agent._phase3_board_analysis = AsyncMock(return_value={"boards": []})
    agent._phase4_test_pin = AsyncMock(return_value={"image_path": ""})
    agent._phase5_synthesize = AsyncMock(return_value={"style_guide": {}})

    await agent.run_warmup("wedding_printable")

    agent._phase2_competitor_pins.assert_called_once_with("wedding_printable")


@pytest.mark.asyncio
async def test_run_warmup_calls_all_5_phases():
    agent = _make_agent()
    agent._phase1_trends = AsyncMock(return_value={"keywords": []})
    agent._phase2_competitor_pins = AsyncMock(return_value={"pins": []})
    agent._phase3_board_analysis = AsyncMock(return_value={"boards": []})
    agent._phase4_test_pin = AsyncMock(return_value={"image_path": ""})
    agent._phase5_synthesize = AsyncMock(return_value={"style_guide": {}})

    await agent.run_warmup("party_printable")

    assert agent._phase1_trends.call_count == 1
    assert agent._phase2_competitor_pins.call_count == 1
    assert agent._phase3_board_analysis.call_count == 1
    assert agent._phase4_test_pin.call_count == 1
    assert agent._phase5_synthesize.call_count == 1


@pytest.mark.asyncio
async def test_run_warmup_phases_1_to_4_run_concurrently():
    """Verifica che fasi 1-4 girino in parallelo (asyncio.gather)."""
    agent = _make_agent()
    start_times: list[tuple[int, float]] = []

    async def _timed(phase_n: int, result: dict):
        start_times.append((phase_n, asyncio.get_event_loop().time()))
        await asyncio.sleep(0.01)  # piccolo delay
        return result

    agent._phase1_trends = lambda sk: _timed(1, {"keywords": []})
    agent._phase2_competitor_pins = lambda sk: _timed(2, {"pins": []})
    agent._phase3_board_analysis = lambda sk: _timed(3, {"boards": []})
    agent._phase4_test_pin = lambda sk: _timed(4, {"image_path": ""})
    agent._phase5_synthesize = AsyncMock(return_value={"style_guide": {}})

    await agent.run_warmup("party_printable")

    assert len(start_times) == 4, "Tutte e 4 le fasi devono essere avviate"
    # Se eseguite in parallelo, la differenza tra il primo e l'ultimo start è < 5ms
    times = [t for _, t in start_times]
    spread = max(times) - min(times)
    assert spread < 0.005, f"Fasi 1-4 non parallele: spread {spread:.4f}s"


@pytest.mark.asyncio
async def test_run_warmup_phase5_receives_phase1_to_4_results():
    """La fase 5 deve ricevere i risultati aggregati delle fasi 1-4."""
    agent = _make_agent()
    agent._phase1_trends = AsyncMock(return_value={"keywords": ["wedding"]})
    agent._phase2_competitor_pins = AsyncMock(return_value={"pins": [{"style": "lifestyle"}]})
    agent._phase3_board_analysis = AsyncMock(return_value={"boards": [{"name": "weddings"}]})
    agent._phase4_test_pin = AsyncMock(return_value={"image_path": "/tmp/test.jpg"})
    agent._phase5_synthesize = AsyncMock(return_value={"style_guide": {}})

    await agent.run_warmup("party_printable")

    call_args = agent._phase5_synthesize.call_args
    # Secondo argomento deve essere il dict con i 4 risultati
    phases_data: dict = call_args[0][1] if call_args[0] else call_args[1].get("phases_data", {})
    assert "trends" in phases_data
    assert "competitor_pins" in phases_data
    assert "board_analysis" in phases_data
    assert "test_pin" in phases_data


# ---------------------------------------------------------------------------
# Warmup — return shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_warmup_returns_dict_with_style_guide():
    agent = _make_agent()
    agent._phase1_trends = AsyncMock(return_value={"keywords": []})
    agent._phase2_competitor_pins = AsyncMock(return_value={"pins": []})
    agent._phase3_board_analysis = AsyncMock(return_value={"boards": []})
    agent._phase4_test_pin = AsyncMock(return_value={"image_path": ""})
    agent._phase5_synthesize = AsyncMock(
        return_value={"style_guide": {"variant_priority": "A", "palettes": []}}
    )

    result = await agent.run_warmup("party_printable")

    assert isinstance(result, dict)
    assert "style_guide" in result


# ---------------------------------------------------------------------------
# run() dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_dispatches_warmup_action_to_run_warmup():
    from apps.backend.core.models import AgentTask  # noqa: PLC0415

    agent = _make_agent()
    agent.run_warmup = AsyncMock(return_value={"style_guide": {}})

    task = AgentTask(
        task_id="b05-test-001",
        agent_name="pinterest",
        input_data={"action": "warmup", "section_key": "party_printable"},
    )
    await agent.run(task)

    agent.run_warmup.assert_called_once_with("party_printable")


@pytest.mark.asyncio
async def test_run_returns_agent_result_with_completed_status():
    from apps.backend.core.models import AgentResult, AgentTask, TaskStatus  # noqa: PLC0415

    agent = _make_agent()
    agent.run_warmup = AsyncMock(return_value={"style_guide": {}})

    task = AgentTask(
        task_id="b05-test-002",
        agent_name="pinterest",
        input_data={"action": "warmup", "section_key": "party_printable"},
    )
    result = await agent.run(task)

    assert isinstance(result, AgentResult)
    assert result.status == TaskStatus.COMPLETED
    assert result.task_id == "b05-test-002"
    assert result.agent_name == "pinterest"
