from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )

    invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    milestone: Mapped[str] = mapped_column(Text, nullable=False)

    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_rate_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), nullable=True, server_default=text("22.00")
    )
    tax_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'")
    )
    due_date: Mapped[date] = mapped_column(nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(nullable=True)
    payment_method: Mapped[str | None] = mapped_column(Text, nullable=True)

    billing_dispute: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    billing_dispute_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    reminder_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_reminder_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_invoices_deal_id", "deal_id"),
        Index("idx_invoices_client_id", "client_id"),
        Index("idx_invoices_status", "status"),
        Index("idx_invoices_due_date", "due_date"),
    )
