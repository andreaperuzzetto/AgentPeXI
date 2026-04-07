from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.proposal import ProposalDetail, ProposalListResponse
from db.models.proposal import Proposal
from tools.file_store import get_presigned_url

log = structlog.get_logger()

router = APIRouter(prefix="/proposals", tags=["proposals"])


def _proposal_to_schema(p: Proposal, pdf_url: str | None = None) -> ProposalDetail:
    return ProposalDetail(
        id=str(p.id),
        deal_id=str(p.deal_id),
        version=p.version,
        pdf_path=p.pdf_path,
        pdf_download_url=pdf_url,
        page_count=p.page_count,
        service_type=p.service_type,
        gap_summary=p.gap_summary,
        solution_summary=p.solution_summary,
        timeline_weeks=p.timeline_weeks,
        roi_summary=p.roi_summary,
        artifact_paths=p.artifact_paths or [],
        sent_at=p.sent_at,
        client_response=p.client_response,
        client_response_at=p.client_response_at,
        client_notes=p.client_notes,
        created_at=p.created_at,
    )


@router.get("", response_model=ProposalListResponse)
async def list_proposals(
    deal_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> ProposalListResponse:
    q = select(Proposal).where(Proposal.deleted_at.is_(None))
    if deal_id:
        try:
            q = q.where(Proposal.deal_id == uuid.UUID(deal_id))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_uuid", "message": "deal_id non valido", "detail": {}},
            )

    q = q.order_by(Proposal.version.asc())
    result = await db.execute(q)
    proposals = result.scalars().all()

    items = []
    for p in proposals:
        try:
            pdf_url = await get_presigned_url(p.pdf_path, expires_in_seconds=3600)
        except Exception:
            pdf_url = None
        items.append(_proposal_to_schema(p, pdf_url))

    return ProposalListResponse(items=items)


@router.get("/{proposal_id}/download")
async def download_proposal(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> RedirectResponse:
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
            detail={"error": "proposal_not_found", "message": f"Proposta {proposal_id} non trovata", "detail": {}},
        )

    presigned_url = await get_presigned_url(proposal.pdf_path, expires_in_seconds=3600)
    return RedirectResponse(url=presigned_url, status_code=302)
