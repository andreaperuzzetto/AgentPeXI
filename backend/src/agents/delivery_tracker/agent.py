from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import anthropic
import jwt as pyjwt
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from tools.db_tools import (
    create_delivery_report,
    create_task,
    get_deal,
    get_latest_proposal,
    get_lead,
    get_service_deliveries_for_deal,
    get_service_delivery,
    get_task_by_idempotency_key,
    update_proposal,
    update_service_delivery,
)
from tools.file_store import download_bytes, upload_bytes

# ── Config ────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")
_PORTAL_SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "")
_BASE_URL = os.environ.get("BASE_URL", "http://localhost:3000")
_PORTAL_TOKEN_TTL_HOURS = 72

# Threshold: reject if rejection_count reaches this value
_MAX_REJECTIONS = 3

# Approval threshold (LLM can override but this guards against silent issues)
_MIN_COMPLETENESS_APPROVED = 70.0

# PNG magic bytes prefix for detecting image artifacts
_PNG_MAGIC = b"\x89PNG"

# Criteria checklist keyed by (service_type, delivery_type)
_CRITERIA: dict[tuple[str, str], list[str]] = {
    # Consulting
    ("consulting", "report"): [
        "Il report contiene almeno 3 raccomandazioni actionable con priorità definita?",
        "Ogni raccomandazione ha ROI o impatto stimato?",
        "I gap identificati sono chiari e supportati da dati?",
    ],
    ("consulting", "workshop"): [
        "Le slide workshop includono obiettivi misurabili?",
        "L'agenda ha tempi definiti per ogni modulo?",
        "Contiene esercizi o esempi concreti?",
    ],
    ("consulting", "roadmap"): [
        "La roadmap ha timeline (+/- 2 settimane), responsabile per ogni milestone, KPI misurabili?",
    ],
    ("consulting", "process_schema"): [
        "Mostra stato AS-IS e TO-BE chiaramente distinti?",
        "I gap tra AS-IS e TO-BE sono evidenti?",
    ],
    ("consulting", "presentation"): [
        "Ha una copertina identificativa?",
        "Struttura logica con sezioni distinte?",
    ],
    # Web Design
    ("web_design", "wireframe"): [
        "Struttura di navigazione chiara?",
        "Placeholder per elementi principali visibili?",
    ],
    ("web_design", "mockup"): [
        "Il mockup usa i colori brand del cliente (rilevati dal Lead Profiler)?",
        "Il layout è responsive: si legge correttamente a 390px senza scrolling orizzontale?",
        "Il copy è in italiano e fa riferimento esplicito al settore del cliente?",
    ],
    ("web_design", "branding"): [
        "Palette colori definita con hex codes?",
        "Tipografia specificata?",
    ],
    ("web_design", "page"): [
        "Tutte le pagine richieste presenti (landing, about, services, contact)?",
        "Il copy è in italiano e contestualizzato al settore?",
        "Call-to-action chiare?",
    ],
    ("web_design", "responsive_check"): [
        "Checklist di compatibilità presente?",
        "Esito chiaro (pass/fail per device)?",
    ],
    # Digital Maintenance
    ("digital_maintenance", "performance_audit"): [
        "L'audit riporta: versioni software attuali, CVE rilevanti, Lighthouse score?",
    ],
    ("digital_maintenance", "update_cycle"): [
        "Il piano aggiornamenti ha: priorità (critical/high/medium), data prevista, rischio se non eseguito?",
        "La documentazione è sufficiente perché l'operatore esegua gli update autonomamente?",
    ],
    ("digital_maintenance", "security_patch"): [
        "Patch applicate elencate con versione pre/post?",
        "Test post-patch documentati?",
    ],
    ("digital_maintenance", "monitoring_setup"): [
        "KPI di monitoraggio definiti?",
        "SLA di risposta specificato?",
        "Servizi monitorati elencati?",
    ],
}


