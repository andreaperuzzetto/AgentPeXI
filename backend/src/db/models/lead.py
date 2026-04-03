from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    google_place_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    business_name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True, server_default=text("'IT'"))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    google_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    google_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)

    sector: Mapped[str] = mapped_column(Text, nullable=False)
    service_gap_detected: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("FALSE")
    )

    suggested_service_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    gap_signals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    lead_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    disqualify_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    gap_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_value_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)

    vat_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    ateco_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_facebook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_instagram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enrichment_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    enrichment_level: Mapped[str | None] = mapped_column(Text, nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'discovered'")
    )

    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_leads_sector", "sector"),
        Index("idx_leads_status", "status"),
        Index("idx_leads_qualified", "qualified"),
        Index("idx_leads_lead_score", "lead_score"),
        Index("idx_leads_service_type", "suggested_service_type"),
    )
