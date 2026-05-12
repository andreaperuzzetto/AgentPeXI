"""tests/e2e/test_cross_component_e2e.py — Cross-component data-consistency tests.

Verifica che i dati scritti da un componente siano leggibili correttamente
da un altro componente usando lo stesso DB SQLite reale (condiviso via MemoryManager).

CC1 — Research scrive cluster → PQ può leggere l'item
CC2 — Publisher aggiorna stato in PQ → autopilot legge lo stato aggiornato
CC3 — Finance tracker scrive revenue → analytics può aggregare
CC4 — Agent logs scritti da un agente → leggibili via memory manager
CC5 — DB condiviso: due componenti che scrivono su tabelle diverse non si interferiscono
"""
from __future__ import annotations

import asyncio
import warnings

import pytest

from apps.backend.core.finance_tracker import FinanceTracker
from tests.e2e.conftest import _make_memory_manager


# ---------------------------------------------------------------------------
# CC1 — Research scrive cluster → PQ può leggere l'item
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cc1_research_writes_cluster_pq_can_read(tmp_path):
    """Research writes a cluster item to PQ; all key fields are readable and coherent."""
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)

    task_id = "cc1-research-cluster-001"
    brief = {
        "cluster_id": "cluster_wedding_planner_v1",
        "keywords": ["wedding planner", "printable", "checklist"],
        "niche": "wedding_planner",
        "release_order": 1,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        await asyncio.wait_for(
            mm.add_to_production_queue(
                task_id=task_id,
                product_type="printable_pdf",
                niche="wedding_planner",
                brief=brief,
            ),
            timeout=5,
        )

    item = await asyncio.wait_for(mm.get_production_queue_item(task_id), timeout=5)

    assert item is not None, "PQ item not found after Research write"
    assert item["task_id"] == task_id
    assert item["niche"] == "wedding_planner"
    assert item["product_type"] == "printable_pdf"
    assert item["status"] == "pending_design"
    assert isinstance(item["brief"], dict), "brief must be deserialized to dict (not raw JSON string)"
    assert item["brief"]["cluster_id"] == "cluster_wedding_planner_v1"
    assert item["brief"]["release_order"] == 1


# ---------------------------------------------------------------------------
# CC2 — Publisher aggiorna stato in PQ → autopilot legge lo stato aggiornato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cc2_publisher_updates_status_autopilot_reads(tmp_path):
    """Publisher updates PQ item status and file_paths; autopilot reads the new state."""
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)

    task_id = "cc2-publisher-status-001"
    file_paths = ["/storage/designs/wedding_planner_v1.pdf"]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        await asyncio.wait_for(
            mm.add_to_production_queue(
                task_id=task_id,
                product_type="printable_pdf",
                niche="wedding_planner",
                brief={"niche": "wedding_planner"},
            ),
            timeout=5,
        )

    # Verify initial state before publisher acts
    initial = await asyncio.wait_for(mm.get_production_queue_item(task_id), timeout=5)
    assert initial["status"] == "pending_design"

    # Simulate publisher updating the status after successful publication
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        await asyncio.wait_for(
            mm.update_production_queue_status(
                task_id=task_id,
                status="published",
                file_paths=file_paths,
            ),
            timeout=5,
        )

    # Autopilot reads updated state via the same MM interface
    updated = await asyncio.wait_for(mm.get_production_queue_item(task_id), timeout=5)

    assert updated is not None
    assert updated["status"] == "published", (
        f"Expected status='published', got {updated['status']!r}"
    )
    assert updated["file_paths"] == file_paths, (
        f"Expected file_paths={file_paths!r}, got {updated['file_paths']!r}"
    )


# ---------------------------------------------------------------------------
# CC3 — Finance tracker scrive revenue → analytics può aggregare
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cc3_finance_writes_revenue_analytics_aggregates(tmp_path):
    """FinanceTracker records N sales; monthly_summary aggregates them correctly."""
    from datetime import datetime, timezone

    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)

    finance = FinanceTracker(memory=mm)

    sales = [
        {"listing_id": "etsy_cc3_001", "order_id": "ord_cc3_001", "gross_eur": 5.00, "niche": "planner"},
        {"listing_id": "etsy_cc3_001", "order_id": "ord_cc3_002", "gross_eur": 4.50, "niche": "planner"},
        {"listing_id": "etsy_cc3_002", "order_id": "ord_cc3_003", "gross_eur": 7.99, "niche": "wedding"},
    ]
    expected_gross = sum(s["gross_eur"] for s in sales)  # 17.49

    for sale in sales:
        await asyncio.wait_for(
            finance.record_sale(
                listing_id=sale["listing_id"],
                order_id=sale["order_id"],
                gross_eur=sale["gross_eur"],
                niche=sale["niche"],
                product_type="printable_pdf",
            ),
            timeout=5,
        )

    now = datetime.now(timezone.utc)
    summary = await asyncio.wait_for(
        finance.monthly_summary(year=now.year, month=now.month),
        timeout=5,
    )

    assert summary["n_sales"] == len(sales), (
        f"Expected {len(sales)} sales, got {summary['n_sales']}"
    )
    assert abs(summary["gross_eur"] - expected_gross) < 0.01, (
        f"Expected gross_eur≈{expected_gross:.2f}, got {summary['gross_eur']}"
    )
    assert summary["net_eur"] < summary["gross_eur"], (
        "net_eur must be lower than gross_eur after Etsy fees"
    )
    assert summary["net_eur"] > 0, (
        "net_eur must be positive for these sale prices"
    )