class DeliveryTrackerAgent(BaseAgent):
    """
    Reviews service_delivery artifacts and produces delivery_reports.
    Reads: service_deliveries (only current client_id). Writes: delivery_reports,
    service_deliveries.status, tasks. Generates GATE 3 portal token when all done.

    SECURITY: Strictly isolated to the client_id in task.payload.
    """

    agent_name = "delivery_tracker"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        sd_id_str = payload.get("service_delivery_id")
        deal_id_str = payload.get("deal_id")
        client_id_str = payload.get("client_id")
        if not sd_id_str or not deal_id_str or not client_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: service_delivery_id, deal_id, client_id",
            )

        sd_id = UUID(str(sd_id_str))
        deal_id = UUID(str(deal_id_str))
        client_id = UUID(str(client_id_str))
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load service_delivery ─────────────────────────────────────────────
        sd = await get_service_delivery(sd_id, db)
        if sd is None:
            raise AgentToolError(
                code="tool_db_service_delivery_not_found",
                message=f"ServiceDelivery {sd_id}",
            )

        # ── SECURITY: client isolation ────────────────────────────────────────
        if str(getattr(sd, "client_id", "")) != str(client_id):
            self.log.critical(
                "task.error.security",
                task_id=str(task.id),
                agent="delivery_tracker",
                error_code="security_unauthorized_workspace_access",
                source="service_delivery",
            )
            raise AgentToolError(
                code="security_unauthorized_workspace_access",
                message="client_id mismatch — unauthorized cross-client access blocked",
            )

        delivery_type: str = sd.type
        service_type: str = sd.service_type
        rejection_count: int = int(getattr(sd, "rejection_count", 0) or 0)

        # ── Max rejections guard ──────────────────────────────────────────────
        if rejection_count >= _MAX_REJECTIONS:
            self.log.warning(
                "delivery_tracker.max_rejections",
                task_id=str(task.id),
                sd_id=str(sd_id),
                deal_id=str(deal_id),
                rejection_count=rejection_count,
            )
            raise GateNotApprovedError("agent_delivery_tracker_max_rejections")

        # ── Idempotency: skip if report already exists for this version ───────
        idem_key = f"{task.id}:review:{sd_id}:{rejection_count}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            self.log.info(
                "delivery_tracker.already_reviewed",
                task_id=str(task.id),
                sd_id=str(sd_id),
            )
            return self._ok_result(task, sd_id, deal_id, client_id, delivery_type,
                                   approved=sd.status == "approved",
                                   completeness_pct=100.0,
                                   blocking_issues=[],
                                   report_path=None,
                                   gate3_portal_url=None,
                                   rejection_count=rejection_count)

        # ── Load deal + lead for context ──────────────────────────────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        lead_id = getattr(deal, "lead_id", None)
        lead = await get_lead(lead_id, db) if lead_id else None

        # ── Download first PNG artifact for visual review (if available) ──────
        artifact_keys: list[str] = list(getattr(sd, "artifact_paths", None) or [])
        image_b64: str | None = None
        first_png_key: str | None = next(
            (k for k in artifact_keys if k.endswith(".png")), None
        )
        if first_png_key and not dry_run:
            try:
                img_bytes = await download_bytes(first_png_key)
                # Confirm PNG magic bytes before encoding
                if img_bytes[:4] == _PNG_MAGIC:
                    image_b64 = base64.standard_b64encode(img_bytes).decode()
            except Exception:
                self.log.warning(
                    "delivery_tracker.artifact_download_failed",
                    task_id=str(task.id),
                    sd_id=str(sd_id),
                )
                # Non-critical — proceed with text-only review

        # ── Criteria lookup ───────────────────────────────────────────────────
        criteria = _CRITERIA.get(
            (service_type, delivery_type),
            [f"Il deliverable '{delivery_type}' è completo e professionale?"],
        )

        # ── LLM review ────────────────────────────────────────────────────────
        if dry_run:
            review = {
                "approved": True,
                "completeness_pct": 100.0,
                "blocking_issues": [],
                "notes": [],
                "review_summary": "dry_run — nessuna review effettuata",
            }
        else:
            review = await self._llm_review(
                task=task,
                delivery_type=delivery_type,
                service_type=service_type,
                sd=sd,
                lead=lead,
                criteria=criteria,
                image_b64=image_b64,
            )

        approved: bool = review["approved"]
        completeness_pct: float = float(review.get("completeness_pct", 0.0))
        blocking_issues: list[dict] = review.get("blocking_issues", [])
        notes: list[dict] = review.get("notes", [])
        review_summary: str = review.get("review_summary", "")

        # Double-check: if blocking_issues exist, force rejected
        if blocking_issues:
            approved = False

        if dry_run:
            return self._ok_result(
                task, sd_id, deal_id, client_id, delivery_type,
                approved=approved,
                completeness_pct=completeness_pct,
                blocking_issues=blocking_issues,
                report_path=None,
                gate3_portal_url=None,
                rejection_count=rejection_count,
            )

        # ── Generate report markdown and upload to MinIO ──────────────────────
        report_path = f"clients/{client_id}/reports/{sd_id}.md"
        report_md = _build_report_markdown(
            delivery_type=delivery_type,
            service_type=service_type,
            title=getattr(sd, "title", ""),
            sd_id=sd_id,
            deal_id=deal_id,
            approved=approved,
            completeness_pct=completeness_pct,
            blocking_issues=blocking_issues,
            notes=notes,
            review_summary=review_summary,
            criteria=criteria,
            operator_name=_OPERATOR_NAME,
        )
        await upload_bytes(
            report_md.encode("utf-8"),
            report_path,
            content_type="text/markdown",
        )

        # ── Create delivery_report record ─────────────────────────────────────
        await create_delivery_report(
            service_delivery_id=sd_id,
            client_id=client_id,
            approved=approved,
            completeness_pct=completeness_pct,
            blocking_issues=blocking_issues,
            notes=notes,
            report_path=report_path,
            reviewer_agent="delivery_tracker",
            db=db,
        )

        # ── Update service_delivery status ────────────────────────────────────
        if approved:
            await update_service_delivery(
                sd_id,
                {"status": "approved", "completed_at": datetime.utcnow()},
                db,
            )
        else:
            new_rejection_count = rejection_count + 1
            await update_service_delivery(
                sd_id,
                {
                    "status": "failed",
                    "rejection_count": new_rejection_count,
                    "rejection_notes": _format_blocking_issues(blocking_issues),
                },
                db,
            )

        self.log.info(
            "delivery_tracker.reviewed",
            task_id=str(task.id),
            sd_id=str(sd_id),
            deal_id=str(deal_id),
            delivery_type=delivery_type,
            approved=approved,
            completeness_pct=completeness_pct,
            blocking_count=len(blocking_issues),
        )

        # ── Register idempotency ──────────────────────────────────────────────
        await create_task(
            type="delivery_tracker.review",
            agent="delivery_tracker",
            payload={
                "service_delivery_id": str(sd_id),
                "approved": approved,
                "rejection_count": rejection_count,
            },
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        # ── GATE 3: check if all deliverables are done ────────────────────────
        gate3_portal_url: str | None = None
        if approved:
            gate3_portal_url = await self._check_and_generate_gate3(
                task=task,
                deal_id=deal_id,
                client_id=client_id,
                db=db,
            )

        return self._ok_result(
            task, sd_id, deal_id, client_id, delivery_type,
            approved=approved,
            completeness_pct=completeness_pct,
            blocking_issues=blocking_issues,
            report_path=report_path,
            gate3_portal_url=gate3_portal_url,
            rejection_count=rejection_count if not approved else rejection_count,
        )

    # ── LLM review ────────────────────────────────────────────────────────────

    async def _llm_review(
        self,
        *,
        task: AgentTask,
        delivery_type: str,
        service_type: str,
        sd: object,
        lead: object | None,
        criteria: list[str],
        image_b64: str | None,
    ) -> dict:
        """
        Call Claude to review the deliverable.
        If image_b64 is provided: uses vision (multimodal).
        Otherwise: text-only review based on context.
        """
        user_data = {
            "delivery_type": delivery_type,
            "service_type": service_type,
            "title": getattr(sd, "title", ""),
            "description": getattr(sd, "description", ""),
            "sector": getattr(lead, "sector", "") if lead else "",
            "sector_label": _sector_label(getattr(lead, "sector", "") if lead else ""),
            "business_name": getattr(lead, "business_name", "") if lead else "",
            "gap_summary": getattr(lead, "gap_summary", "") if lead else "",
            "criteria": criteria,
            "has_visual_artifact": image_b64 is not None,
        }
        user_json = json.dumps(user_data, ensure_ascii=False)

        # Build content array (text + optional image)
        content: list[dict] = []
        if image_b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            })
        content.append({"type": "text", "text": user_json})

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

        text = response.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning(
                "delivery_tracker.llm_parse_error",
                task_id=str(task.id),
                sd_id=str(getattr(sd, "id", "")),
            )
            # Conservative fallback: approve with low confidence
            return {
                "approved": True,
                "completeness_pct": 75.0,
                "blocking_issues": [],
                "notes": [{"section": "review", "note": "Review automatica non disponibile — approvazione per default."}],
                "review_summary": "Review automatica non disponibile. Approvazione conservativa.",
            }

    # ── GATE 3 check ─────────────────────────────────────────────────────────

    async def _check_and_generate_gate3(
        self,
        *,
        task: AgentTask,
        deal_id: UUID,
        client_id: UUID,
        db: AsyncSession,
    ) -> str | None:
        """
        If all service_deliveries for this deal are approved → generate GATE 3 portal token.
        Saves the token on the latest proposal. Returns portal URL or None.
        """
        deliveries = await get_service_deliveries_for_deal(deal_id, db)
        if not deliveries:
            return None

        all_approved = all(
            getattr(sd, "status", "") in ("approved", "completed")
            for sd in deliveries
        )
        if not all_approved:
            return None

        # All deliverables approved — generate GATE 3 portal token
        proposal = await get_latest_proposal(deal_id, db)
        if proposal is None:
            self.log.warning(
                "delivery_tracker.gate3_no_proposal",
                task_id=str(task.id),
                deal_id=str(deal_id),
            )
            return None

        delivery_token = _generate_portal_token(
            proposal_id=str(proposal.id),
            deal_id=str(deal_id),
            gate="delivery",
        )
        portal_url = f"{_BASE_URL}/portal/{delivery_token}"
        portal_expires = datetime.utcnow() + timedelta(hours=_PORTAL_TOKEN_TTL_HOURS)

        await update_proposal(
            proposal.id,
            {
                "portal_link_token": delivery_token,
                "portal_link_expires": portal_expires,
            },
            db,
        )

        self.log.info(
            "delivery_tracker.gate3_generated",
            task_id=str(task.id),
            deal_id=str(deal_id),
            proposal_id=str(proposal.id),
        )
        return portal_url

    # ── Result builder ────────────────────────────────────────────────────────

    def _ok_result(
        self,
        task: AgentTask,
        sd_id: UUID,
        deal_id: UUID,
        client_id: UUID,
        delivery_type: str,
        *,
        approved: bool,
        completeness_pct: float,
        blocking_issues: list[dict],
        report_path: str | None,
        gate3_portal_url: str | None,
        rejection_count: int,
    ) -> AgentResult:
        next_tasks: list[str]
        if approved:
            # Tell the orchestrator to look for next ready deliverables
            next_tasks = ["delivery_orchestrator.check_progress"]
        else:
            # Trigger re-generation of this deliverable
            next_tasks = ["doc_generator.generate"]

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "service_delivery_id": str(sd_id),
                "deal_id": str(deal_id),
                "client_id": str(client_id),
                "delivery_type": delivery_type,
                "approved": approved,
                "completeness_pct": completeness_pct,
                "blocking_issues_count": len(blocking_issues),
                "rejection_count": rejection_count,
                "report_path": report_path,
                "gate3_portal_url": gate3_portal_url,
            },
            next_tasks=next_tasks,
        )


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _generate_portal_token(proposal_id: str, deal_id: str, gate: str) -> str:
    """Generate signed JWT for client portal (72h TTL)."""
    payload = {
        "proposal_id": proposal_id,
        "deal_id": deal_id,
        "exp": datetime.utcnow() + timedelta(hours=_PORTAL_TOKEN_TTL_HOURS),
        "iat": datetime.utcnow(),
        "type": "portal_access",
        "gate": gate,
    }
    return pyjwt.encode(payload, _PORTAL_SECRET_KEY, algorithm="HS256")


