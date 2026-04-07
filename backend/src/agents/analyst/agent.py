from __future__ import annotations

import json
from pathlib import Path
from typing import Any
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

# ── Config ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_SCORING: dict = yaml.safe_load((_ROOT / "config" / "scoring.yaml").read_text())
_QUALIFY_THRESHOLD: int = _SCORING["thresholds"]["qualify"]          # 65
_AUTO_DISCARD_THRESHOLD: int = _SCORING["thresholds"]["auto_discard"]  # 20

# ── System prompt (loaded once at module level) ───────────────────────────────
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

# ── Sectors whose update frequency is inherently high (digital_maintenance) ──
_HIGH_UPDATE_SECTORS: frozenset[str] = frozenset(
    {"healthcare", "education", "retail", "food_retail"}
)

# ── Signal key → service type reverse index ───────────────────────────────────
_SERVICE_TYPES = ("web_design", "consulting", "digital_maintenance")


class AnalystAgent(BaseAgent):
    """Scores leads with config/scoring.yaml and qualifies them (threshold ≥ 65)."""

    agent_name = "analyst"

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

        leads_analyzed = 0
        leads_qualified = 0
        leads_disqualified = 0
        leads_skipped = 0
        qualified_lead_ids: list[str] = []

        for raw_id in raw_ids:
            lead_id = UUID(str(raw_id))

            # ── Load lead ─────────────────────────────────────────────────────
            lead = await get_lead(lead_id, db)
            if lead is None:
                raise AgentToolError(
                    code="tool_db_lead_not_found",
                    message=f"Lead {lead_id} not found",
                )

            # ── Skip if already processed ─────────────────────────────────────
            if lead.status not in ("discovered", "analyzing"):
                leads_skipped += 1
                self.log.info(
                    "analyst.lead_skipped",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                    lead_status=lead.status,
                )
                continue

            # ── Idempotency guard ─────────────────────────────────────────────
            idem_key = f"{task.id}:analyze:{lead_id}"
            existing_idem = await get_task_by_idempotency_key(idem_key, db)
            if existing_idem is not None:
                # This lead was already scored in a previous (retried) run
                leads_skipped += 1
                self.log.info(
                    "analyst.idempotent_skip",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                )
                continue

            leads_analyzed += 1

            # Mark as analyzing in DB before LLM call
            if not dry_run:
                await update_lead(lead_id, {"status": "analyzing"}, db)

            # ── Deterministic signals ─────────────────────────────────────────
            det_signals = self._deterministic_signals(lead)

            # ── Fast-path: auto-discard candidates (no LLM call needed) ───────
            # Quick upper-bound: if max possible deterministic score < auto_discard,
            # skip the LLM and disqualify immediately.
            max_det_score = self._max_deterministic_score(det_signals, lead.sector)
            if max_det_score <= _AUTO_DISCARD_THRESHOLD:
                self.log.info(
                    "analyst.auto_discard",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                    max_det_score=max_det_score,
                )
                if not dry_run:
                    await self._persist_result(
                        lead_id=lead_id,
                        score=max_det_score,
                        qualified=False,
                        suggested_service_type=self._dominant_service(det_signals),
                        gap_signals={},
                        gap_summary="Lead non qualificato per assenza di segnali di gap rilevanti.",
                        estimated_value_eur=None,
                        disqualify_reason=f"auto_discard: score {max_det_score} ≤ {_AUTO_DISCARD_THRESHOLD}",
                        idem_key=idem_key,
                        task=task,
                        db=db,
                    )
                leads_disqualified += 1
                continue

            # ── LLM analysis ──────────────────────────────────────────────────
            try:
                llm_result = await self._call_llm(lead, det_signals)
            except (json.JSONDecodeError, KeyError, ValueError):
                self.log.warning(
                    "analyst.llm_parse_error",
                    task_id=str(task.id),
                    lead_id=str(lead_id),
                )
                llm_result = self._fallback_llm_result(lead, det_signals)

            # Merge: deterministic values always override LLM for same signal keys
            merged_signals: dict[str, dict[str, bool]] = {}
            for svc in _SERVICE_TYPES:
                llm_svc = llm_result.get("signals", {}).get(svc, {})
                det_svc = det_signals.get(svc, {})
                merged_signals[svc] = {**llm_svc, **det_svc}

            suggested_service_type: str = llm_result.get(
                "suggested_service_type", self._dominant_service(merged_signals)
            )
            gap_summary: str = llm_result.get("gap_summary", "")
            estimated_value_eur: int | None = llm_result.get("estimated_value_eur")

            # ── Score calculation ─────────────────────────────────────────────
            score, gap_signals = self._calculate_score(
                signals=merged_signals.get(suggested_service_type, {}),
                service_type=suggested_service_type,
                sector=lead.sector,
                lead=lead,
            )

            qualified = score >= _QUALIFY_THRESHOLD
            disqualify_reason = (
                None if qualified else f"score {score} < {_QUALIFY_THRESHOLD}"
            )

            self.log.info(
                "analyst.lead_scored",
                task_id=str(task.id),
                lead_id=str(lead_id),
                score=score,
                qualified=qualified,
                service_type=suggested_service_type,
            )

            # ── Persist ───────────────────────────────────────────────────────
            if not dry_run:
                await self._persist_result(
                    lead_id=lead_id,
                    score=score,
                    qualified=qualified,
                    suggested_service_type=suggested_service_type,
                    gap_signals=gap_signals,
                    gap_summary=gap_summary,
                    estimated_value_eur=estimated_value_eur,
                    disqualify_reason=disqualify_reason,
                    idem_key=idem_key,
                    task=task,
                    db=db,
                )

            if qualified:
                leads_qualified += 1
                qualified_lead_ids.append(str(lead_id))
            else:
                leads_disqualified += 1

        # ── Final gate: no qualified leads → block for operator ───────────────
        if leads_analyzed > 0 and leads_qualified == 0:
            raise GateNotApprovedError("agent_analyst_no_qualified_leads")

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "leads_analyzed": leads_analyzed,
                "leads_qualified": leads_qualified,
                "leads_disqualified": leads_disqualified,
                "leads_skipped": leads_skipped,
                "qualified_lead_ids": qualified_lead_ids,
            },
            next_tasks=["lead_profiler.enrich"] if leads_qualified > 0 else [],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _deterministic_signals(self, lead: Any) -> dict[str, dict[str, bool]]:
        """Signals computable directly from lead fields — no LLM needed."""
        rating = float(lead.google_rating or 0)
        review_count = int(lead.google_review_count or 0)
        has_website = lead.website_url is not None

        return {
            "web_design": {
                "no_website": not has_website,
                "low_google_rating": rating < 3.5 and review_count >= 20,
                "few_google_reviews": review_count < 10,
            },
            "consulting": {
                "high_review_volume": review_count > 100,
            },
            "digital_maintenance": {
                "existing_digital_presence": has_website,
                "high_update_frequency_sector": lead.sector in _HIGH_UPDATE_SECTORS,
            },
        }

    def _calculate_score(
        self,
        signals: dict[str, bool],
        service_type: str,
        sector: str,
        lead: Any,
    ) -> tuple[int, dict[str, bool]]:
        """
        Formula from config/scoring.yaml:
          raw = earned_weight / total_weight * 100
          score = clamp(raw * sector_multiplier + modifiers, 0, 100)
        """
        svc_signals_cfg = _SCORING.get(f"{service_type}_signals", {})
        total_weight = sum(s["weight"] for s in svc_signals_cfg.values())

        if total_weight == 0:
            raw = 0.0
        else:
            earned = sum(
                cfg["weight"]
                for key, cfg in svc_signals_cfg.items()
                if signals.get(key, False)
            )
            raw = earned / total_weight * 100.0

        # Sector multiplier
        multiplier = float(_SCORING.get("sector_multipliers", {}).get(sector, 1.0))
        score = raw * multiplier

        # General modifiers
        rating = float(lead.google_rating or 0)
        reviews = int(lead.google_review_count or 0)

        if rating >= 4.2 and reviews >= 30:
            score += 8   # high_google_rating bonus
        if not lead.website_url and not lead.phone:
            score -= 20  # no_website_no_phone malus
        if reviews < 5:
            score -= 5   # very_low_review_count malus

        final = max(0, min(100, int(round(score))))
        gap_signals = {k: v for k, v in signals.items() if v}
        return final, gap_signals

    def _max_deterministic_score(self, det_signals: dict[str, dict[str, bool]], sector: str) -> int:
        """
        Upper-bound score assuming all non-deterministic signals are True.
        Used for the auto-discard fast path.
        """
        best = 0
        for svc in _SERVICE_TYPES:
            svc_cfg = _SCORING.get(f"{svc}_signals", {})
            all_signals = {k: True for k in svc_cfg}
            all_signals.update(det_signals.get(svc, {}))
            score, _ = self._calculate_score(all_signals, svc, sector, _NullLead())
            best = max(best, score)
        return best

    def _dominant_service(self, signals: dict[str, dict[str, bool]]) -> str:
        """Return the service type with the most True signals."""
        counts = {svc: sum(1 for v in sigs.values() if v) for svc, sigs in signals.items()}
        return max(counts, key=lambda k: counts[k], default="web_design")

    async def _call_llm(self, lead: Any, det_signals: dict[str, dict[str, bool]]) -> dict:
        """
        Call Claude to assess non-deterministic gap signals and generate gap_summary.
        Input does NOT include PII (no phone, no business_name, no address).
        """
        user_input = {
            "lead_id": str(lead.id),
            "sector": lead.sector,
            "google_category": lead.google_category,
            "city": lead.city,
            "google_rating": float(lead.google_rating) if lead.google_rating else None,
            "google_review_count": lead.google_review_count,
            "has_website": lead.website_url is not None,
            "has_phone": lead.phone is not None,
            "deterministic_signals_already_computed": det_signals,
        }

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
        )

        text = response.content[0].text.strip()
        # Strip code fences if model wraps the JSON
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        return json.loads(text)

    def _fallback_llm_result(
        self, lead: Any, det_signals: dict[str, dict[str, bool]]
    ) -> dict:
        """Conservative fallback when LLM call fails or returns invalid JSON."""
        dominant = self._dominant_service(det_signals)
        return {
            "signals": {svc: {} for svc in _SERVICE_TYPES},
            "suggested_service_type": dominant,
            "gap_summary": "Analisi automatica non disponibile — revisione manuale consigliata.",
            "estimated_value_eur": None,
        }

    async def _persist_result(
        self,
        *,
        lead_id: UUID,
        score: int,
        qualified: bool,
        suggested_service_type: str,
        gap_signals: dict,
        gap_summary: str,
        estimated_value_eur: int | None,
        disqualify_reason: str | None,
        idem_key: str,
        task: AgentTask,
        db: AsyncSession,
    ) -> None:
        """Write scoring results to leads and register idempotency key."""
        new_status = "qualified" if qualified else "disqualified"

        await update_lead(
            lead_id,
            {
                "status": new_status,
                "lead_score": score,
                "qualified": qualified,
                "suggested_service_type": suggested_service_type,
                "gap_signals": {suggested_service_type: gap_signals},
                "gap_summary": gap_summary,
                "estimated_value_eur": estimated_value_eur,
                "service_gap_detected": bool(gap_signals),
                "disqualify_reason": disqualify_reason,
            },
            db,
        )

        # Register idempotency key so retried tasks skip this lead
        await create_task(
            type="analyst.score_lead",
            agent="analyst",
            payload={"lead_id": str(lead_id), "score": score},
            db=db,
            idempotency_key=idem_key,
        )


class _NullLead:
    """Dummy lead for score upper-bound calculation (no actual data needed)."""

    google_rating = None
    google_review_count = None
    website_url = None
    phone = None
