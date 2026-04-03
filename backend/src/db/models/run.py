from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deals.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'running'")
    )

    current_phase: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    gate_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    awaiting_gate_since: Mapped[datetime | None] = mapped_column(nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_runs_deal_id", "deal_id"),
        Index("idx_runs_status", "status"),
    )
