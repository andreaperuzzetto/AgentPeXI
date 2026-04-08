"""Factory per Proposal — oggetti semplici (SimpleNamespace) per i test."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace


def make_proposal(
    *,
    deal_id: uuid.UUID | None = None,
    version: int = 1,
    service_type: str = "web_design",
    pdf_path: str | None = None,
    gap_summary: str = "Gap: nessun sito web.",
    solution_summary: str = "Realizziamo un sito professionale.",
    timeline_weeks: int = 4,
    pricing_json: dict | None = None,
    deliverables_json: dict | None = None,
    artifact_paths: list[str] | None = None,
) -> SimpleNamespace:
    """Crea un oggetto Proposal-like (SimpleNamespace) per i test unit."""
    deal_id = deal_id or uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        deal_id=deal_id,
        version=version,
        pdf_path=pdf_path or f"clients/{deal_id}/proposals/v{version}.pdf",
        page_count=4,
        gap_summary=gap_summary,
        solution_summary=solution_summary,
        service_type=service_type,
        deliverables_json=deliverables_json or {
            "deliverables": ["Homepage", "About", "Contatti"]
        },
        pricing_json=pricing_json or {
            "total": 350000,
            "deposit": 105000,
            "currency": "EUR",
        },
        timeline_weeks=timeline_weeks,
        roi_summary="ROI stimato: riduzione costi acquisizione del 20% in 6 mesi.",
        artifact_paths=artifact_paths or [],
        sent_at=None,
        portal_link_token=None,
        portal_link_expires=None,
        client_viewed_at=None,
        client_response=None,
        client_response_at=None,
        client_notes=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        deleted_at=None,
    )
