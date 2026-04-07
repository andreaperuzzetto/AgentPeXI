from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

import anthropic
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError
from tools.db_tools import (
    MaxProposalVersionsError,
    create_proposal,
    create_task,
    get_deal,
    get_latest_proposal,
    get_lead,
    get_task_by_idempotency_key,
    update_deal,
)
from tools.file_store import file_exists, get_presigned_url, upload_file
from tools.pdf_generator import render_pdf

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_PROPOSAL_TEMPLATE = str(_ROOT / "config" / "templates" / "proposal" / "base.html")
_PROPOSAL_BASE_URL = str(_ROOT / "config" / "templates" / "proposal")

_PRICING: dict = yaml.safe_load((_ROOT / "config" / "pricing.yaml").read_text())
_SCORING: dict = yaml.safe_load((_ROOT / "config" / "scoring.yaml").read_text())
_SECTORS: dict = yaml.safe_load((_ROOT / "config" / "sectors.yaml").read_text())["sectors"]

# ── Operator identity ─────────────────────────────────────────────────────────
_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")
_OPERATOR_EMAIL = os.environ.get("OPERATOR_EMAIL", "")
_OPERATOR_PHONE = os.environ.get("OPERATOR_PHONE", "")

# ── System prompt (module-level) ──────────────────────────────────────────────
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

# ── Service type labels ───────────────────────────────────────────────────────
_SERVICE_LABELS: dict[str, str] = {
    "web_design": "Web Design",
    "consulting": "Consulenza",
    "digital_maintenance": "Manutenzione Digitale",
}

# ── Artifact section titles per service type ──────────────────────────────────
_ARTIFACT_SECTION: dict[str, tuple[str, str]] = {
    "web_design": (
        "Come apparirà il vostro sito",
        "Anteprima del mockup personalizzato realizzato per la vostra attività.",
    ),
    "consulting": (
        "Il nostro piano di lavoro",
        "Visualizzazione della roadmap operativa e del processo di miglioramento.",
    ),
    "digital_maintenance": (
        "Lo stato attuale e il piano",
        "Schema architetturale dei sistemi attuali e piano di aggiornamento.",
    ),
}

# ── Max artifacts to show in PDF (keep PDF lean) ─────────────────────────────
_MAX_ARTIFACT_IMAGES = 4


