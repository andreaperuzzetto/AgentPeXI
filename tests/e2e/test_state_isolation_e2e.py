"""tests/e2e/test_state_isolation_e2e.py — Domain isolation: personal vs etsy.

Verifica che le operazioni del dominio 'personal' non contaminino il dominio 'etsy'
e viceversa, usando lo stesso MemoryManager su DB reale (file SQLite via tmp_path).

Schema reale (da _memory/_base.py):
  - production_queue  : nessun campo domain — distinzione via product_type / niche
  - agent_logs        : campo agent_name filtra per dominio (es. 'research' vs 'research_personal')
  - revenue_events    : nessun campo domain — tabella unificata Etsy-only per design

SI1 — Item personal non appare nelle query Etsy (production_queue)
SI2 — Finance tracker: record distinguibili per product_type e niche (tabella unificata)
SI3 — Agent logs research_personal non inquinano query per agente research (etsy)
SI4 — Stesso DB, scritture concorrenti su entrambi i domini, no collisione ID
"""
from __future__ import annotations

import asyncio

import pytest

from apps.backend.core.finance_tracker import FinanceTracker
from apps.backend.core.production_queue import ProductionQueueService
from tests.e2e.conftest import _make_memory_manager


# ---------------------------------------------------------------------------
# SI1 — production_queue: item personal non appare nelle query etsy
# ---------------------------------------------------------------------------

async def test_si1_personal_item_does_not_contaminate_etsy_queue(tmp_path):
    """ProductionQueueService: items filtrati per product_type non si contaminano.

    NOTE: production_queue non ha un campo domain esplicito. La distinzione
    avviene tramite product_type (Etsy usa 'printable_pdf', 'digital_art_png',
    'svg_bundle'; il dominio personal non scrive nella PQ in produzione).
    Il test simula la convivenza usando product_type='personal_task' per
    il dominio personal, e verifica che le query per tipo siano isolate.
    """
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)
    db = await mm.get_db()
    queue = ProductionQueueService(db)

    # Inserisci 1 item etsy e 1 item personal nella stessa PQ
    etsy_id = await asyncio.wait_for(
        queue.create_item(
            niche="wedding_planner",
            product_type="printable_pdf",
            keywords=["wedding", "planner"],
            entry_score=0.8,
        ),
        timeout=5,
    )
    personal_id = await asyncio.wait_for(
        queue.create_item(
            niche="personal_research",
            product_type="personal_task",
            keywords=["research"],
            entry_score=0.5,
        ),
        timeout=5,
    )

    # Query filtrata per tipo etsy
    cursor = await db.execute(
        "SELECT id, product_type FROM production_queue WHERE product_type = 'printable_pdf'"
    )
    etsy_rows = await cursor.fetchall()

    # Query filtrata per tipo personal
    cursor = await db.execute(
        "SELECT id, product_type FROM production_queue WHERE product_type = 'personal_task'"
    )
    personal_rows = await cursor.fetchall()

    # La query etsy restituisce solo l'item etsy
    assert len(etsy_rows) == 1, f"Atteso 1 item etsy, trovati {len(etsy_rows)}"
    assert etsy_rows[0]["id"] == etsy_id
    assert etsy_rows[0]["product_type"] == "printable_pdf"

    # La query personal restituisce solo l'item personal
    assert len(personal_rows) == 1, f"Atteso 1 item personal, trovati {len(personal_rows)}"
    assert personal_rows[0]["id"] == personal_id
    assert personal_rows[0]["product_type"] == "personal_task"

    # Verifica cross-contamination: nessun ID in comune
    etsy_ids = {row["id"] for row in etsy_rows}
    personal_ids = {row["id"] for row in personal_rows}
    assert personal_id not in etsy_ids, "Item personal appare nella query etsy"
    assert etsy_id not in personal_ids, "Item etsy appare nella query personal"


# ---------------------------------------------------------------------------
# SI2 — finance_tracker: tabella unificata, record distinguibili per field
# ---------------------------------------------------------------------------

