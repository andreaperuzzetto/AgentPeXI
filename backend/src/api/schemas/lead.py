from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class LeadSummary(BaseModel):
    id: str
    business_name: str
    city: str | None
    sector: str
    suggested_service_type: str | None
    lead_score: int | None
    qualified: bool | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadDetail(BaseModel):
    id: str
    google_place_id: str
    business_name: str
    address: str | None
    city: str | None
    region: str | None
    country: str | None
    google_rating: float | None
    google_review_count: int | None
    google_category: str | None
    website_url: str | None
    # phone escluso: campo PII cifrato
    sector: str
    suggested_service_type: str | None
    gap_signals: dict[str, Any] | None
    lead_score: int | None
    qualified: bool | None
    disqualify_reason: str | None
    gap_summary: str | None
    estimated_value_eur: int | None
    ateco_code: str | None
    company_size: str | None
    social_facebook_url: str | None
    social_instagram_url: str | None
    enrichment_confidence: float | None
    enrichment_level: str | None
    # vat_number escluso: campo PII cifrato
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    items: list[LeadSummary]
    total: int
    page: int
    per_page: int