class ProposalAgent(BaseAgent):
    """
    Generates the commercial proposal PDF for a deal.
    Reads: deals, leads. Writes: proposals, tasks, MinIO PDF.
    Does NOT send emails — that is the Sales Agent's responsibility.
    """

    agent_name = "proposal"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        deal_id_str = payload.get("deal_id")
        lead_id_str = payload.get("lead_id")
        if not deal_id_str or not lead_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: deal_id, lead_id",
            )

        deal_id = UUID(str(deal_id_str))
        lead_id = UUID(str(lead_id_str))
        artifact_keys: list[str] = payload.get("artifact_paths", [])
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load entities ─────────────────────────────────────────────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        lead = await get_lead(lead_id, db)
        if lead is None:
            raise AgentToolError(code="tool_db_lead_not_found", message=f"Lead {lead_id}")

        service_type: str = deal.service_type

        # ── Determine proposal version ────────────────────────────────────────
        latest = await get_latest_proposal(deal_id, db)
        version = (latest.version + 1) if latest else 1
        if version > 5:
            raise AgentToolError(
                code="agent_proposal_max_versions",
                message=f"Deal {deal_id} ha già 5 versioni di proposta",
            )

        pdf_minio_key = f"clients/{deal_id}/proposals/v{version}.pdf"

        # ── Idempotency: skip if PDF already exists on MinIO ──────────────────
        idem_key = f"{task.id}:generate_proposal:{deal_id}:v{version}"
        if not dry_run:
            if await file_exists(pdf_minio_key):
                self.log.info(
                    "proposal.already_exists",
                    task_id=str(task.id),
                    deal_id=str(deal_id),
                    version=version,
                )
                return self._result_from_existing(task, deal_id, latest, service_type)

            existing_idem = await get_task_by_idempotency_key(idem_key, db)
            if existing_idem is not None:
                return self._result_from_existing(task, deal_id, latest, service_type)

        # ── Select pricing tier ───────────────────────────────────────────────
        estimated_eur: int = int(getattr(lead, "estimated_value_eur", 0) or 0)
        tier = _select_tier(service_type, estimated_eur)
        tier_data = _PRICING[service_type]["tiers"][tier]
        pricing = _compute_pricing(tier_data, estimated_eur)

        # ── Build gap signals as human-readable labels ────────────────────────
        gap_signal_labels = _gap_signal_labels(
            getattr(lead, "gap_signals", None), _SCORING
        )

        # ── Generate LLM content ──────────────────────────────────────────────
        llm_content = await self._generate_content(lead, service_type, tier, tier_data, gap_signal_labels, task)

        # ── Resolve artifact presigned URLs (desktop variants only, max 4) ───
        desktop_keys = [k for k in artifact_keys if "_desktop" in k][:_MAX_ARTIFACT_IMAGES]
        artifact_urls: list[str] = []
        if not dry_run:
            for key in desktop_keys:
                try:
                    url = await get_presigned_url(key, expires_in_seconds=7200)
                    artifact_urls.append(url)
                except Exception:
                    # Non-critical: skip missing artifact
                    self.log.warning(
                        "proposal.artifact_url_failed",
                        task_id=str(task.id),
                        deal_id=str(deal_id),
                    )

        # ── Build Jinja2 template context ─────────────────────────────────────
        art_title, art_desc = _ARTIFACT_SECTION.get(
            service_type, ("Artefatti", "")
        )
        context = {
            # Business identity
            "business_name": getattr(lead, "business_name", ""),
            "sector_label": _sector_label(getattr(lead, "sector", "")),
            "service_type_label": _SERVICE_LABELS.get(service_type, service_type),
            # Operator
            "operator_name": _OPERATOR_NAME,
            "operator_email": _OPERATOR_EMAIL,
            "operator_phone": _OPERATOR_PHONE,
            # Dates
            "proposal_date": datetime.utcnow().strftime("%d/%m/%Y"),
            # Gap analysis
            "gap_summary": getattr(lead, "gap_summary", "") or llm_content.get("gap_summary", ""),
            "gap_signals": gap_signal_labels,
            # Solution
            "solution_summary": llm_content["solution_summary"],
            "deliverables": [
                {"title": d, "description": ""} for d in tier_data.get("deliverables", [])
            ],
            # Artifacts section
            "artifact_paths": artifact_urls,
            "artifact_section_title": art_title,
            "artifact_section_desc": art_desc,
            # ROI
            "roi_metrics": llm_content["roi_metrics"],
            "roi_summary": llm_content.get("roi_summary", ""),
            # Pricing
            "total_price_formatted": pricing["total_formatted"],
            "deposit_amount_formatted": pricing["deposit_formatted"],
            "delivery_amount_formatted": pricing["delivery_formatted"],
            "trailing_amount_formatted": pricing["trailing_formatted"],
            # Timeline
            "timeline_weeks": pricing.get("timeline_weeks") or tier_data.get("timeline_weeks", 4),
            "milestones": llm_content["milestones"],
            # Portal (not yet generated — placeholder)
            "portal_url": "",
        }

        # ── Render PDF ────────────────────────────────────────────────────────
        if dry_run:
            return AgentResult(
                task_id=task.id,
                success=True,
                output={
                    "deal_id": str(deal_id),
                    "proposal_id": None,
                    "proposal_version": version,
                    "pdf_path": pdf_minio_key,
                    "service_type": service_type,
                    "tier": tier,
                    "timeline_weeks": context["timeline_weeks"],
                    "estimated_value_eur": estimated_eur,
                },
                next_tasks=["sales.send_proposal"],
            )

        with tempfile.NamedTemporaryFile(
            suffix=".pdf", prefix=f"agentpexi_proposal_{deal_id}_", delete=False
        ) as tmp:
            pdf_local_path = tmp.name

        try:
            await render_pdf(
                template_path=_PROPOSAL_TEMPLATE,
                context=context,
                output_path=pdf_local_path,
                base_url=_PROPOSAL_BASE_URL,
            )

            # ── Upload PDF to MinIO ───────────────────────────────────────────
            await upload_file(pdf_local_path, pdf_minio_key)

        finally:
            try:
                os.unlink(pdf_local_path)
            except OSError:
                pass

        # ── Create proposal record ────────────────────────────────────────────
        try:
            proposal = await create_proposal(
                deal_id=deal_id,
                data={
                    "pdf_path": pdf_minio_key,
                    "gap_summary": context["gap_summary"],
                    "solution_summary": context["solution_summary"],
                    "service_type": service_type,
                    "deliverables_json": [d["title"] for d in context["deliverables"]],
                    "pricing_json": {
                        "tier": tier,
                        "tier_label": tier_data["label"],
                        "total_eur": pricing["total_eur"],
                        "deposit_pct": pricing["deposit_pct"],
                        "delivery_pct": pricing["delivery_pct"],
                        "trailing_pct": pricing["trailing_pct"],
                        "is_monthly": pricing["is_monthly"],
                    },
                    "timeline_weeks": context["timeline_weeks"],
                    "roi_summary": context["roi_summary"],
                    "artifact_paths": artifact_keys,
                },
                db=db,
            )
        except MaxProposalVersionsError:
            raise AgentToolError(
                code="agent_proposal_max_versions",
                message=f"Deal {deal_id}: superato limite 5 versioni proposta",
            )

        # ── Update deal status → proposal_ready ───────────────────────────────
        await update_deal(deal_id, {"status": "proposal_ready"}, db)

        # ── Register idempotency key ──────────────────────────────────────────
        await create_task(
            type="proposal.generate",
            agent="proposal",
            payload={
                "deal_id": str(deal_id),
                "proposal_id": str(proposal.id),
                "version": version,
            },
            db=db,
            deal_id=deal_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "proposal.generated",
            task_id=str(task.id),
            deal_id=str(deal_id),
            version=version,
            service_type=service_type,
            tier=tier,
        )

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "deal_id": str(deal_id),
                "proposal_id": str(proposal.id),
                "proposal_version": version,
                "pdf_path": pdf_minio_key,
                "service_type": service_type,
                "tier": tier,
                "timeline_weeks": context["timeline_weeks"],
                "estimated_value_eur": estimated_eur,
            },
            artifacts=[pdf_minio_key],
            next_tasks=["sales.send_proposal"],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _generate_content(
        self,
        lead: object,
        service_type: str,
        tier: str,
        tier_data: dict,
        gap_signal_labels: list[str],
        task: AgentTask,
    ) -> dict:
        """
        LLM call to generate: solution_summary, roi_metrics, milestones, roi_summary.
        Input: no PII — only sector, category, city, service context.
        """
        user_input = {
            "sector": getattr(lead, "sector", ""),
            "sector_label": _sector_label(getattr(lead, "sector", "")),
            "google_category": getattr(lead, "google_category", ""),
            "city": getattr(lead, "city", ""),
            "service_type": service_type,
            "service_type_label": _SERVICE_LABELS.get(service_type, service_type),
            "gap_summary": getattr(lead, "gap_summary", "") or "",
            "gap_signal_labels": gap_signal_labels,
            "tier": tier,
            "tier_label": tier_data.get("label", ""),
            "deliverables": tier_data.get("deliverables", []),
            "timeline_weeks": tier_data.get("timeline_weeks") or 4,
            "estimated_value_eur": int(getattr(lead, "estimated_value_eur", 0) or 0),
        }

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}
            ],
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
                "proposal.llm_parse_error",
                task_id=str(task.id),
                service_type=service_type,
            )
            return _fallback_content(service_type, tier_data)

    def _result_from_existing(
        self,
        task: AgentTask,
        deal_id: UUID,
        existing_proposal: object | None,
        service_type: str,
    ) -> AgentResult:
        """Return a success result pointing at the existing proposal."""
        version = getattr(existing_proposal, "version", 1) if existing_proposal else 1
        pdf_path = getattr(existing_proposal, "pdf_path", "") if existing_proposal else ""
        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "deal_id": str(deal_id),
                "proposal_id": str(getattr(existing_proposal, "id", "")) if existing_proposal else "",
                "proposal_version": version,
                "pdf_path": pdf_path,
                "service_type": service_type,
                "tier": "",
                "timeline_weeks": getattr(existing_proposal, "timeline_weeks", 4) if existing_proposal else 4,
                "estimated_value_eur": 0,
            },
            artifacts=[pdf_path] if pdf_path else [],
            next_tasks=["sales.send_proposal"],
        )


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _select_tier(service_type: str, estimated_eur: int) -> str:
    """Select mini/standard/premium based on estimated deal value."""
    _MINI_THRESHOLDS: dict[str, int] = {
        "consulting": 1500,
        "web_design": 1200,
        "digital_maintenance": 1200,
    }
    _STANDARD_THRESHOLDS: dict[str, int] = {
        "consulting": 4500,
        "web_design": 3500,
        "digital_maintenance": 99999,  # standard is the top for monthly
    }
    mini_limit = _MINI_THRESHOLDS.get(service_type, 1200)
    std_limit = _STANDARD_THRESHOLDS.get(service_type, 3500)

    if estimated_eur <= mini_limit:
        return "mini"
    elif estimated_eur <= std_limit:
        return "standard"
    return "premium"


