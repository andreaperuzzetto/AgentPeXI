from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ClientSummary(BaseModel):
    id: str
    business_name: str
    city: str | None
    country: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClientDetail(BaseModel):
    id: str
    lead_id: str | None
    business_name: str
    address: str | None
    city: str | None
    region: str | None
    country: str | None
    # contact_name, contact_email, contact_phone esclusi: PII cifrati
    sla_response_hours: int | None
    preferred_language: str | None
    timezone: str | None
    db_schema_name: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientListResponse(BaseModel):
    items: list[ClientSummary]
    total: int
    page: int
    per_page: int
