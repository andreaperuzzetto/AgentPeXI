"""tests/e2e/test_scheduler_safety_e2e.py

E2E safety tests: verifica che il doppio avvio dello scheduler non produca
job duplicati e che i job critici siano configurati correttamente.

APScheduler 3.x — AsyncIOScheduler.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from apps.backend.core.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def _make_scheduler() -> Scheduler:
    """Scheduler con tutte le dipendenze esterne mockate."""
    mem = MagicMock()
    mem.get_enabled_scheduled_tasks = AsyncMock(return_value=[])
    return Scheduler(memory=mem)


# ---------------------------------------------------------------------------
# CS1 — start() chiamato due volte → job count invariato
# ---------------------------------------------------------------------------

async def test_double_start_no_duplicate_jobs():
    """CS1: chiamare start() due volte non raddoppia i job registrati.

    La guardia ``if self._scheduler.running: return`` deve bloccare il secondo
    avvio senza alzare eccezioni e senza aggiungere job extra.
    """
    sched = _make_scheduler()
    try:
        await asyncio.wait_for(sched.start(), timeout=5)
        job_count_first = len(sched._scheduler.get_jobs())
        assert job_count_first > 0, "Nessun job registrato al primo start"

        # Secondo avvio — deve essere un no-op grazie alla guardia running
        await asyncio.wait_for(sched.start(), timeout=5)
        job_count_second = len(sched._scheduler.get_jobs())

        assert job_count_second == job_count_first, (
            f"Doppio start ha cambiato il numero di job: "
            f"{job_count_first} → {job_count_second}"
        )
        assert sched._scheduler.running
    finally:
        if sched._scheduler.running:
            sched._scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# CS2 — Job critici configurati con coalesce=True e max_instances=1
# ---------------------------------------------------------------------------

async def test_critical_jobs_have_safe_execution_config():
    """CS2: publish_checker e pinterest_publisher hanno coalesce=True e max_instances=1.

    Questo è un test di configurazione: verifica che i job ad alto rischio di
    sovrapposizione siano registrati con le opzioni di sicurezza corrette.
    """
    sched = _make_scheduler()
    try:
        await asyncio.wait_for(sched.start(), timeout=5)

        for job_id in ("publish_checker", "pinterest_publisher"):
            job = sched._scheduler.get_job(job_id)
            assert job is not None, f"Job critico '{job_id}' non trovato"
            assert job.coalesce is True, (
                f"'{job_id}' deve avere coalesce=True per prevenire accodamento"
            )
            assert job.max_instances == 1, (
                f"'{job_id}' deve avere max_instances=1 per prevenire esecuzioni parallele"
            )
    finally:
        if sched._scheduler.running:
            sched._scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# CS3 — stop() → start() → job count corretto (no accumulo)
# ---------------------------------------------------------------------------

async def test_restart_no_job_accumulation():
    """CS3: stop() + start() sullo stesso oggetto non accumula job extra.

    Dopo shutdown(), tutte le chiamate successive a ``_register_builtin_jobs``
    usano ``replace_existing=True``, quindi il numero di job rimane invariato.
    """
    sched = _make_scheduler()
    try:
        await asyncio.wait_for(sched.start(), timeout=5)
        job_count_initial = len(sched._scheduler.get_jobs())
        assert job_count_initial > 0, "Nessun job registrato al primo start"

        await asyncio.wait_for(sched.stop(), timeout=5)
        # APScheduler necessita di un'iterazione del loop per aggiornare running=False
        await asyncio.sleep(0.1)
        assert not sched._scheduler.running, (
            "Lo scheduler dovrebbe essere fermo dopo stop()"
        )

        # Riavvio sullo stesso oggetto — simula hot-restart
        await asyncio.wait_for(sched.start(), timeout=5)
        job_count_after_restart = len(sched._scheduler.get_jobs())

        assert job_count_after_restart == job_count_initial, (
            f"Dopo restart il job count è cambiato: "
            f"{job_count_initial} (iniziale) vs {job_count_after_restart} (dopo restart)"
        )
    finally:
        if sched._scheduler.running:
            sched._scheduler.shutdown(wait=False)
