from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'lead_identified'")
    )
    service_type: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_value_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)

    proposal_human_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    proposal_approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    proposal_approved_by: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=text("'operator'")
    )
    proposal_rejection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    proposal_rejection_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    kickoff_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    kickoff_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    delivery_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    delivery_approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivery_rejection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    delivery_rejection_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    consulting_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    consulting_approved_at: Mapped[datetime | None] = mapped_column(nullable=True)

    total_price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_pct: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("30")
    )
    payment_terms_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("30")
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_deals_lead_id", "lead_id"),
        Index("idx_deals_client_id", "client_id"),
        Index("idx_deals_status", "status"),
        Index("idx_deals_service_type", "service_type"),
    )
