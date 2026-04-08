from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from tools.db_tools import (
    LeadAlreadyExistsError,
    create_lead,
    get_lead_by_place_id,
)
from tools.google_maps import search_businesses

# ── Sectors config ────────────────────────────────────────────────────────────
# Loaded once at import — config/sectors.yaml is at project root (5 levels up).
_SECTORS_PATH = Path(__file__).parents[4] / "config" / "sectors.yaml"
_SECTORS: dict = yaml.safe_load(_SECTORS_PATH.read_text())["sectors"]
_VALID_SECTORS: frozenset[str] = frozenset(_SECTORS.keys())

# Expansion fallback settings
_MIN_LEADS_THRESHOLD = 3
_MAX_EXPANSION_ATTEMPTS = 3
_RADIUS_EXPANSION_STEP_KM = 5


class ScoutAgent(BaseAgent):
    """Discovers business leads via Google Maps Places for a given zone + sector."""

    agent_name = "scout"

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        zone: str | None = payload.get("zone")
        sector: str | None = payload.get("sector")
        if not zone or not sector:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required payload fields: zone, sector",
            )

        if sector not in _VALID_SECTORS:
            raise AgentToolError(
                code="validation_invalid_sector",
                message=f"Sector '{sector}' not in config/sectors.yaml",
            )

        radius_km: int = int(payload.get("radius_km", 10))
        max_results: int = int(payload.get("max_results", 20))
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Build search query from sector keywords ───────────────────────────
        keywords: list[str] = _SECTORS[sector].get("keywords", [])
        search_query = f"{keywords[0]} {zone}" if keywords else f"{sector} {zone}"

        # ── Search with radius expansion fallback ─────────────────────────────
        # Accumulate across attempts; dedup by google_place_id.
        all_places: dict[str, dict] = {}
        current_radius = radius_km

        for attempt in range(_MAX_EXPANSION_ATTEMPTS):
            self.log.info(
                "scout.search",
                task_id=str(task.id),
                attempt=attempt + 1,
                radius_km=current_radius,
                zone=zone,
                sector=sector,
            )
            results = await search_businesses(
                query=search_query,
                location=zone,
                radius_km=current_radius,
                max_results=max_results,
            )
            for place in results:
                all_places[place["google_place_id"]] = place

            if len(all_places) >= _MIN_LEADS_THRESHOLD:
                break

            if attempt < _MAX_EXPANSION_ATTEMPTS - 1:
                current_radius += _RADIUS_EXPANSION_STEP_KM

        places = list(all_places.values())

        if not places:
            self.log.warning(
                "scout.no_results",
                task_id=str(task.id),
                zone=zone,
                sector=sector,
                final_radius_km=current_radius,
            )
            raise GateNotApprovedError("agent_scout_no_results")

        # ── Write leads to DB (skip if dry_run) ───────────────────────────────
        leads_written = 0
        skipped_duplicates = 0
        written_lead_ids: list[str] = []

        for place in places:
            # Deduplication: check existing lead by google_place_id before writing.
            existing = await get_lead_by_place_id(place["google_place_id"], db)
            if existing is not None:
                skipped_duplicates += 1
                continue

            if dry_run:
                leads_written += 1
                continue

            lead_data = {
                "google_place_id": place["google_place_id"],
                "business_name": place.get("business_name"),
                "address": place.get("address"),
                "city": place.get("city"),
                "region": place.get("region"),
                "country": place.get("country", "IT"),
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
                "google_rating": place.get("google_rating"),
                "google_review_count": place.get("google_review_count"),
                "google_category": place.get("google_category"),
                "website_url": place.get("website_url"),
                "phone": place.get("phone"),
                "sector": sector,
            }

            try:
                lead = await create_lead(lead_data, db)
                leads_written += 1
                written_lead_ids.append(str(lead.id))
            except LeadAlreadyExistsError:
                # Race condition between check and insert — treat as duplicate.
                skipped_duplicates += 1

        self.log.info(
            "scout.done",
            task_id=str(task.id),
            leads_found=len(places),
            leads_written=leads_written,
            skipped_duplicates=skipped_duplicates,
            radius_used_km=current_radius,
            dry_run=dry_run,
        )

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "leads_found": len(places),
                "leads_written": leads_written,
                "lead_ids": written_lead_ids,
                "skipped_duplicates": skipped_duplicates,
                "zone_searched": zone,
                "radius_used_km": current_radius,
            },
            next_tasks=["analyst.score_lead"],
        )
