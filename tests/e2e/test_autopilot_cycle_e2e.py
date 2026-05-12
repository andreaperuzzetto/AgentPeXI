"""tests/e2e/test_autopilot_cycle_e2e.py — Component integration: Design→approval→Publisher cycle.

Verifica il ciclo completo Design→approval→Publisher usando SQLite reale su tmpdir.

Mocked: anthropic_client (LLM), etsy_api, storage filesystem,
        publisher._publish_single (layer Etsy/LLM interno).
Real:   SQLite + schema MemoryManager, ProductionQueueService state machine.

AC1: item entra in PQ con stato "pending_approval" dopo set_design_ready
AC2: approval signal (set_approved) → stato "approved"
AC3: Publisher.run() → set_published → stato "published", move_to_uploaded × 1
AC4: ciclo completo sequenziale D→A→P senza item residui in stati intermedi
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from apps.backend.agents.publisher import PublisherAgent
from apps.backend.core.models import AgentTask
from apps.backend.core.production_queue import ProductionQueueService
from tests.e2e.conftest import _make_memory_manager

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_FAKE_LISTING_ID = "mock_listing_88888"

_FAKE_PUBLISH_RESULT: dict = {
    "niche": "placeholder",          # sovrascritto per-test
    "file_type": "printable_pdf",
    "template": "",
    "color_scheme": "",
    "ab_variant": "A",
    "listing_id": _FAKE_LISTING_ID,
    "images_uploaded": 0,
    "seo_validated": True,
    "status": "published",
    "price_source": "fallback_hardcoded",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_pq(tmp_path):
    """MemoryManager con SQLite tmpdir + PQService che condivide la stessa connessione."""
    mm = _make_memory_manager(tmp_path)
    await mm.init()
    db = await mm.get_db()
    pq = ProductionQueueService(db)
    return mm, pq


def _make_publisher(mm):
    """PublisherAgent con tutte le dipendenze esterne mockate, MemoryManager reale."""
    mock_anthropic = MagicMock(spec=anthropic.AsyncAnthropic)
    mock_storage = MagicMock()
    mock_storage.is_available.return_value = True
    # move_to_uploaded è un metodo SYNC (non awaited in publisher.py)
    mock_storage.move_to_uploaded = MagicMock(return_value=Path("/fake/uploaded/file.pdf"))
    mock_etsy_api = MagicMock()
    mock_etsy_api.mock_mode = True

    publisher = PublisherAgent(
        anthropic_client=mock_anthropic,
        memory=mm,
        storage=mock_storage,
        etsy_api=mock_etsy_api,
        telegram_broadcaster=None,
    )
    return publisher, mock_storage


async def _transition_to_scheduled(pq: ProductionQueueService, item_id: int) -> None:
    """Porta un item da pending_design → scheduled via transizioni di stato reali."""
    await pq.set_design_ready(
        item_id=item_id,
        design_prompt="minimalist daily planner",
        image_url="https://cdn.example.com/img.png",
        thumbnail_path="/tmp/thumb.jpg",
        title="Daily Planner Printable",
        description="A beautiful daily planner",
        tags=["planner", "printable"],
        price=4.99,
    )
    await pq.set_approved(item_id)
    await pq.assign_slot(item_id, time.time() + 3600)


async def _get_task_id(mm, item_id: int) -> str:
    """Legge task_id UUID dal DB (campo non esposto dal dataclass ProductionQueueItem)."""
    db = await mm.get_db()
    cursor = await db.execute(
        "SELECT task_id FROM production_queue WHERE id = ?", (item_id,)
    )
    row = await cursor.fetchone()
    assert row is not None, f"Item {item_id} non trovato in DB"
    return row["task_id"]


# ---------------------------------------------------------------------------
# AC1 — Design item entra in PQ con stato "pending_approval"
# ---------------------------------------------------------------------------

async def test_ac1_design_item_enters_pq_with_pending_approval(tmp_path):
    """AC1: set_design_ready porta l'item in 'pending_approval' e lo rende
    recuperabile via get_items_by_status.
    """
    mm, pq = await asyncio.wait_for(_make_pq(tmp_path), timeout=10)

    item_id = await asyncio.wait_for(
        pq.create_item(
            niche="planner_niche",
            product_type="printable_pdf",
            keywords=["planner"],
        ),
        timeout=10,
    )

    # Stato iniziale atteso: pending_design
    item_initial = await pq.get_item(item_id)
    assert item_initial.status == "pending_design"

    # Simula completamento design
    await asyncio.wait_for(
        pq.set_design_ready(
            item_id=item_id,
            design_prompt="minimalist daily planner",
            image_url="https://cdn.example.com/img.png",
            thumbnail_path="/tmp/thumb.jpg",
            title="Daily Planner Printable",
            description="A beautiful daily planner",
            tags=["planner", "printable"],
            price=4.99,
        ),
        timeout=10,
    )

    item = await asyncio.wait_for(pq.get_item(item_id), timeout=10)
    assert item is not None
    assert item.status == "pending_approval"

    # Recuperabile via get_items_by_status
    pending = await asyncio.wait_for(
        pq.get_items_by_status("pending_approval"), timeout=10
    )
    assert any(i.id == item_id for i in pending), (
        f"Item {item_id} non trovato in pending_approval list"
    )


# ---------------------------------------------------------------------------
# AC2 — Approval signal → stato transisce a "approved"
# ---------------------------------------------------------------------------

async def test_ac2_approval_signal_transitions_to_approved(tmp_path):
    """AC2: set_approved (come farebbe autopilot.py via register_approval) porta
    l'item da 'pending_approval' ad 'approved' e lo rimuove dalla pending list.
    """
    mm, pq = await asyncio.wait_for(_make_pq(tmp_path), timeout=10)

    item_id = await pq.create_item(
        niche="approval_niche",
        product_type="printable_pdf",
        keywords=[],
    )
    await pq.set_design_ready(
        item_id=item_id,
        design_prompt="test",
        image_url="u",
        thumbnail_path="t",
        title="T",
        description="D",
        tags=[],
        price=3.99,
    )

    # Approval signal: autopilot.py chiama loop.register_approval(item_id, "approved")
    # → AutopilotLoop imposta _approval_results e poi chiama pq.set_approved.
    await asyncio.wait_for(pq.set_approved(item_id), timeout=10)

    item = await asyncio.wait_for(pq.get_item(item_id), timeout=10)
    assert item is not None
    assert item.status == "approved"

    # Non deve più comparire nella lista pending_approval
    pending = await pq.get_items_by_status("pending_approval")
    assert not any(i.id == item_id for i in pending), (
        "Item ancora in pending_approval dopo set_approved"
    )


# ---------------------------------------------------------------------------
# AC3 — Publisher.run() consuma item → stato = "published"
# ---------------------------------------------------------------------------

async def test_ac3_publisher_run_transitions_to_published(tmp_path):
    """AC3: publisher.run() deve:
    1. Aggiornare lo stato dell'item a 'published' nel DB reale (via _PQService.set_published)
    2. Chiamare storage.move_to_uploaded esattamente 1 volta

    Nota: _PQService.set_published() richiede stato 'scheduled', quindi il setup
    porta l'item fino a scheduled prima di invocare publisher.run().
    """
    mm, pq = await asyncio.wait_for(_make_pq(tmp_path), timeout=10)
    publisher, mock_storage = _make_publisher(mm)

    # File fittizio reale: publisher.run() chiama Path(fp).is_file()
    fake_pdf = tmp_path / "test_product.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content e2e")

    # Setup: pending_design → pending_approval → approved → scheduled
    item_id = await pq.create_item(
        niche="publisher_niche",
        product_type="printable_pdf",
        keywords=["test"],
    )
    await _transition_to_scheduled(pq, item_id)

    item_before = await pq.get_item(item_id)
    assert item_before.status == "scheduled"

    pq_task_id = await _get_task_id(mm, item_id)
    fake_result = {**_FAKE_PUBLISH_RESULT, "niche": "publisher_niche"}

    # Mock _publish_single per evitare chiamate a Etsy API / LLM
    with patch.object(publisher, "_publish_single", new_callable=AsyncMock) as mock_publish:
        mock_publish.return_value = fake_result

        task = AgentTask(
            agent_name="publisher",
            task_id="e2e-ac3",
            input_data={
                "file_paths": [str(fake_pdf)],
                "niche": "publisher_niche",
                "product_type": "printable_pdf",
                "template": "",
                "keywords": ["test"],
                "production_queue_task_id": pq_task_id,
                "research_context": {},
            },
        )
        await asyncio.wait_for(publisher.run(task), timeout=10)

    # storage.move_to_uploaded chiamato esattamente 1 volta (1 file, 1 listing creato)
    mock_storage.move_to_uploaded.assert_called_once()

    # Stato "published" nel DB reale
    item_after = await asyncio.wait_for(pq.get_item(item_id), timeout=10)
    assert item_after is not None
    assert item_after.status == "published"
    assert item_after.etsy_listing_id == _FAKE_LISTING_ID


# ---------------------------------------------------------------------------
# AC4 — Ciclo completo sequenziale Design→approval→published
# ---------------------------------------------------------------------------

async def test_ac4_full_design_approval_publish_cycle(tmp_path):
    """AC4: Ciclo completo Design→approval→published sullo stesso DB in tmpdir.

    Verifica:
    - Gli stati attraversano le 3 fasi in ordine senza corruzioni del DB
    - Nessun item residuo in stati intermedi (pending_approval, approved, scheduled)
      al termine del ciclo
    """
    mm, pq = await asyncio.wait_for(_make_pq(tmp_path), timeout=10)
    publisher, _mock_storage = _make_publisher(mm)

    fake_pdf = tmp_path / "cycle_product.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 cycle test e2e")

    # === Fase 1: Design → pending_approval (AC1) ===
    item_id = await asyncio.wait_for(
        pq.create_item(
            niche="cycle_niche",
            product_type="printable_pdf",
            keywords=["cycle"],
        ),
        timeout=10,
    )
    await asyncio.wait_for(
        pq.set_design_ready(
            item_id=item_id,
            design_prompt="cycle test prompt",
            image_url="https://cdn.example.com/cycle.png",
            thumbnail_path="/tmp/cycle_thumb.jpg",
            title="Cycle Planner",
            description="Full cycle test",
            tags=["cycle", "test"],
            price=5.99,
        ),
        timeout=10,
    )
    item = await pq.get_item(item_id)
    assert item.status == "pending_approval", f"Fase 1 fallita: {item.status!r}"

    # === Fase 2: Approval signal → approved (AC2) ===
    await asyncio.wait_for(pq.set_approved(item_id), timeout=10)
    item = await pq.get_item(item_id)
    assert item.status == "approved", f"Fase 2 fallita: {item.status!r}"

    # Transizione a scheduled (necessaria perché set_published la richiede)
    await asyncio.wait_for(pq.assign_slot(item_id, time.time() + 3600), timeout=10)
    item = await pq.get_item(item_id)
    assert item.status == "scheduled", f"Transizione scheduled fallita: {item.status!r}"

    # === Fase 3: Publisher.run() → published (AC3) ===
    pq_task_id = await _get_task_id(mm, item_id)
    fake_result = {**_FAKE_PUBLISH_RESULT, "niche": "cycle_niche"}

    with patch.object(publisher, "_publish_single", new_callable=AsyncMock) as mock_publish:
        mock_publish.return_value = fake_result

        task = AgentTask(
            agent_name="publisher",
            task_id="e2e-ac4",
            input_data={
                "file_paths": [str(fake_pdf)],
                "niche": "cycle_niche",
                "product_type": "printable_pdf",
                "template": "",
                "keywords": ["cycle"],
                "production_queue_task_id": pq_task_id,
                "research_context": {},
            },
        )
        await asyncio.wait_for(publisher.run(task), timeout=10)

    item_final = await asyncio.wait_for(pq.get_item(item_id), timeout=10)
    assert item_final.status == "published", f"Fase 3 fallita: {item_final.status!r}"
    assert item_final.etsy_listing_id == _FAKE_LISTING_ID

    # Nessun item residuo in stati intermedi alla fine del ciclo
    for mid_status in ("pending_approval", "approved", "scheduled"):
        residual = await pq.get_items_by_status(mid_status)
        assert not any(i.id == item_id for i in residual), (
            f"Item residuo in stato intermedio '{mid_status}' al termine del ciclo"
        )