def _compute_pricing(tier_data: dict, estimated_eur: int) -> dict:
    """Compute total and split amounts from tier config and estimated value."""
    is_monthly = tier_data.get("billing_model") == "monthly"

    min_cents = tier_data.get("amount_min_cents", 0)
    max_cents = tier_data.get("amount_max_cents", min_cents)
    total_cents = max(min_cents, min(max_cents, estimated_eur * 100))
    total_eur = total_cents / 100

    if is_monthly:
        return {
            "is_monthly": True,
            "total_eur": total_eur,
            "total_formatted": f"€ {total_eur:,.0f}/mese".replace(",", "."),
            "deposit_formatted": f"€ {total_eur:,.0f}".replace(",", "."),
            "delivery_formatted": "—",
            "trailing_formatted": "—",
            "deposit_pct": 100,
            "delivery_pct": 0,
            "trailing_pct": 0,
            "timeline_weeks": None,
        }

    deposit = total_cents * 30 // 100
    delivery = total_cents * 60 // 100
    trailing = total_cents - deposit - delivery

    def fmt(cents: int) -> str:
        return f"€ {cents / 100:,.0f}".replace(",", ".")

    return {
        "is_monthly": False,
        "total_eur": total_eur,
        "total_formatted": fmt(total_cents),
        "deposit_formatted": fmt(deposit),
        "delivery_formatted": fmt(delivery),
        "trailing_formatted": fmt(trailing),
        "deposit_pct": 30,
        "delivery_pct": 60,
        "trailing_pct": 10,
        "timeline_weeks": tier_data.get("timeline_weeks"),
    }


