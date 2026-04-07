from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.stats import PipelineStats
from db.models.deal import Deal
from db.models.lead import Lead
from db.models.run import Run

log = structlog.get_logger()

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/pipeline", response_model=PipelineStats)
async def pipeline_stats(
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> PipelineStats:
    # Leads
    leads_result = await db.execute(
        select(func.count(Lead.id)).where(Lead.deleted_at.is_(None))
    )
    leads_total: int = leads_result.scalar_one() or 0

    leads_qualified_result = await db.execute(
        select(func.count(Lead.id)).where(Lead.deleted_at.is_(None), Lead.qualified.is_(True))
    )
    leads_qualified: int = leads_qualified_result.scalar_one() or 0

    # Deals attivi (non cancelled/lost)
    deals_active_result = await db.execute(
        select(func.count(Deal.id)).where(
            Deal.deleted_at.is_(None),
            Deal.status.not_in(["lost", "cancelled"]),
        )
    )
    deals_active: int = deals_active_result.scalar_one() or 0

    # Deals per service_type
    deals_by_service_result = await db.execute(
        select(Deal.service_type, func.count(Deal.id))
        .where(Deal.deleted_at.is_(None), Deal.status.not_in(["lost", "cancelled"]))
        .group_by(Deal.service_type)
    )
    deals_by_service: dict[str, int] = {
        row[0]: row[1] for row in deals_by_service_result.all()
    }

    # Deals awaiting gate
    deals_awaiting_gate_result = await db.execute(
        select(func.count(Run.run_id)).where(
            Run.deleted_at.is_(None), Run.status == "awaiting_gate"
        )
    )
    deals_awaiting_gate: int = deals_awaiting_gate_result.scalar_one() or 0

    # Deals in delivery
    deals_in_delivery_result = await db.execute(
        select(func.count(Deal.id)).where(
            Deal.deleted_at.is_(None), Deal.status == "in_delivery"
        )
    )
    deals_in_delivery: int = deals_in_delivery_result.scalar_one() or 0

    # Deals delivered
    deals_delivered_result = await db.execute(
        select(func.count(Deal.id)).where(
            Deal.deleted_at.is_(None), Deal.status == "delivered"
        )
    )
    deals_delivered: int = deals_delivered_result.scalar_one() or 0

    # Revenue consegnato (deals in stato delivered)
    revenue_delivered_result = await db.execute(
        select(func.sum(Deal.total_price_eur)).where(
            Deal.deleted_at.is_(None),
            Deal.status.in_(["delivered", "active"]),
        )
    )
    revenue_delivered_eur: int = revenue_delivered_result.scalar_one() or 0

    # Revenue in pipeline (deals attivi con valore stimato)
    revenue_pipeline_result = await db.execute(
        select(func.sum(Deal.estimated_value_eur)).where(
            Deal.deleted_at.is_(None),
            Deal.status.not_in(["lost", "cancelled", "delivered", "active"]),
        )
    )
    revenue_pipeline_eur: int = revenue_pipeline_result.scalar_one() or 0

    return PipelineStats(
        leads_total=leads_total,
        leads_qualified=leads_qualified,
        deals_active=deals_active,
        deals_by_service=deals_by_service,
        deals_awaiting_gate=deals_awaiting_gate,
        deals_in_delivery=deals_in_delivery,
        deals_delivered=deals_delivered,
        revenue_delivered_eur=revenue_delivered_eur,
        revenue_pipeline_eur=revenue_pipeline_eur,
    )