async def test_si2_finance_tracker_records_distinguishable_by_product_type(tmp_path):
    """FinanceTracker: revenue_events non ha campo domain — i record sono
    distinguibili per product_type e niche.

    NOTE: finance_tracker usa tabella unificata (revenue_events) senza
    domain isolation esplicita. Non esiste un campo domain/source.
    In produzione solo vendite Etsy transitano qui; il dominio personal
    non usa revenue_events. Il test verifica che aggregati filtrati per
    product_type restituiscano il sottoinsieme corretto e che i totali
    per dominio non si sommino tra loro.
    """
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)
    tracker = FinanceTracker(memory=mm)

    # Vendita Etsy
    await asyncio.wait_for(
        tracker.record_sale(
            listing_id="etsy_listing_001",
            order_id="etsy_order_001",
            gross_eur=9.99,
            niche="wedding_planner",
            product_type="printable_pdf",
        ),
        timeout=5,
    )

    # Voce non-Etsy (simulazione dominio personal — non avviene in produzione,
    # ma verifica che se un record fosse inserito rimanga distinguibile e isolato)
    await asyncio.wait_for(
        tracker.record_sale(
            listing_id="personal_service_001",
            order_id="personal_order_001",
            gross_eur=50.0,
            niche="personal_coaching",
            product_type="personal_service",
        ),
        timeout=5,
    )

    db = await mm.get_db()

    # Query Etsy: solo printable_pdf
    cursor = await db.execute(
        "SELECT gross_eur FROM revenue_events WHERE product_type = 'printable_pdf'"
    )
    etsy_sales = await cursor.fetchall()
    assert len(etsy_sales) == 1
    assert abs(float(etsy_sales[0]["gross_eur"]) - 9.99) < 0.01

    # Query personal: solo personal_service
    cursor = await db.execute(
        "SELECT gross_eur FROM revenue_events WHERE product_type = 'personal_service'"
    )
    personal_sales = await cursor.fetchall()
    assert len(personal_sales) == 1
    assert abs(float(personal_sales[0]["gross_eur"]) - 50.0) < 0.01

    # Totale filtrato per etsy NON include il record personal
    cursor = await db.execute(
        "SELECT SUM(gross_eur) AS total FROM revenue_events WHERE product_type = 'printable_pdf'"
    )
    row = await cursor.fetchone()
    etsy_total = float(row["total"] or 0)

    # Totale complessivo include entrambi
    cursor = await db.execute("SELECT SUM(gross_eur) AS total FROM revenue_events")
    row = await cursor.fetchone()
    overall_total = float(row["total"] or 0)

    assert abs(etsy_total - 9.99) < 0.01, f"Totale etsy errato: {etsy_total}"
    assert abs(overall_total - 59.99) < 0.01, f"Totale complessivo errato: {overall_total}"
    # Il totale etsy è strettamente inferiore al totale globale (i record non si sommano)
    assert etsy_total < overall_total, (
        "I totali si sovrappongono: il record personal contamina il totale etsy"
    )


# ---------------------------------------------------------------------------
# SI3 — agent_logs: log research_personal non inquinano query research (etsy)
# ---------------------------------------------------------------------------

async def test_si3_personal_agent_logs_do_not_contaminate_etsy_logs(tmp_path):
    """AgentLogsMixin: log filtrati per agent_name sono isolati tra domini.

    'research'          → dominio etsy (ResearchAgent)
    'research_personal' → dominio personal (ResearchPersonalAgent)

    Il test verifica che la query su agent_name='research' restituisca
    esattamente i log dell'agente etsy e che il count non sia inflazionato
    dai log del dominio personal.
    """
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)

    # 2 task del dominio personal
    await asyncio.wait_for(
        mm.log_agent_task(
            agent_name="research_personal",
            task_id="personal-task-001",
            status="completed",
        ),
        timeout=5,
    )
    await asyncio.wait_for(
        mm.log_agent_task(
            agent_name="research_personal",
            task_id="personal-task-002",
            status="completed",
        ),
        timeout=5,
    )

    # 1 task del dominio etsy
    await asyncio.wait_for(
        mm.log_agent_task(
            agent_name="research",
            task_id="etsy-task-001",
            status="completed",
        ),
        timeout=5,
    )

    db = await mm.get_db()

    # Query log etsy: solo agent_name='research'
    cursor = await db.execute(
        "SELECT task_id FROM agent_logs WHERE agent_name = 'research'"
    )
    etsy_logs = await cursor.fetchall()

    # Query log personal: solo agent_name='research_personal'
    cursor = await db.execute(
        "SELECT task_id FROM agent_logs WHERE agent_name = 'research_personal'"
    )
    personal_logs = await cursor.fetchall()

    # Esattamente 1 log etsy (count non inflazionato dai log personal)
    assert len(etsy_logs) == 1, f"Atteso 1 log etsy, trovati {len(etsy_logs)}"
    assert etsy_logs[0]["task_id"] == "etsy-task-001"

    # Esattamente 2 log personal
    assert len(personal_logs) == 2, f"Atteso 2 log personal, trovati {len(personal_logs)}"
    personal_task_ids = {row["task_id"] for row in personal_logs}
    assert personal_task_ids == {"personal-task-001", "personal-task-002"}

    # Nessuna sovrapposizione tra i due insiemi
    etsy_task_ids = {row["task_id"] for row in etsy_logs}
    overlapping = etsy_task_ids & personal_task_ids
    assert not overlapping, (
        f"Log personal {overlapping} appaiono nella query etsy — contaminazione rilevata"
    )


