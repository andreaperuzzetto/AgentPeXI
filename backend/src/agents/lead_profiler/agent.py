from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import anthropic
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from tools.db_tools import (
    create_task,
    get_lead,
    get_task_by_idempotency_key,
    update_lead,
)
from tools.google_maps import get_place_details

# ── Config ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]

_SECTORS: dict = yaml.safe_load((_ROOT / "config" / "sectors.yaml").read_text())["sectors"]

# Full ATECO catalogue: {code: description}
_ATECO: dict[str, str] = json.loads((_ROOT / "config" / "data" / "ateco_codes.json").read_text())

# System prompt loaded once at module level
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

# Enrichment level decision: "full" requires both ateco_code and company_size
_FULL_ENRICHMENT_REQUIRED: frozenset[str] = frozenset({"ateco_code", "company_size"})

# Confidence bonus/penalty increments
_CONF_BASE = 0.55
_CONF_BONUS_PLACE_DETAILS = 0.10
_CONF_BONUS_SOCIAL = 0.08
_CONF_PENALTY_NO_REVIEWS = 0.10


class LeadProfilerAgent(BaseAgent):
    """
    Enriches qualified leads with: ATECO code, company_size, social handles.
    Reads: leads. Writes: leads, tasks.
    Does NOT touch: vat_number (no company registry API available).
    """

    agent_name = "lead_profiler"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        raw_ids: list | None = payload.get("lead_ids")
        if not raw_ids and "lead_id" in payload:
            raw_ids = [payload["lead_id"]]
        if not raw_ids:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: lead_ids (list) or lead_id (single UUID)",
            )

        dry_run: bool = bool(payload.get("dry_run", False))

        enriched = 0
        skipped = 0
        failed = 0
        level_counts: dict[str, int] = {"basic": 0, "full": 0}

        for raw_id in raw_ids:
            lead_id = UUID(str(raw_id))

            # ── Load lead ─────────────────────────────────────────────────────
            lead = await get_lead(lead_id, db)
            if lead is None:
                raise AgentToolError(
                    code="tool_db_lead_not_found",
                    message=f"Lead {lead_id} not found",
                )

            # ── Skip already-enriched leads ───────────────────────────────────
            if lead.enrichment_level is not None:
                skipped += 1
                self.log.info(
                    "lead_profiler.lead_skipped",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                    enrichment_level=lead.enrichment_level,
                )
                continue

            # ── Idempotency guard ─────────────────────────────────────────────
            idem_key = f"{task.id}:enrich:{lead_id}"
            if await get_task_by_idempotency_key(idem_key, db) is not None:
                skipped += 1
                self.log.info(
                    "lead_profiler.idempotent_skip",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                )
                continue

            # ── Refresh Maps data ─────────────────────────────────────────────
            place_details: dict | None = None
            try:
                place_details = await get_place_details(lead.google_place_id)
            except AgentToolError as e:
                # Non-critical: log and continue with existing lead data
                self.log.warning(
                    "lead_profiler.place_details_failed",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                    error_code=e.code,
                )

            # Merge refreshed data if available (Maps may have updated rating etc.)
            effective_rating = (
                place_details.get("google_rating") or lead.google_rating
                if place_details else lead.google_rating
            )
            effective_reviews = (
                place_details.get("google_review_count") or lead.google_review_count
                if place_details else lead.google_review_count
            )
            effective_category = (
                place_details.get("google_category") or lead.google_category
                if place_details else lead.google_category
            )
            website_url = (
                place_details.get("website_url") or lead.website_url
                if place_details else lead.website_url
            )

            # ── Build ATECO candidate list ────────────────────────────────────
            sector_data = _SECTORS.get(lead.sector, {})
            candidate_codes: list[str] = sector_data.get("ateco_codes", [])
            # Build descriptions dict limited to candidates (no full catalogue to LLM)
            ateco_descriptions = {
                code: _ATECO.get(code, code) for code in candidate_codes
            }

            # ── LLM enrichment ────────────────────────────────────────────────
            try:
                llm_result = await self._call_llm(
                    lead_id=lead_id,
                    sector=lead.sector,
                    google_category=effective_category,
                    city=lead.city,
                    google_rating=effective_rating,
                    google_review_count=effective_reviews,
                    website_url_present=website_url is not None,
                    candidate_ateco_codes=candidate_codes,
                    ateco_descriptions=ateco_descriptions,
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                self.log.warning(
                    "lead_profiler.llm_parse_error",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                )
                llm_result = self._fallback_result(candidate_codes)

            # ── Validate ATECO code is from the allowed list ───────────────────
            ateco_code: str | None = llm_result.get("ateco_code")
            if ateco_code and candidate_codes and ateco_code not in candidate_codes:
                # LLM hallucinated a code outside the candidates — use first candidate
                ateco_code = candidate_codes[0] if candidate_codes else None

            company_size: str | None = llm_result.get("company_size")
            if company_size not in {"solo", "micro", "small", "medium", None}:
                company_size = None

            # ── Social URLs ───────────────────────────────────────────────────
            # Build full URLs from handles inferred by LLM; null if handle is null.
            fb_handle: str | None = llm_result.get("social_facebook_handle")
            ig_handle: str | None = llm_result.get("social_instagram_handle")
            social_facebook_url = (
                f"https://www.facebook.com/{fb_handle}" if fb_handle else None
            )
            social_instagram_url = (
                f"https://www.instagram.com/{ig_handle}" if ig_handle else None
            )

            # ── Enrichment level + confidence ─────────────────────────────────
            enrichment_fields_present = {
                f for f in _FULL_ENRICHMENT_REQUIRED
                if locals().get(f) is not None
            }
            enrichment_level = (
                "full"
                if enrichment_fields_present == _FULL_ENRICHMENT_REQUIRED
                else "basic"
            )

            # Confidence: LLM provides a base, we adjust for known quality signals
            raw_confidence: float = float(llm_result.get("enrichment_confidence", _CONF_BASE))
            if place_details is not None:
                raw_confidence = min(1.0, raw_confidence + _CONF_BONUS_PLACE_DETAILS)
            if social_facebook_url or social_instagram_url:
                raw_confidence = min(1.0, raw_confidence + _CONF_BONUS_SOCIAL)
            if not effective_reviews or int(effective_reviews) < 5:
                raw_confidence = max(0.0, raw_confidence - _CONF_PENALTY_NO_REVIEWS)
            enrichment_confidence = round(min(0.95, raw_confidence), 2)

            self.log.info(
                "lead_profiler.enriched",
                task_id=str(task.id),
                lead_id=str(lead_id),
                enrichment_level=enrichment_level,
                enrichment_confidence=enrichment_confidence,
            )

            # ── Persist ───────────────────────────────────────────────────────
            if not dry_run:
                await update_lead(
                    lead_id,
                    {
                        "ateco_code": ateco_code,
                        "company_size": company_size,
                        "social_facebook_url": social_facebook_url,
                        "social_instagram_url": social_instagram_url,
                        "enrichment_level": enrichment_level,
                        "enrichment_confidence": enrichment_confidence,
                        # Refresh Maps data if place_details returned updated values
                        **(
                            {
                                "google_rating": effective_rating,
                                "google_review_count": effective_reviews,
                                "google_category": effective_category,
                                "website_url": website_url,
                            }
                            if place_details is not None
                            else {}
                        ),
                    },
                    db,
                )
                # Register idempotency key
                await create_task(
                    type="lead_profiler.enrich",
                    agent="lead_profiler",
                    payload={
                        "lead_id": str(lead_id),
                        "enrichment_level": enrichment_level,
                    },
                    db=db,
                    idempotency_key=idem_key,
                )

            enriched += 1
            level_counts[enrichment_level] = level_counts.get(enrichment_level, 0) + 1

        # No leads were processed at all
        if enriched == 0 and skipped == 0:
            raise GateNotApprovedError("agent_lead_profiler_no_leads_processed")

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "leads_enriched": enriched,
                "leads_skipped": skipped,
                "leads_failed_enrichment": failed,
                "enrichment_levels": level_counts,
            },
            next_tasks=["design.create_artifacts"] if enriched > 0 else [],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _call_llm(
        self,
        *,
        lead_id: UUID,
        sector: str,
        google_category: str | None,
        city: str | None,
        google_rating: float | None,
        google_review_count: int | None,
        website_url_present: bool,
        candidate_ateco_codes: list[str],
        ateco_descriptions: dict[str, str],
    ) -> dict:
        """
        Call Claude to classify ATECO code, estimate company_size, infer social handles.
        Input: no PII — only public classification data.
        """
        user_input = {
            "lead_id": str(lead_id),
            "sector": sector,
            "google_category": google_category,
            "city": city,
            "google_rating": float(google_rating) if google_rating else None,
            "google_review_count": google_review_count,
            "website_url_present": website_url_present,
            "candidate_ateco_codes": candidate_ateco_codes,
            "ateco_descriptions": ateco_descriptions,
        }

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
        )

        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        return json.loads(text)

    def _fallback_result(self, candidate_codes: list[str]) -> dict:
        """Conservative fallback when LLM call fails or returns invalid JSON."""
        return {
            "ateco_code": candidate_codes[0] if candidate_codes else None,
            "company_size": "micro",
            "social_facebook_handle": None,
            "social_instagram_handle": None,
            "enrichment_confidence": _CONF_BASE,
        }
