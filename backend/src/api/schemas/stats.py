from __future__ import annotations

from pydantic import BaseModel


class PipelineStats(BaseModel):
    leads_total: int
    leads_qualified: int
    deals_active: int
    deals_by_service: dict[str, int]
    deals_awaiting_gate: int
    deals_in_delivery: int
    deals_delivered: int
    revenue_delivered_eur: int  # in centesimi
    revenue_pipeline_eur: int   # in centesimi
