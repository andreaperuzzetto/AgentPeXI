from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, Request
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import decode_portal_token
from api.schemas.webhooks import (
    PortalClientApproveRequest,
    PortalClientDeliveryConfirmRequest,
    PortalClientRejectRequest,
)
from db.models.deal import Deal
from db.models.proposal import Proposal
from db.session import get_db_session

log = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _verify_portal_auth(request: Request) -> str:
    """
    Verifica l'header Authorization: Bearer {portal_jwt}.
    Restituisce il token JWT grezzo per ulteriori controlli.
    Solleva 401 se assente o invalido.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": "missing_portal_token", "message": "Token portale mancante", "detail": {}},
        )
    return auth_header.removeprefix("Bearer ").strip()


async def _get_proposal_or_400(
    proposal_id: str, db: AsyncSession
) -> Proposal:
    try:
        uid = uuid.UUID(proposal_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_uuid", "message": "proposal_id non valido", "detail": {}},
        )
    result = await db.execute(
        select(Proposal).where(Proposal.id == uid, Proposal.deleted_at.is_(None))
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "proposal_not_found", "message": "Proposta non trovata", "detail": {}},
        )
    return proposal


async def _get_deal_or_400(deal_id: uuid.UUID, db: AsyncSession) -> Deal:
    result = await db.execute(
        select(Deal).where(Deal.id == deal_id, Deal.deleted_at.is_(None))
    )
    deal = result.scalar_one_or_none()
    if deal is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": "Deal non trovato", "detail": {}},
        )
    return deal


# ── POST /webhooks/portal/client-approve ─────────────────────────────────────

@router.post("/portal/client-approve")
async def portal_client_approve(
    request: Request,
    body: PortalClientApproveRequest,
) -> dict[str, str]:
    """
    Il cliente approva la proposta via portale.
    Verifica JWT con PORTAL_SECRET_KEY, aggiorna proposal e deal.
    """
    _bearer = await _verify_portal_auth(request)

    try:
        claims = decode_portal_token(body.token)
    except InvalidTokenError:
        raise HTTPException(
            status_code=400,
            detail={"error": "token_expired", "message": "Token scaduto o non valido", "detail": {}},
        )

    now = datetime.utcnow()

    async with get_db_session() as db:
        proposal = await _get_proposal_or_400(body.proposal_id, db)

        # Verifica scadenza link
        if proposal.portal_link_expires and proposal.portal_link_expires < now:
            raise HTTPException(
                status_code=400,
                detail={"error": "token_expired", "message": "Link portale scaduto", "detail": {}},
            )

        # Idempotenza
        if proposal.client_response is not None:
            raise HTTPException(
                status_code=409,
                detail={"error": "already_responded", "message": "Il cliente ha già risposto", "detail": {}},
            )

        await db.execute(
            update(Proposal)
            .where(Proposal.id == proposal.id)
            .values(
                client_response="approved",
                client_response_at=now,
                updated_at=now,
            )
        )

        deal = await _get_deal_or_400(proposal.deal_id, db)
        await db.execute(
            update(Deal)
            .where(Deal.id == deal.id)
            .values(status="client_approved", updated_at=now)
        )

    log.info("webhook.portal.client_approved", proposal_id=body.proposal_id)
    return {"message": "Proposta approvata. Verrete contattati per il kickoff."}


# ── POST /webhooks/portal/client-reject ──────────────────────────────────────

@router.post("/portal/client-reject")
async def portal_client_reject(
    request: Request,
    body: PortalClientRejectRequest,
) -> dict[str, str]:
    """Il cliente rifiuta la proposta via portale."""
    await _verify_portal_auth(request)

    try:
        decode_portal_token(body.token)
    except InvalidTokenError:
        raise HTTPException(
            status_code=400,
            detail={"error": "token_expired", "message": "Token scaduto o non valido", "detail": {}},
        )

    now = datetime.utcnow()

    async with get_db_session() as db:
        proposal = await _get_proposal_or_400(body.proposal_id, db)

        if proposal.portal_link_expires and proposal.portal_link_expires < now:
            raise HTTPException(
                status_code=400,
                detail={"error": "token_expired", "message": "Link portale scaduto", "detail": {}},
            )

        if proposal.client_response is not None:
            raise HTTPException(
                status_code=409,
                detail={"error": "already_responded", "message": "Il cliente ha già risposto", "detail": {}},
            )

        await db.execute(
            update(Proposal)
            .where(Proposal.id == proposal.id)
            .values(
                client_response="rejected",
                client_response_at=now,
                client_notes=body.notes,
                updated_at=now,
            )
        )

        deal = await _get_deal_or_400(proposal.deal_id, db)
        await db.execute(
            update(Deal)
            .where(Deal.id == deal.id)
            .values(status="lost", lost_reason=body.notes, updated_at=now)
        )

    log.info("webhook.portal.client_rejected", proposal_id=body.proposal_id)
    return {"message": "Grazie per il feedback."}


# ── POST /webhooks/portal/client-delivery-confirm ────────────────────────────

@router.post("/portal/client-delivery-confirm")
async def portal_client_delivery_confirm(
    request: Request,
    body: PortalClientDeliveryConfirmRequest,
) -> dict[str, str]:
    """
    Il cliente conferma la consegna (GATE 3) via portale.
    Aggiorna deal.delivery_approved o deal.consulting_approved in base al service_type.
    """
    await _verify_portal_auth(request)

    try:
        claims = decode_portal_token(body.token)
    except InvalidTokenError:
        raise HTTPException(
            status_code=400,
            detail={"error": "token_expired", "message": "Token scaduto o non valido", "detail": {}},
        )

    # Verifica che il token sia di tipo delivery
    if claims.get("gate") != "delivery":
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_gate", "message": "Il token non è di tipo delivery", "detail": {}},
        )

    now = datetime.utcnow()

    async with get_db_session() as db:
        proposal = await _get_proposal_or_400(body.proposal_id, db)

        if proposal.portal_link_expires and proposal.portal_link_expires < now:
            raise HTTPException(
                status_code=400,
                detail={"error": "token_expired", "message": "Link portale scaduto", "detail": {}},
            )

        deal = await _get_deal_or_400(proposal.deal_id, db)

        # Idempotenza
        if deal.service_type == "consulting" and deal.consulting_approved:
            raise HTTPException(
                status_code=409,
                detail={"error": "already_confirmed", "message": "Consegna già confermata", "detail": {}},
            )
        if deal.service_type != "consulting" and deal.delivery_approved:
            raise HTTPException(
                status_code=409,
                detail={"error": "already_confirmed", "message": "Consegna già confermata", "detail": {}},
            )

        if deal.service_type == "consulting":
            update_data = {"consulting_approved": True, "consulting_approved_at": now, "updated_at": now}
        else:
            update_data = {"delivery_approved": True, "delivery_approved_at": now, "updated_at": now}

        await db.execute(update(Deal).where(Deal.id == deal.id).values(**update_data))

    log.info("webhook.portal.delivery_confirmed", proposal_id=body.proposal_id)
    return {"message": "Consegna confermata. Grazie per aver scelto i nostri servizi."}