# ---------------------------------------------------------------------------
# CC4 — Agent logs scritti da un agente → leggibili via memory manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cc4_agent_logs_written_readable_with_filter(tmp_path):
    """Logs from multiple agents are readable with correct per-agent filtering and step ordering."""
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)

    # Two different agents write their logs
    await asyncio.wait_for(
        mm.log_agent_task(
            agent_name="research",
            task_id="cc4-research-task-001",
            status="running",
            input_data={"niche": "planner"},
        ),
        timeout=5,
    )
    await asyncio.wait_for(
        mm.log_agent_task(
            agent_name="design",
            task_id="cc4-design-task-001",
            status="running",
            input_data={"niche": "planner", "template": "minimal"},
        ),
        timeout=5,
    )
    await asyncio.wait_for(
        mm.log_agent_task(
            agent_name="research",
            task_id="cc4-research-task-002",
            status="completed",
            input_data={"niche": "wedding"},
        ),
        timeout=5,
    )

    # Research agent also writes two steps
    await asyncio.wait_for(
        mm.log_step(
            task_id="cc4-research-task-001",
            agent_name="research",
            step_number=1,
            step_type="analysis",
            description="Cluster keyword analysis",
        ),
        timeout=5,
    )
    await asyncio.wait_for(
        mm.log_step(
            task_id="cc4-research-task-001",
            agent_name="research",
            step_number=2,
            step_type="output",
            description="Generated cluster brief",
        ),
        timeout=5,
    )

    # get_task_by_id roundtrip: design agent's log is readable and has correct fields
    task = await asyncio.wait_for(
        mm.get_task_by_id("cc4-design-task-001"),
        timeout=5,
    )
    assert task is not None
    assert task["agent_name"] == "design"
    assert isinstance(task["input_data"], dict), "input_data must be deserialized to dict"
    assert task["input_data"]["template"] == "minimal"

    # Steps filtered by agent — only research steps are returned
    steps = await asyncio.wait_for(
        mm.get_recent_agent_steps(limit=50, agent_name="research"),
        timeout=5,
    )
    assert len(steps) == 2, f"Expected 2 steps for research, got {len(steps)}"
    for step in steps:
        assert step["agent_name"] == "research", (
            f"Step from unexpected agent: {step['agent_name']!r}"
        )

    # Steps are in chronological (ascending) order
    step_numbers = [s["step_number"] for s in steps]
    assert step_numbers == sorted(step_numbers), (
        f"Steps not in chronological order: {step_numbers}"
    )


# ---------------------------------------------------------------------------
# CC5 — DB condiviso: due componenti che scrivono su tabelle diverse non si interferiscono
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cc5_shared_db_concurrent_writes_no_interference(tmp_path):
    """Concurrent writes to production_queue and revenue_events complete without errors."""
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)

    finance = FinanceTracker(memory=mm)

    async def _write_pq() -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            await mm.add_to_production_queue(
                task_id="cc5-pq-task-001",
                product_type="printable_pdf",
                niche="productivity",
                brief={"keywords": ["daily planner"]},
            )

    async def _write_revenue() -> None:
        await finance.record_sale(
            listing_id="etsy_cc5_001",
            order_id="ord_cc5_001",
            gross_eur=6.99,
            niche="productivity",
            product_type="printable_pdf",
        )

    # Both writes run concurrently — different tables, no shared row locks
    await asyncio.wait_for(
        asyncio.gather(_write_pq(), _write_revenue()),
        timeout=5,
    )

    # Verify PQ write succeeded
    pq_item = await asyncio.wait_for(
        mm.get_production_queue_item("cc5-pq-task-001"),
        timeout=5,
    )
    assert pq_item is not None, "PQ item missing after concurrent write"
    assert pq_item["task_id"] == "cc5-pq-task-001"
    assert pq_item["niche"] == "productivity"

    # Verify revenue write succeeded — query raw DB to bypass RevenueMixin (cross-component)
    db = await mm.get_db()
    cursor = await db.execute(
        "SELECT gross_eur FROM revenue_events WHERE order_id = ?",
        ("ord_cc5_001",),
    )
    row = await cursor.fetchone()
    assert row is not None, "Revenue event missing after concurrent write"
    assert abs(float(row[0]) - 6.99) < 0.01, (
        f"Expected gross_eur≈6.99, got {row[0]}"
    )
