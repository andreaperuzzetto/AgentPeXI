"""B-06 — Pin generation mixin: variant selection, copy generation, scheduling.

TDD: questi test devono essere RED prima dell'implementazione.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_LISTING = {
    "listing_id": "listing_001",
    "title": "ADHD Daily Planner Printable",
    "niche": "planners_organizers",
    "section_key": "planners_organizers",
    "audience_target": "ADHD adults",
    "conversion_triggers": ["instant download", "undated"],
    "thumbnail_style": "lifestyle",
    "gap_to_exploit": "Existing planners are too rigid for ADHD needs",
    "main_image_path": "/storage/uploads/planner.jpg",
    "board_id": "board_planners_001",
    "production_queue_id": 42,
    "cluster_size": 0,
    "selling_signals": {"thumbnail_style": "lifestyle"},
}


def _make_agent(memory=None):
    """PinterestAgent senza dipendenze reali."""
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    agent = PinterestAgent.__new__(PinterestAgent)
    agent.name = "pinterest"
    agent.model = "claude-haiku-4-5-20251001"
    agent.client = MagicMock()
    agent.memory = memory or MagicMock()
    agent._ws_broadcast = None
    agent._task_id = "test-task"
    agent._step_counter = 0
    agent._llm_call_count = 0
    agent._tool_call_count = 0
    agent._total_cost = 0.0
    agent._total_tokens = 0
    return agent


async def _make_memory_base(tmp_path):
    """Crea MemoryBase reale con schema B-01 (pinterest_queue + pinterest_boards)."""
    from apps.backend.core._memory._base import MemoryBase  # noqa: PLC0415

    mm = MemoryBase.__new__(MemoryBase)
    mm._db_path = str(tmp_path / "test.db")
    mm._chromadb_path = str(tmp_path / "chromadb")
    mm._db = None
    mm._chroma_collection = None
    mm._screen_memory_collection = None
    mm._personal_memory_collection = None
    mm._shared_memory_collection = None
    mm._ws_broadcaster = None
    mm._bridge_callback = None
    mm.mock_mode = False
    await mm.init()  # crea schema, poi chiude _db → _db = None
    # Riapre connessione persistente
    mm._db = await aiosqlite.connect(mm._db_path)
    mm._db.row_factory = aiosqlite.Row
    return mm


# ---------------------------------------------------------------------------
# Part 1 — Variant selection (pure logic, no async)
# ---------------------------------------------------------------------------

def test_select_variants_default_returns_a_and_b():
    """Senza condizioni speciali, genera sempre variante A (1) e B (2)."""
    agent = _make_agent()
    variants = agent._select_variants(_MINIMAL_LISTING)
    assert 1 in variants
    assert 2 in variants


def test_select_variants_only_a_b_when_no_conditions():
    """Con listing minimale (no editorial, no pain_point, cluster<3) → solo A e B."""
    listing = {**_MINIMAL_LISTING, "selling_signals": {"thumbnail_style": "lifestyle"}, "gap_to_exploit": "generic gap", "cluster_size": 0}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert sorted(variants) == [1, 2]


def test_select_variants_adds_c_for_editorial_thumbnail_style():
    """Se thumbnail_style='editorial' in selling_signals → aggiunge variante C (3)."""
    listing = {**_MINIMAL_LISTING, "selling_signals": {"thumbnail_style": "editorial"}}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert 3 in variants


def test_select_variants_no_c_for_non_editorial_thumbnail():
    """thumbnail_style diverso da 'editorial' → C non inclusa."""
    listing = {**_MINIMAL_LISTING, "selling_signals": {"thumbnail_style": "flat_lay"}}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert 3 not in variants


def test_select_variants_adds_d_when_gap_has_pain_point():
    """Se gap_to_exploit contiene 'pain' (case-insensitive) → aggiunge variante D (4)."""
    listing = {**_MINIMAL_LISTING, "gap_to_exploit": "Major pain point in current planner designs"}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert 4 in variants


def test_select_variants_no_d_when_gap_has_no_pain_point():
    """gap_to_exploit senza 'pain' → D non inclusa."""
    listing = {**_MINIMAL_LISTING, "gap_to_exploit": "Existing designs are too complicated"}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert 4 not in variants


def test_select_variants_no_e_when_cluster_lt_3():
    """cluster_size < 3 → variante E (5) non inclusa."""
    listing = {**_MINIMAL_LISTING, "cluster_size": 2}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert 5 not in variants


def test_select_variants_includes_e_when_cluster_ge_3():
    """cluster_size >= 3 → variante E (5) inclusa."""
    listing = {**_MINIMAL_LISTING, "cluster_size": 3}
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert 5 in variants


def test_select_variants_returns_all_five_when_all_conditions_met():
    """Con tutte le condizioni → [1, 2, 3, 4, 5]."""
    listing = {
        **_MINIMAL_LISTING,
        "selling_signals": {"thumbnail_style": "editorial"},
        "gap_to_exploit": "Pain point: planners too rigid",
        "cluster_size": 5,
    }
    agent = _make_agent()
    variants = agent._select_variants(listing)
    assert sorted(variants) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Part 2 — Pin copy generation (async, mocked _call_llm)
# ---------------------------------------------------------------------------

def _mock_llm_response(title: str, description: str) -> str:
    return json.dumps({"title": title, "description": description})


@pytest.mark.asyncio
async def test_generate_pin_copy_returns_title_and_description():
    """_generate_pin_copy() ritorna una tupla (title, description, costs)."""
    agent = _make_agent()
    agent._call_llm = AsyncMock(return_value=_mock_llm_response(
        "The Planner That Finally Works for ADHD Brains",
        "Struggling with ADHD focus? This printable daily planner breaks your day into manageable chunks — download instantly. Works for even the busiest brains.",
    ))

    title, description, costs = await agent._generate_pin_copy(1, _MINIMAL_LISTING)

    assert isinstance(title, str)
    assert isinstance(description, str)
    assert isinstance(costs, dict)


@pytest.mark.asyncio
async def test_generate_pin_copy_title_max_100_chars():
    """Titolo > 100 caratteri viene troncato a 100."""
    long_title = "A" * 150  # 150 chars
    agent = _make_agent()
    agent._call_llm = AsyncMock(return_value=_mock_llm_response(
        long_title,
        "Struggling with ADHD focus? This printable daily planner breaks your day into manageable chunks — download instantly.",
    ))

    title, _, _ = await agent._generate_pin_copy(1, _MINIMAL_LISTING)

    assert len(title) <= 100


@pytest.mark.asyncio
async def test_generate_pin_copy_hashtags_stripped_from_description():
    """Hashtag nella descrizione vengono rimossi."""
    agent = _make_agent()
    agent._call_llm = AsyncMock(return_value=_mock_llm_response(
        "The Planner That Works for ADHD",
        "Great daily planner for ADHD adults. #ADHDPlanner #PrintableOrganizer #InstantDownload",
    ))

    _, description, _ = await agent._generate_pin_copy(1, _MINIMAL_LISTING)

    assert "#" not in description


@pytest.mark.asyncio
async def test_generate_pin_copy_uses_haiku_model_override():
    """_call_llm viene chiamata con model_override=MODEL_HAIKU."""
    from apps.backend.core.config import MODEL_HAIKU  # noqa: PLC0415

    agent = _make_agent()
    captured_kwargs: dict = {}

    async def fake_call_llm(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _mock_llm_response("Title", "Description of at least 150 chars to pass length constraints for this test pin.")

    agent._call_llm = fake_call_llm

    await agent._generate_pin_copy(1, _MINIMAL_LISTING)

    assert captured_kwargs.get("model_override") == MODEL_HAIKU


@pytest.mark.asyncio
async def test_generate_pin_copy_costs_dict_has_required_keys():
    """Il dict costs ha 'cost_llm' e 'cost_image_gen'."""
    agent = _make_agent()
    agent._call_llm = AsyncMock(return_value=_mock_llm_response(
        "Good title here",
        "Solid Pinterest description without hashtags covering benefit and CTA clearly for the audience.",
    ))

    _, _, costs = await agent._generate_pin_copy(1, _MINIMAL_LISTING)

    assert "cost_llm" in costs
    assert "cost_image_gen" in costs


# ---------------------------------------------------------------------------
# Part 3 — Scheduling (async, real DB)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_pins_inserts_rows_to_pinterest_queue(tmp_path):
    """_schedule_pins() inserisce righe in pinterest_queue."""
    mm = await _make_memory_base(tmp_path)
    agent = _make_agent(memory=mm)

    pins = [
        {"variant": 1, "image_path": "/img/a.jpg", "title": "Title A", "description": "Desc A", "cost_image_gen": 0.01, "cost_llm": 0.001},
        {"variant": 2, "image_path": "/img/b.jpg", "title": "Title B", "description": "Desc B", "cost_image_gen": 0.01, "cost_llm": 0.001},
    ]

    ids = await agent._schedule_pins(_MINIMAL_LISTING, pins)

    assert len(ids) == 2
    cursor = await mm._db.execute("SELECT COUNT(*) FROM pinterest_queue")
    row = await cursor.fetchone()
    assert row[0] == 2

    await mm._db.close()


@pytest.mark.asyncio
async def test_schedule_pin_1_offset_is_1_hour(tmp_path):
    """Il primo pin (slot 1) è schedulato a NOW + 1 ora."""
    mm = await _make_memory_base(tmp_path)
    agent = _make_agent(memory=mm)
    now = datetime.now(timezone.utc)

    pins = [
        {"variant": 1, "image_path": "", "title": "T", "description": "D", "cost_image_gen": 0.0, "cost_llm": 0.0},
    ]

    await agent._schedule_pins(_MINIMAL_LISTING, pins)

    cursor = await mm._db.execute("SELECT scheduled_at FROM pinterest_queue WHERE pin_variant=1")
    row = await cursor.fetchone()
    scheduled_str = row[0]
    # Parse ISO string — supporta sia "Z" che "+00:00"
    scheduled_dt = datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))
    expected = now + timedelta(hours=1)
    delta = abs((scheduled_dt - expected).total_seconds())
    assert delta < 5, f"Slot 1 off by {delta}s (expected +1h)"

    await mm._db.close()


@pytest.mark.asyncio
async def test_schedule_pin_2_offset_is_1_day(tmp_path):
    """Il secondo pin (slot 2) è schedulato a NOW + 1 giorno."""
    mm = await _make_memory_base(tmp_path)
    agent = _make_agent(memory=mm)
    now = datetime.now(timezone.utc)

    pins = [
        {"variant": 1, "image_path": "", "title": "T1", "description": "D1", "cost_image_gen": 0.0, "cost_llm": 0.0},
        {"variant": 2, "image_path": "", "title": "T2", "description": "D2", "cost_image_gen": 0.0, "cost_llm": 0.0},
    ]

    await agent._schedule_pins(_MINIMAL_LISTING, pins)

    cursor = await mm._db.execute("SELECT scheduled_at FROM pinterest_queue WHERE pin_variant=2")
    row = await cursor.fetchone()
    scheduled_dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    expected = now + timedelta(days=1)
    delta = abs((scheduled_dt - expected).total_seconds())
    assert delta < 5, f"Slot 2 off by {delta}s (expected +1d)"

    await mm._db.close()


@pytest.mark.asyncio
async def test_schedule_pins_stores_board_id_from_listing(tmp_path):
    """board_id dell'input listing viene salvato correttamente."""
    mm = await _make_memory_base(tmp_path)
    agent = _make_agent(memory=mm)

    pins = [
        {"variant": 1, "image_path": "", "title": "T", "description": "D", "cost_image_gen": 0.0, "cost_llm": 0.0},
    ]

    await agent._schedule_pins(_MINIMAL_LISTING, pins)

    cursor = await mm._db.execute("SELECT board_id FROM pinterest_queue")
    row = await cursor.fetchone()
    assert row[0] == "board_planners_001"

    await mm._db.close()