# ---------------------------------------------------------------------------
# SI4 — Concurrent domain writes: worst-case test
# ---------------------------------------------------------------------------

async def test_si4_concurrent_domain_writes_no_id_collision(tmp_path):
    """ProductionQueueService: scritture concorrenti su entrambi i domini
    non producono collisioni di ID né contaminazione incrociata.

    asyncio.gather di 3 create_item (worst-case concurrent):
      - 2 item etsy  : digital_art_png + svg_bundle
      - 1 item personal : personal_task

    Verifica:
      - count etsy == 2, count personal == 1
      - tutti e 3 gli ID sono distinti (nessuna collisione SQLite AUTOINCREMENT)
      - gli ID etsy non compaiono nella query personal e viceversa
    """
    mm = _make_memory_manager(tmp_path)
    await asyncio.wait_for(mm.init(), timeout=5)
    db = await mm.get_db()
    queue = ProductionQueueService(db)

    # 3 scritture concorrenti
    etsy_id_1, etsy_id_2, personal_id = await asyncio.wait_for(
        asyncio.gather(
            queue.create_item(
                niche="digital_art",
                product_type="digital_art_png",
                keywords=["art", "digital"],
            ),
            queue.create_item(
                niche="svg_icons",
                product_type="svg_bundle",
                keywords=["svg", "icons"],
            ),
            queue.create_item(
                niche="personal_notes",
                product_type="personal_task",
                keywords=["notes"],
            ),
        ),
        timeout=5,
    )

    # Count etsy (digital_art_png + svg_bundle)
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM production_queue"
        " WHERE product_type IN ('digital_art_png', 'svg_bundle')"
    )
    row = await cursor.fetchone()
    etsy_count = row["cnt"]

    # Count personal
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM production_queue WHERE product_type = 'personal_task'"
    )
    row = await cursor.fetchone()
    personal_count = row["cnt"]

    assert etsy_count == 2, f"Atteso 2 item etsy, trovati {etsy_count}"
    assert personal_count == 1, f"Atteso 1 item personal, trovato {personal_count}"

    # Tutti gli ID devono essere distinti (AUTOINCREMENT non collide)
    all_ids = [etsy_id_1, etsy_id_2, personal_id]
    assert len(set(all_ids)) == 3, f"Collisione ID rilevata: {all_ids}"

    # Gli ID etsy non compaiono nella query personal e viceversa
    cursor = await db.execute(
        "SELECT id FROM production_queue"
        " WHERE product_type IN ('digital_art_png', 'svg_bundle')"
    )
    etsy_rows = await cursor.fetchall()
    etsy_ids = {row["id"] for row in etsy_rows}

    assert personal_id not in etsy_ids, (
        f"ID personal {personal_id} appare nella query etsy {etsy_ids}"
    )
    assert etsy_id_1 in etsy_ids, f"etsy_id_1 {etsy_id_1} mancante dalla query etsy"
    assert etsy_id_2 in etsy_ids, f"etsy_id_2 {etsy_id_2} mancante dalla query etsy"
