from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DealSummary(BaseModel):
    id: str
    lead_id: str
    client_id: str | None
    status: str
    service_type: str
    sector: str
    estimated_value_eur: int | None
    proposal_human_approved: bool
    kickoff_confirmed: bool
    delivery_approved: bool
    consulting_approved: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DealDetail(BaseModel):
    id: str
    lead_id: str
    client_id: str | None
    status: str
    service_type: str
    sector: str
    estimated_value_eur: int | None
    total_price_eur: int | None
    deposit_pct: int | None
    payment_terms_days: int | None

    # Gate flags
    proposal_human_approved: bool
    proposal_approved_at: datetime | None
    proposal_rejection_count: int
    proposal_rejection_notes: str | None
    kickoff_confirmed: bool
    kickoff_confirmed_at: datetime | None
    delivery_approved: bool
    delivery_approved_at: datetime | None
    delivery_rejection_count: int
    delivery_rejection_notes: str | None
    consulting_approved: bool
    consulting_approved_at: datetime | None

    notes: str | None
    lost_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DealListResponse(BaseModel):
    items: list[DealSummary]
    total: int
    page: int
    per_page: int


class DealStatusPatch(BaseModel):
    status: str
    notes: str | None = None


class DealGateResponse(BaseModel):
    deal_id: str
    gate: str
    approved: bool
    approved_at: datetime | None = None
    confirmed: bool | None = None
    confirmed_at: datetime | None = None
    rejection_count: int | None = None
    notes: str | None = None


class GateProposalRejectRequest(BaseModel):
    notes: str


class GateDeliveryRejectRequest(BaseModel):
    notes: str
