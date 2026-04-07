from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.client import ClientDetail, ClientListResponse, ClientSummary
from db.models.client import Client

log = structlog.get_logger()

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=ClientListResponse)
async def list_clients(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> ClientListResponse:
    q = select(Client).where(Client.deleted_at.is_(None))

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total: int = total_result.scalar_one()

    q = q.order_by(Client.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    clients = result.scalars().all()

    items = [
        ClientSummary(
            id=str(c.id),
            business_name=c.business_name,
            city=c.city,
            country=c.country,
            created_at=c.created_at,
        )
        for c in clients
    ]
    return ClientListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/{client_id}", response_model=ClientDetail)
async def get_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> ClientDetail:
    try:
        uid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_uuid", "message": "client_id non valido", "detail": {}},
        )

    result = await db.execute(
        select(Client).where(Client.id == uid, Client.deleted_at.is_(None))
    )
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "client_not_found", "message": f"Client {client_id} non trovato", "detail": {}},
        )

    return ClientDetail(
        id=str(client.id),
        lead_id=str(client.lead_id) if client.lead_id else None,
        business_name=client.business_name,
        address=client.address,
        city=client.city,
        region=client.region,
        country=client.country,
        sla_response_hours=client.sla_response_hours,
        preferred_language=client.preferred_language,
        timezone=client.timezone,
        db_schema_name=client.db_schema_name,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )
