from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.deal import (
    DealDetail,
    DealGateResponse,
    DealListResponse,
    DealStatusPatch,
    DealSummary,
    GateDeliveryRejectRequest,
    GateProposalRejectRequest,
)
from db.models.deal import Deal
from tools.db_tools import get_deal, update_deal

log = structlog.get_logger()

router = APIRouter(prefix="/deals", tags=["deals"])


def _deal_to_detail(deal: Deal) -> DealDetail:
    return DealDetail(
        id=str(deal.id),
        lead_id=str(deal.lead_id),
        client_id=str(deal.client_id) if deal.client_id else None,
        status=deal.status,
        service_type=deal.service_type,
        sector=deal.sector,
        estimated_value_eur=deal.estimated_value_eur,
        total_price_eur=deal.total_price_eur,
        deposit_pct=deal.deposit_pct,
        payment_terms_days=deal.payment_terms_days,
        proposal_human_approved=deal.proposal_human_approved,
        proposal_approved_at=deal.proposal_approved_at,
        proposal_rejection_count=deal.proposal_rejection_count,
        proposal_rejection_notes=deal.proposal_rejection_notes,
        kickoff_confirmed=deal.kickoff_confirmed,
        kickoff_confirmed_at=deal.kickoff_confirmed_at,
        delivery_approved=deal.delivery_approved,
        delivery_approved_at=deal.delivery_approved_at,
        delivery_rejection_count=deal.delivery_rejection_count,
        delivery_rejection_notes=deal.delivery_rejection_notes,
        consulting_approved=deal.consulting_approved,
        consulting_approved_at=deal.consulting_approved_at,
        notes=deal.notes,
        lost_reason=deal.lost_reason,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
    )


@router.get("", response_model=DealListResponse)
async def list_deals(
    deal_status: str | None = Query(default=None, alias="status"),
    service_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealListResponse:
    q = select(Deal).where(Deal.deleted_at.is_(None))
    if deal_status:
        q = q.where(Deal.status == deal_status)
    if service_type:
        q = q.where(Deal.service_type == service_type)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total: int = total_result.scalar_one()

    q = q.order_by(Deal.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    deals = result.scalars().all()

    items = [
        DealSummary(
            id=str(d.id),
            lead_id=str(d.lead_id),
            client_id=str(d.client_id) if d.client_id else None,
            status=d.status,
            service_type=d.service_type,
            sector=d.sector,
            estimated_value_eur=d.estimated_value_eur,
            proposal_human_approved=d.proposal_human_approved,
            kickoff_confirmed=d.kickoff_confirmed,
            delivery_approved=d.delivery_approved,
            consulting_approved=d.consulting_approved,
            created_at=d.created_at,
        )
        for d in deals
    ]
    return DealListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/{deal_id}", response_model=DealDetail)
async def get_deal_endpoint(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealDetail:
    deal = await _get_or_404(deal_id, db)
    return _deal_to_detail(deal)


@router.patch("/{deal_id}/status", response_model=DealDetail)
async def patch_deal_status(
    deal_id: str,
    body: DealStatusPatch,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealDetail:
    await _get_or_404(deal_id, db)
    data: dict = {"status": body.status}
    if body.notes is not None:
        data["notes"] = body.notes
    if body.status == "lost" and body.notes:
        data["lost_reason"] = body.notes
    deal = await update_deal(uuid.UUID(deal_id), data, db)
    log.info("api.deals.status_patched", deal_id=deal_id, new_status=body.status)
    return _deal_to_detail(deal)


# ── GATE 1 — Approva proposta ────────────────────────────────────────────────

@router.post("/{deal_id}/gates/proposal-approve", response_model=DealGateResponse)
async def gate_proposal_approve(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealGateResponse:
    await _get_or_404(deal_id, db)
    now = datetime.utcnow()
    await update_deal(
        uuid.UUID(deal_id),
        {"proposal_human_approved": True, "proposal_approved_at": now},
        db,
    )
    log.info("api.deals.gate.proposal_approved", deal_id=deal_id)
    return DealGateResponse(
        deal_id=deal_id,
        gate="proposal_review",
        approved=True,
        approved_at=now,
    )


# ── GATE 1 — Rifiuta proposta ────────────────────────────────────────────────

@router.post("/{deal_id}/gates/proposal-reject", response_model=DealGateResponse)
async def gate_proposal_reject(
    deal_id: str,
    body: GateProposalRejectRequest,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealGateResponse:
    deal = await _get_or_404(deal_id, db)
    new_count = (deal.proposal_rejection_count or 0) + 1
    await update_deal(
        uuid.UUID(deal_id),
        {
            "proposal_rejection_count": new_count,
            "proposal_rejection_notes": body.notes,
            "proposal_human_approved": False,
        },
        db,
    )
    log.info("api.deals.gate.proposal_rejected", deal_id=deal_id, count=new_count)
    return DealGateResponse(
        deal_id=deal_id,
        gate="proposal_review",
        approved=False,
        rejection_count=new_count,
        notes=body.notes,
    )


# ── GATE 2 — Conferma kickoff ────────────────────────────────────────────────

@router.post("/{deal_id}/gates/kickoff-confirm", response_model=DealGateResponse)
async def gate_kickoff_confirm(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealGateResponse:
    await _get_or_404(deal_id, db)
    now = datetime.utcnow()
    await update_deal(
        uuid.UUID(deal_id),
        {"kickoff_confirmed": True, "kickoff_confirmed_at": now},
        db,
    )
    log.info("api.deals.gate.kickoff_confirmed", deal_id=deal_id)
    return DealGateResponse(
        deal_id=deal_id,
        gate="kickoff",
        approved=True,
        confirmed=True,
        confirmed_at=now,
    )


# ── GATE 3 — Approva consegna ────────────────────────────────────────────────

@router.post("/{deal_id}/gates/delivery-approve", response_model=DealGateResponse)
async def gate_delivery_approve(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealGateResponse:
    deal = await _get_or_404(deal_id, db)
    now = datetime.utcnow()

    if deal.service_type == "consulting":
        update_data = {"consulting_approved": True, "consulting_approved_at": now}
    else:
        update_data = {"delivery_approved": True, "delivery_approved_at": now}

    await update_deal(uuid.UUID(deal_id), update_data, db)
    log.info("api.deals.gate.delivery_approved", deal_id=deal_id, service_type=deal.service_type)
    return DealGateResponse(
        deal_id=deal_id,
        gate="delivery",
        approved=True,
        approved_at=now,
    )


# ── GATE 3 — Rifiuta consegna ────────────────────────────────────────────────

@router.post("/{deal_id}/gates/delivery-reject", response_model=DealGateResponse)
async def gate_delivery_reject(
    deal_id: str,
    body: GateDeliveryRejectRequest,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> DealGateResponse:
    deal = await _get_or_404(deal_id, db)
    new_count = (deal.delivery_rejection_count or 0) + 1
    await update_deal(
        uuid.UUID(deal_id),
        {
            "delivery_rejection_count": new_count,
            "delivery_rejection_notes": body.notes,
        },
        db,
    )
    log.info("api.deals.gate.delivery_rejected", deal_id=deal_id, count=new_count)
    return DealGateResponse(
        deal_id=deal_id,
        gate="delivery",
        approved=False,
        rejection_count=new_count,
        notes=body.notes,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(deal_id: str, db: AsyncSession) -> Deal:
    try:
        uid = uuid.UUID(deal_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_uuid", "message": "deal_id non valido", "detail": {}},
        )
    deal = await get_deal(uid, db)
    if deal is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal {deal_id} non trovato", "detail": {}},
        )
    return deal
