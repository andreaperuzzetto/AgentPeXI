from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    pdf_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    gap_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_type: Mapped[str] = mapped_column(Text, nullable=False)
    deliverables_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pricing_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timeline_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    roi_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifact_paths: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    portal_link_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    portal_link_expires: Mapped[datetime | None] = mapped_column(nullable=True)
    client_viewed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    client_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_response_at: Mapped[datetime | None] = mapped_column(nullable=True)
    client_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_proposals_deal_id", "deal_id"),
        UniqueConstraint("deal_id", "version", name="uq_proposals_deal_version"),
    )