# ---------------------------------------------------------------------------
# Part 4 — Full generate_pins
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_pins_returns_list_of_dicts():
    """generate_pins() ritorna list[dict]."""
    agent = _make_agent()
    agent._generate_pin_image = AsyncMock(return_value=("", {"cost_image_gen": 0.0}))
    agent._generate_pin_copy = AsyncMock(return_value=("Title A", "Description A", {"cost_llm": 0.0, "cost_image_gen": 0.0}))
    agent._schedule_pins = AsyncMock(return_value=[1, 2])

    result = await agent.generate_pins(_MINIMAL_LISTING)

    assert isinstance(result, list)
    assert all(isinstance(p, dict) for p in result)


@pytest.mark.asyncio
async def test_generate_pins_result_has_required_fields():
    """Ogni pin nel risultato ha title, description, variant, image_path, cost_image_gen, cost_llm."""
    agent = _make_agent()
    agent._generate_pin_image = AsyncMock(return_value=("/img/a.jpg", {"cost_image_gen": 0.01}))
    agent._generate_pin_copy = AsyncMock(return_value=("Title A", "Description A", {"cost_llm": 0.001, "cost_image_gen": 0.0}))
    agent._schedule_pins = AsyncMock(return_value=[1, 2])

    result = await agent.generate_pins(_MINIMAL_LISTING)

    required = {"variant", "image_path", "title", "description", "cost_image_gen", "cost_llm"}
    for pin in result:
        missing = required - set(pin.keys())
        assert not missing, f"Campi mancanti nel pin: {missing}"


@pytest.mark.asyncio
async def test_generate_pins_calls_schedule_pins_with_all_generated_pins():
    """_schedule_pins viene chiamato con tutti i pin generati."""
    agent = _make_agent()
    agent._generate_pin_image = AsyncMock(return_value=("", {"cost_image_gen": 0.0}))
    agent._generate_pin_copy = AsyncMock(return_value=("T", "D", {"cost_llm": 0.0, "cost_image_gen": 0.0}))
    captured = {}
    async def fake_schedule(listing_data, pins):
        captured["pins"] = pins
        return [i + 1 for i in range(len(pins))]

    agent._schedule_pins = fake_schedule
    await agent.generate_pins(_MINIMAL_LISTING)

    # MINIMAL_LISTING → solo A e B → 2 pin
    assert len(captured["pins"]) == 2
    variants_generated = [p["variant"] for p in captured["pins"]]
    assert 1 in variants_generated
    assert 2 in variants_generated