def _gap_signal_labels(gap_signals_json: dict | None, scoring: dict) -> list[str]:
    """Convert gap_signals JSONB {service_type: {signal_key: bool}} to list of Italian labels."""
    if not gap_signals_json:
        return []
    labels: list[str] = []
    for svc_type, signals in gap_signals_json.items():
        svc_config = scoring.get(f"{svc_type}_signals", {})
        for key, present in signals.items():
            if present and key in svc_config:
                labels.append(svc_config[key]["label"])
    return labels


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


def _fallback_content(service_type: str, tier_data: dict) -> dict:
    """Conservative fallback when LLM returns unparseable JSON."""
    timeline_weeks = tier_data.get("timeline_weeks") or 4
    milestones = [
        {"week": "1", "title": "Avvio progetto", "description": "Raccolta requisiti e pianificazione."},
        {"week": str(timeline_weeks), "title": "Consegna finale", "description": "Revisione e consegna deliverable."},
    ]
    return {
        "solution_summary": "Realizziamo una soluzione professionale su misura per la vostra attività, "
                            "con focus sulla qualità e sul rispetto dei tempi concordati.",
        "roi_metrics": [
            {"value": "+40%", "label": "Efficienza"},
            {"value": "24/7", "label": "Disponibilità"},
            {"value": "100%", "label": "Qualità garantita"},
            {"value": f"{timeline_weeks} sett.", "label": "Consegna"},
        ],
        "milestones": milestones,
        "roi_summary": "Il nostro intervento professionale genera valore misurabile fin dalla prima settimana.",
    }
