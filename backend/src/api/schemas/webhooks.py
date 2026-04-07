from __future__ import annotations

from pydantic import BaseModel


class PortalClientApproveRequest(BaseModel):
    proposal_id: str
    token: str


class PortalClientRejectRequest(BaseModel):
    proposal_id: str
    token: str
    notes: str | None = None


class PortalClientDeliveryConfirmRequest(BaseModel):
    proposal_id: str
    token: str
