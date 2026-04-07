"""
Gmail Poller — Celery Beat task (ogni 5 minuti).

Controlla l'inbox Gmail per nuove email da clienti esistenti e dispatcha
task di classificazione al Support Agent.

Sicurezza:
- Non logga MAI indirizzi email, mittenti o corpo email (PII)
- Traccia i message_id già processati in Redis (TTL 7 giorni)
- Non esegue mai il Support Agent direttamente: dispatch via Celery
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime

import redis.asyncio as aioredis
import structlog
from celery import shared_task
from sqlalchemy import text

from agents.models import AgentTask
from db.session import get_db_session

log = structlog.get_logger()

REDIS_URL: str = os.environ["REDIS_URL"]

# Prefisso Redis per tracciare i message_id già processati
_PROCESSED_KEY_PREFIX = "gmail_processed:"
# TTL: 7 giorni — evita riprocessamento su restart + pulisce automaticamente
_PROCESSED_TTL_SECONDS = 7 * 24 * 3600

# Numero massimo di email da controllare per ciclo (evita timeout)
_MAX_UNREAD = 30


@shared_task(name="agents.gmail_poller")
def poll_gmail() -> None:
    """Entry point Celery Beat — sync wrapper. Non rendere async."""
    asyncio.run(_poll_gmail_async())


async def _poll_gmail_async() -> None:
    """
    Lista le email non lette, filtra quelle già processate o da mittenti sconosciuti,
    e dispatcha task di classificazione al Support Agent per ogni email rilevante.
    """
    # Import lazy per evitare circolarità
    from tools.gmail import list_unread  # noqa: PLC0415

    try:
        unread = await list_unread(max_results=_MAX_UNREAD)
    except Exception as exc:
        log.error("gmail_poller.list_failed", error=str(exc))
        return

    if not unread:
        return

    log.info("gmail_poller.checking", unread_count=len(unread))

    r = aioredis.from_url(REDIS_URL)
    try:
        for msg in unread:
            message_id: str = msg.get("message_id", "")
            thread_id: str = msg.get("thread_id", "")
            if not message_id or not thread_id:
                continue

            # ── Skip già processati ────────────────────────────────────────────
            redis_key = f"{_PROCESSED_KEY_PREFIX}{message_id}"
            already_done = await r.get(redis_key)
            if already_done:
                continue

            # ── Lookup client da indirizzo mittente ────────────────────────────
            # Il campo "from" è PII: usato solo per lookup, mai loggato
            sender_email: str = msg.get("from", "")
            client_info = await _find_client_by_email(sender_email)

            if client_info is None:
                # Mittente sconosciuto — non è un cliente, skip silenzioso
                # Marca come processato per non ri-controllarlo
                await r.setex(redis_key, _PROCESSED_TTL_SECONDS, "unknown_sender")
                continue

            client_id, deal_id = client_info

            # ── Dispatch task Support Agent ────────────────────────────────────
            await _dispatch_support_task(
                thread_id=thread_id,
                client_id=client_id,
                deal_id=deal_id,
                subject=msg.get("subject", ""),
            )

            # Marca come processato in Redis
            await r.setex(redis_key, _PROCESSED_TTL_SECONDS, "dispatched")

            log.info(
                "gmail_poller.dispatched",
                thread_id=thread_id,
                client_id=str(client_id),
            )
    finally:
        await r.aclose()


async def _find_client_by_email(sender_email: str) -> tuple[str, str | None] | None:
    """
    Cerca il client_id e deal_id attivi per il mittente.
    Nota: contact_email è cifrato a riposo (EncryptedType) — il confronto
    avviene a livello ORM dopo decryption.

    Ritorna (client_id, deal_id) oppure None se mittente sconosciuto.
    Non logga mai l'email.
    """
    if not sender_email:
        return None

    async with get_db_session() as db:
        # SQLAlchemy decripta contact_email via EncryptedType nel modello ORM.
        # Non possiamo fare WHERE sul campo cifrato → carichiamo client attivi
        # (deleted_at IS NULL) e confrontiamo in Python.
        # In produzione con molti clienti, usare un indice su email hash dedicato.
        rows = await db.execute(
            text(
                "SELECT id, contact_email FROM clients WHERE deleted_at IS NULL"
            )
        )
        clients = rows.fetchall()

    sender_lower = sender_email.strip().lower()
    matched_client_id: str | None = None
    for row in clients:
        raw_email = row[1]
        if raw_email and str(raw_email).strip().lower() == sender_lower:
            matched_client_id = str(row[0])
            break

    if matched_client_id is None:
        return None

    # Trova l'ultimo deal attivo per questo client
    async with get_db_session() as db:
        deal_row = await db.execute(
            text(
                "SELECT id FROM deals "
                "WHERE client_id = :cid "
                "  AND status NOT IN ('lost', 'cancelled') "
                "  AND deleted_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": matched_client_id},
        )
        deal_id_raw = deal_row.scalar_one_or_none()

    deal_id: str | None = str(deal_id_raw) if deal_id_raw else None
    return matched_client_id, deal_id


async def _dispatch_support_task(
    *,
    thread_id: str,
    client_id: str,
    deal_id: str | None,
    subject: str,
) -> None:
    """
    Crea e invia un task Celery per il Support Agent.
    Import locale dell'app Celery per evitare circolarità.
    Non logga PII (subject potrebbe contenere dati sensibili — usato solo nel payload).
    """
    from agents.worker import celery_app  # noqa: PLC0415

    task_id = str(uuid.uuid4())
    task = AgentTask(
        id=task_id,
        type="support.classify",
        agent="support",
        deal_id=deal_id,
        client_id=client_id,
        payload={
            "action": "classify",
            "client_id": client_id,
            "deal_id": deal_id,
            "email_thread_id": thread_id,
            "subject": subject[:200],  # truncated — PII minimization
        },
    )

    celery_app.send_task(
        "agents.support.run",
        args=[task.model_dump(mode="json")],
        task_id=task_id,
    )