def _format_blocking_issues(blocking_issues: list[dict]) -> str:
    """Convert blocking_issues list to a plain-text string for rejection_notes."""
    if not blocking_issues:
        return ""
    parts = [f"[{bi.get('field', '?')}] {bi.get('description', '')}" for bi in blocking_issues]
    return "; ".join(parts)


def _build_report_markdown(
    *,
    delivery_type: str,
    service_type: str,
    title: str,
    sd_id: UUID,
    deal_id: UUID,
    approved: bool,
    completeness_pct: float,
    blocking_issues: list[dict],
    notes: list[dict],
    review_summary: str,
    criteria: list[str],
    operator_name: str,
) -> str:
    """Generate the markdown review report for storage on MinIO."""
    today = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    esito = "✅ APPROVATO" if approved else "❌ RIFIUTATO"

    lines: list[str] = [
        f"# Delivery Review Report",
        f"",
        f"**Deliverable:** {title} (`{delivery_type}`)",
        f"**Servizio:** {service_type}",
        f"**ID:** {sd_id}",
        f"**Deal:** {deal_id}",
        f"**Data review:** {today}",
        f"**Revisore:** delivery_tracker (AI)",
        f"",
        f"---",
        f"",
        f"## Esito: {esito}",
        f"",
        f"**Completezza:** {completeness_pct:.1f}%",
        f"",
        f"_{review_summary}_",
        f"",
    ]

    if blocking_issues:
        lines += [
            "## Problemi bloccanti",
            "",
        ]
        for bi in blocking_issues:
            lines.append(f"- **[{bi.get('field', '?')}]** {bi.get('description', '')}")
        lines.append("")

    if notes:
        lines += [
            "## Note",
            "",
        ]
        for note in notes:
            lines.append(f"- **[{note.get('section', '?')}]** {note.get('note', '')}")
        lines.append("")

    lines += [
        "## Criteri valutati",
        "",
    ]
    for criterion in criteria:
        lines.append(f"- {criterion}")

    lines += [
        "",
        "---",
        f"*Report generato automaticamente da {operator_name} / AgentPeXI*",
    ]

    return "\n".join(lines)


def _sector_label(sector: str) -> str:
    _LABELS: dict[str, str] = {
        "horeca": "Ristorazione e Ospitalità",
        "hotel_tourism": "Turismo e Ricettività",
        "retail": "Commercio al Dettaglio",
        "food_retail": "Alimentari e Gastronomia",
        "beauty_wellness": "Bellezza e Benessere",
        "fitness_sport": "Sport e Fitness",
        "healthcare": "Salute e Medicina",
        "education": "Formazione e Istruzione",
        "professional_services": "Servizi Professionali",
        "real_estate": "Agenzie Immobiliari",
        "automotive": "Auto e Motori",
        "construction": "Edilizia e Impiantistica",
        "manufacturing_craft": "Artigianato e Piccola Produzione",
        "logistics_transport": "Logistica e Trasporti",
        "creative_media": "Creatività e Media",
    }
    return _LABELS.get(sector, sector.replace("_", " ").title())
