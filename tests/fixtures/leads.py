"""
Factory per Lead, Deal, Client — oggetti semplici (SimpleNamespace) per i test.

Non usano ORM __new__ (incompatibile con SQLAlchemy mapped attributes senza InstanceState)
— usano types.SimpleNamespace che permette l'impostazione libera degli attributi.
I test mockano le funzioni db_tools che li restituiscono, quindi non serve
l'integrazione ORM reale.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace


def make_lead(
    *,
    google_place_id: str | None = None,
    place_id: str | None = None,  # alias comodo
    business_name: str = "Bar Test SRL",
    sector: str = "horeca",
    city: str = "Roma",
    region: str = "Lazio",
    country: str = "IT",
    google_rating: float | None = 4.2,
    google_review_count: int | None = 87,
    website_url: str | None = None,
    phone: str | None = None,
    lead_score: int | None = None,
    qualified: bool | None = None,
    status: str = "discovered",
    suggested_service_type: str | None = "web_design",
    service_gap_detected: bool | None = None,
    gap_summary: str | None = None,
    estimated_value_eur: int | None = 3500,
    enrichment_level: str | None = None,
    ateco_code: str | None = None,
    company_size: str | None = None,
    address: str = "Via Roma 1",
) -> SimpleNamespace:
    """
    Crea un oggetto Lead-like (SimpleNamespace) per i test unit.
    Non usa __new__ di SQLAlchemy — non richiede DB né InstanceState.
    """
    gplace_id = place_id or google_place_id or f"ChIJtest_{uuid.uuid4().hex[:8]}"
    gap = service_gap_detected if service_gap_detected is not None else (website_url is None)
    return SimpleNamespace(
        id=uuid.uuid4(),
        google_place_id=gplace_id,
        place_id=gplace_id,  # alias
        business_name=business_name,
        address=address,
        city=city,
        region=region,
        country=country,
        latitude=Decimal("41.9028"),
        longitude=Decimal("12.4964"),
        google_rating=Decimal(str(google_rating)) if google_rating else None,
        google_review_count=google_review_count,
        google_category="Restaurant",
        website_url=website_url,
        phone=phone,  # PII — None by default
        sector=sector,
        service_gap_detected=gap,
        suggested_service_type=suggested_service_type,
        gap_signals={},
        lead_score=lead_score,
        qualified=qualified,
        disqualify_reason=None,
        gap_summary=gap_summary,
        estimated_value_eur=estimated_value_eur,
        vat_number=None,  # PII
        ateco_code=ateco_code,
        company_size=company_size,
        social_facebook_url=None,
        social_instagram_url=None,
        enrichment_confidence=None,
        enrichment_level=enrichment_level,
        embedding=None,
        status=status,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        deleted_at=None,
    )


def make_deal(
    *,
    lead_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    service_type: str = "web_design",
    sector: str = "horeca",
    status: str = "proposal_ready",
    proposal_human_approved: bool = False,
    kickoff_confirmed: bool = False,
    delivery_approved: bool = False,
    consulting_approved: bool = False,
    estimated_value_eur: int | None = 3500,
    total_price_eur: int | None = None,
    deposit_pct: int = 30,
    payment_terms_days: int = 30,
    proposal_rejection_count: int = 0,
    delivery_rejection_count: int = 0,
) -> SimpleNamespace:
    """Crea un oggetto Deal-like (SimpleNamespace) per i test unit."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id or uuid.uuid4(),
        client_id=client_id,
        status=status,
        service_type=service_type,
        sector=sector,
        estimated_value_eur=estimated_value_eur,
        proposal_human_approved=proposal_human_approved,
        proposal_approved_at=datetime.utcnow() if proposal_human_approved else None,
        proposal_approved_by="operator" if proposal_human_approved else None,
        proposal_rejection_count=proposal_rejection_count,
        proposal_rejection_notes=None,
        kickoff_confirmed=kickoff_confirmed,
        kickoff_confirmed_at=datetime.utcnow() if kickoff_confirmed else None,
        delivery_approved=delivery_approved,
        delivery_approved_at=datetime.utcnow() if delivery_approved else None,
        delivery_rejection_count=delivery_rejection_count,
        delivery_rejection_notes=None,
        consulting_approved=consulting_approved,
        consulting_approved_at=datetime.utcnow() if consulting_approved else None,
        total_price_eur=total_price_eur,
        deposit_pct=deposit_pct,
        payment_terms_days=payment_terms_days,
        notes=None,
        lost_reason=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        deleted_at=None,
    )


def make_client(
    *,
    lead_id: uuid.UUID | None = None,
    business_name: str = "Bar Test SRL",
    city: str = "Roma",
    region: str = "Lazio",
    db_schema_name: str | None = None,
    sla_response_hours: int = 4,
    contact_email: str | None = None,
    contact_name: str | None = None,
) -> SimpleNamespace:
    """Crea un oggetto Client-like (SimpleNamespace) per i test unit."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id or uuid.uuid4(),
        business_name=business_name,
        vat_number=None,  # PII
        address="Via Roma 1",
        city=city,
        region=region,
        country="IT",
        contact_name=contact_name,   # PII
        contact_email=contact_email,  # PII
        contact_phone=None,  # PII
        sla_response_hours=sla_response_hours,
        preferred_language="it",
        timezone="Europe/Rome",
        db_schema_name=db_schema_name or f"client_{uuid.uuid4().hex}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        deleted_at=None,
    )
