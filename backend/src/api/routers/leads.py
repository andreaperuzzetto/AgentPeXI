from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.lead import LeadDetail, LeadListResponse, LeadSummary
from db.models.lead import Lead

log = structlog.get_logger()

router = APIRouter(prefix="/leads", tags=["leads"])


def _lead_to_schema(lead: Lead) -> LeadDetail:
    return LeadDetail(
        id=str(lead.id),
        google_place_id=lead.google_place_id,
        business_name=lead.business_name,
        address=lead.address,
        city=lead.city,
        region=lead.region,
        country=lead.country,
        google_rating=float(lead.google_rating) if lead.google_rating is not None else None,
        google_review_count=lead.google_review_count,
        google_category=lead.google_category,
        website_url=lead.website_url,
        sector=lead.sector,
        suggested_service_type=lead.suggested_service_type,
        gap_signals=lead.gap_signals,
        lead_score=lead.lead_score,
        qualified=lead.qualified,
        disqualify_reason=lead.disqualify_reason,
        gap_summary=lead.gap_summary,
        estimated_value_eur=lead.estimated_value_eur,
        ateco_code=lead.ateco_code,
        company_size=lead.company_size,
        social_facebook_url=lead.social_facebook_url,
        social_instagram_url=lead.social_instagram_url,
        enrichment_confidence=float(lead.enrichment_confidence) if lead.enrichment_confidence is not None else None,
        enrichment_level=lead.enrichment_level,
        status=lead.status,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


@router.get("", response_model=LeadListResponse)
async def list_leads(
    sector: str | None = Query(default=None),
    qualified: bool | None = Query(default=None),
    lead_status: str | None = Query(default=None, alias="status"),
    city: str | None = Query(default=None),
    suggested_service_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> LeadListResponse:
    q = select(Lead).where(Lead.deleted_at.is_(None))
    if sector:
        q = q.where(Lead.sector == sector)
    if qualified is not None:
        q = q.where(Lead.qualified == qualified)
    if lead_status:
        q = q.where(Lead.status == lead_status)
    if city:
        q = q.where(Lead.city.ilike(f"%{city}%"))
    if suggested_service_type:
        q = q.where(Lead.suggested_service_type == suggested_service_type)

    total_result = await db.execute(
        select(func.count()).select_from(q.subquery())
    )
    total: int = total_result.scalar_one()

    q = q.order_by(Lead.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    leads = result.scalars().all()

    items = [
        LeadSummary(
            id=str(lead.id),
            business_name=lead.business_name,
            city=lead.city,
            sector=lead.sector,
            suggested_service_type=lead.suggested_service_type,
            lead_score=lead.lead_score,
            qualified=lead.qualified,
            status=lead.status,
            created_at=lead.created_at,
        )
        for lead in leads
    ]

    return LeadListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/{lead_id}", response_model=LeadDetail)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> LeadDetail:
    try:
        uid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_uuid", "message": "lead_id non valido", "detail": {}},
        )

    result = await db.execute(
        select(Lead).where(Lead.id == uid, Lead.deleted_at.is_(None))
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "lead_not_found", "message": f"Lead {lead_id} non trovato", "detail": {}},
        )

    return _lead_to_schema(lead)
