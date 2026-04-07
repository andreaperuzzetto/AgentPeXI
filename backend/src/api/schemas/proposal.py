from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ProposalDetail(BaseModel):
    id: str
    deal_id: str
    version: int
    pdf_path: str
    pdf_download_url: str | None  # presigned URL MinIO, 1h
    page_count: int | None
    service_type: str
    gap_summary: str | None
    solution_summary: str | None
    timeline_weeks: int | None
    roi_summary: str | None
    artifact_paths: list[str]
    sent_at: datetime | None
    client_response: str | None
    client_response_at: datetime | None
    client_notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProposalListResponse(BaseModel):
    items: list[ProposalDetail]
