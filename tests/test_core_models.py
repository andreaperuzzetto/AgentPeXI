"""Tests for core data models: AgentTask, AgentResult, AgentCard, enums.

NOTE: models is loaded from disk via importlib to bypass the sys.modules
stub installed by test_block1_integration.py at collection time.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from datetime import datetime, timezone

import pytest

# Load models.py from disk — test_block1_integration.py explicitly replaces
# sys.modules["apps.backend.core.models"] with a minimal stub, so we bypass it.
_models_path = Path(__file__).parent.parent / "apps" / "backend" / "core" / "models.py"
_spec = importlib.util.spec_from_file_location("_real_core_models", str(_models_path))
_mod = importlib.util.module_from_spec(_spec)
import sys as _sys
_sys.modules["_real_core_models"] = _mod  # must register before exec so @dataclass works
_spec.loader.exec_module(_mod)

AgentTask = _mod.AgentTask
AgentResult = _mod.AgentResult
AgentCard = _mod.AgentCard
AgentStatus = _mod.AgentStatus
TaskStatus = _mod.TaskStatus


# ---------------------------------------------------------------------------
# AgentTask
# ---------------------------------------------------------------------------

def test_agent_task_required_fields():
    task = AgentTask(agent_name="research", input_data={"query": "test"})
    assert task.agent_name == "research"
    assert task.input_data == {"query": "test"}


def test_agent_task_id_auto_generated_as_uuid():
    task = AgentTask(agent_name="design", input_data={})
    # Must be parseable as UUID
    parsed = uuid.UUID(task.task_id)
    assert str(parsed) == task.task_id


def test_agent_task_created_at_has_timezone():
    task = AgentTask(agent_name="design", input_data={})
    assert task.created_at.tzinfo is not None


def test_agent_task_source_defaults_to_web():
    task = AgentTask(agent_name="analytics", input_data={})
    assert task.source == "web"


def test_agent_task_pending_input_defaults_to_none():
    task = AgentTask(agent_name="analytics", input_data={})
    assert task.pending_input is None


def test_agent_task_two_tasks_have_different_ids():
    t1 = AgentTask(agent_name="a", input_data={})
    t2 = AgentTask(agent_name="b", input_data={})
    assert t1.task_id != t2.task_id


def test_agent_task_source_can_be_set():
    task = AgentTask(agent_name="analytics", input_data={}, source="telegram")
    assert task.source == "telegram"


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

def test_agent_result_required_fields():
    result = AgentResult(
        task_id="abc-123",
        agent_name="research",
        status=TaskStatus.COMPLETED,
    )
    assert result.task_id == "abc-123"
    assert result.agent_name == "research"
    assert result.status == TaskStatus.COMPLETED


def test_agent_result_tokens_used_defaults_to_zero():
    result = AgentResult(task_id="x", agent_name="y", status=TaskStatus.FAILED)
    assert result.tokens_used == 0


def test_agent_result_cost_usd_defaults_to_zero():
    result = AgentResult(task_id="x", agent_name="y", status=TaskStatus.FAILED)
    assert result.cost_usd == 0.0


def test_agent_result_confidence_defaults_to_zero():
    result = AgentResult(task_id="x", agent_name="y", status=TaskStatus.COMPLETED)
    assert result.confidence == 0.0


def test_agent_result_missing_data_defaults_to_empty_list():
    result = AgentResult(task_id="x", agent_name="y", status=TaskStatus.COMPLETED)
    assert result.missing_data == []


def test_agent_result_reply_voice_defaults_to_empty_string():
    result = AgentResult(task_id="x", agent_name="y", status=TaskStatus.COMPLETED)
    assert result.reply_voice == ""


def test_agent_result_output_data_defaults_to_empty_dict():
    result = AgentResult(task_id="x", agent_name="y", status=TaskStatus.COMPLETED)
    assert result.output_data == {}


# ---------------------------------------------------------------------------
# AgentCard
# ---------------------------------------------------------------------------

def test_agent_card_all_fields_present():
    card = AgentCard(
        name="research",
        description="Finds niches",
        input_schema={"type": "object"},
        layer="business",
        llm="sonnet",
    )
    assert card.name == "research"
    assert card.description == "Finds niches"
    assert card.layer == "business"
    assert card.llm == "sonnet"


def test_agent_card_requires_clarification_defaults_to_empty_list():
    card = AgentCard(
        name="x", description="d", input_schema={}, layer="personal", llm="haiku"
    )
    assert card.requires_clarification == []


def test_agent_card_confidence_threshold_defaults_to_085():
    card = AgentCard(
        name="x", description="d", input_schema={}, layer="personal", llm="haiku"
    )
    assert card.confidence_threshold == 0.85


def test_agent_card_requires_confirmation_defaults_to_false():
    card = AgentCard(
        name="x", description="d", input_schema={}, layer="personal", llm="haiku"
    )
    assert card.requires_confirmation is False


def test_agent_card_pipeline_position_defaults_to_none():
    card = AgentCard(
        name="x", description="d", input_schema={}, layer="personal", llm="haiku"
    )
    assert card.pipeline_position is None


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

def test_agent_status_idle_value():
    assert AgentStatus.IDLE == "idle"


def test_agent_status_running_value():
    assert AgentStatus.RUNNING == "running"


def test_agent_status_is_string():
    assert isinstance(AgentStatus.IDLE.value, str)


def test_task_status_completed_value():
    assert TaskStatus.COMPLETED == "completed"


def test_task_status_failed_value():
    assert TaskStatus.FAILED == "failed"


def test_task_status_pending_value():
    assert TaskStatus.PENDING == "pending"


def test_task_status_all_values_are_strings():
    for status in TaskStatus:
        assert isinstance(status.value, str)


def test_agent_status_all_values_are_strings():
    for status in AgentStatus:
        assert isinstance(status.value, str)
