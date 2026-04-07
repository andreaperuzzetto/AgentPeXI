from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

import anthropic
import jinja2
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError
from tools.db_tools import create_task, get_deal, get_lead, get_task_by_idempotency_key
from tools.file_store import file_exists, upload_file
from tools.mockup_renderer import render_to_png

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_TEMPLATES_DIR = _ROOT / "config" / "templates" / "artifacts"
_PROMPTS_DIR = Path(__file__).parent / "prompts"

# ── Operator identity (single-operator system) ────────────────────────────────
_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")

# ── Artifact pages per service type ──────────────────────────────────────────
_PAGES: dict[str, list[str]] = {
    "web_design": ["landing", "about", "services", "contact"],
    "consulting": ["roadmap", "workshop_structure", "process_schema", "presentation"],
    "digital_maintenance": ["architecture", "update_plan", "monitoring_dashboard"],
}

# Template path relative to _TEMPLATES_DIR: {service_type}/{page}.html
_TEMPLATE_SUBDIR: dict[str, str] = {
    "web_design": "web_design",
    "consulting": "consulting",
    "digital_maintenance": "digital_maintenance",
}

# Viewports: web_design gets desktop + mobile; others desktop only
_VP_DESKTOP = {"width": 1440, "height": 900}
_VP_MOBILE = {"width": 390, "height": 844}


def _viewports(service_type: str) -> list[tuple[str, dict]]:
    result = [("desktop", _VP_DESKTOP)]
    if service_type == "web_design":
        result.append(("mobile", _VP_MOBILE))
    return result


def _minio_key(deal_id: UUID, service_type: str, page: str, variant: str) -> str:
    """Canonical MinIO object key for a design artifact."""
    return f"clients/{deal_id}/artifacts/{service_type}/{page}_v1_{variant}.png"


class DesignAgent(BaseAgent):
    """
    Produces visual artifacts (PNG screenshots) for proposals.
    Reads: deals, leads. Writes: tasks, MinIO artifacts.
    One LLM call per execute() generates all Jinja2 contexts; Puppeteer renders each page.
    """

    agent_name = "design"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"
        # Lazy-loaded system prompts keyed by service_type
        self._system_prompts: dict[str, str] = {}

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
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load entities ─────────────────────────────────────────────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        lead = await get_lead(lead_id, db)
        if lead is None:
            raise AgentToolError(code="tool_db_lead_not_found", message=f"Lead {lead_id}")

        service_type: str = deal.service_type
        pages = _PAGES.get(service_type)
        if not pages:
            raise AgentToolError(
                code="validation_invalid_service_type",
                message=f"Unknown service_type: {service_type}",
            )

        # ── Generate all template contexts via single LLM call ─────────────────
        contexts = await self._generate_contexts(lead, deal, service_type, task)

        # ── Render + upload each page × viewport ─────────────────────────────
        artifact_paths: list[str] = []
        pages_rendered = 0
        pages_skipped = 0

        tmpdir = tempfile.mkdtemp(prefix="agentpexi_design_")
        try:
            for page in pages:
                # Merge shared context with page-specific context
                page_ctx = {**contexts.get("shared", {}), **contexts.get(page, {})}
                template_path = _TEMPLATES_DIR / _TEMPLATE_SUBDIR[service_type] / f"{page}.html"

                for variant, viewport in _viewports(service_type):
                    minio_key = _minio_key(deal_id, service_type, page, variant)
                    idem_key = f"{task.id}:render:{service_type}:{page}:{variant}"

                    # ── Idempotency: skip already-uploaded artifacts ──────────
                    if not dry_run:
                        already_exists = await file_exists(minio_key)
                        if already_exists:
                            self.log.info(
                                "design.artifact_skipped",
                                task_id=str(task.id),
                                deal_id=str(deal_id),
                                page=page,
                                variant=variant,
                            )
                            artifact_paths.append(minio_key)
                            pages_skipped += 1
                            continue

                        existing_idem = await get_task_by_idempotency_key(idem_key, db)
                        if existing_idem is not None:
                            artifact_paths.append(minio_key)
                            pages_skipped += 1
                            continue

                    # ── Render Jinja2 HTML ────────────────────────────────────
                    rendered_html = _render_jinja2(template_path, page_ctx)

                    html_tmp = os.path.join(tmpdir, f"{page}_{variant}.html")
                    with open(html_tmp, "w", encoding="utf-8") as fh:
                        fh.write(rendered_html)

                    if dry_run:
                        pages_rendered += 1
                        continue

                    # ── Render HTML → PNG via Puppeteer ───────────────────────
                    png_tmp = os.path.join(tmpdir, f"{page}_{variant}.png")
                    await render_to_png(
                        html_path=html_tmp,
                        output_path=png_tmp,
                        viewport_width=viewport["width"],
                        viewport_height=viewport["height"],
                    )

                    # ── Upload PNG to MinIO ────────────────────────────────────
                    await upload_file(png_tmp, minio_key)
                    artifact_paths.append(minio_key)

                    # ── Register idempotency key ──────────────────────────────
                    await create_task(
                        type="design.render_page",
                        agent="design",
                        payload={
                            "deal_id": str(deal_id),
                            "service_type": service_type,
                            "page": page,
                            "variant": variant,
                            "minio_key": minio_key,
                        },
                        db=db,
                        deal_id=deal_id,
                        idempotency_key=idem_key,
                    )

                    pages_rendered += 1
                    self.log.info(
                        "design.page_rendered",
                        task_id=str(task.id),
                        deal_id=str(deal_id),
                        page=page,
                        variant=variant,
                    )

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "deal_id": str(deal_id),
                "service_type": service_type,
                "artifact_paths": artifact_paths,
                "pages_rendered": pages_rendered,
                "pages_skipped": pages_skipped,
            },
            artifacts=artifact_paths,
            next_tasks=["proposal.generate"],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _generate_contexts(
        self,
        lead: object,
        deal: object,
        service_type: str,
        task: AgentTask,
    ) -> dict:
        """
        Single LLM call that returns all Jinja2 contexts for the given service_type.
        Input: only public/non-PII data (sector, category, gap_summary, city).
        """
        today = datetime.utcnow().strftime("%d/%m/%Y")

        user_input = {
            "business_name": getattr(lead, "business_name", ""),
            "sector": getattr(lead, "sector", ""),
            "sector_label": _sector_label(getattr(lead, "sector", "")),
            "google_category": getattr(lead, "google_category", ""),
            "city": getattr(lead, "city", ""),
            "gap_summary": getattr(lead, "gap_summary", ""),
            "estimated_value_eur": getattr(lead, "estimated_value_eur", None),
            "today_date": today,
            "operator_name": _OPERATOR_NAME,
        }

        system_prompt = self._load_system_prompt(service_type)

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}
            ],
        )

        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning(
                "design.llm_parse_error",
                task_id=str(task.id),
                service_type=service_type,
            )
            return _fallback_contexts(service_type, getattr(lead, "business_name", ""))

    def _load_system_prompt(self, service_type: str) -> str:
        """Load and cache the system prompt for a given service type."""
        if service_type not in self._system_prompts:
            path = _PROMPTS_DIR / f"system_{service_type}.md"
            self._system_prompts[service_type] = path.read_text(encoding="utf-8")
        return self._system_prompts[service_type]


# ── Module-level helpers (no I/O, no state) ───────────────────────────────────

def _render_jinja2(template_path: Path, context: dict) -> str:
    """Render a Jinja2 HTML template with the given context dict."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_path.parent)),
        autoescape=False,
        undefined=jinja2.Undefined,  # silently ignore missing vars → template defaults
    )
    template = env.get_template(template_path.name)
    return template.render(**context)


def _sector_label(sector: str) -> str:
    """Return Italian sector label for a sector key, falling back gracefully."""
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


def _fallback_contexts(service_type: str, business_name: str) -> dict:
    """
    Minimal fallback when LLM returns unparseable JSON.
    Returns a skeleton context so Jinja2 rendering doesn't raise KeyError.
    Templates use `| default(...)` filters for most variables, so a sparse dict is fine.
    """
    shared: dict = {"business_name": business_name, "operator_name": _OPERATOR_NAME}

    if service_type == "web_design":
        shared.update(
            {
                "brand_primary": "#0f172a",
                "brand_accent": "#0ea5e9",
                "brand_secondary": "#1e3a5f",
                "business_city": "",
                "business_phone": "+39 XXX XXX XXXX",
                "business_email": "info@example.it",
                "footer_tagline": "",
            }
        )
        pages: dict = {
            "landing": {"nav_links": [], "services": [], "hero_headline": business_name},
            "about": {"hero_stats": [], "company_values": [], "team_members": []},
            "services": {"services": [], "process_steps": []},
            "contact": {"contact_intro": "", "opening_hours": {}},
        }
    elif service_type == "consulting":
        shared.update(
            {"proposal_date": datetime.utcnow().strftime("%d/%m/%Y"), "sector_label": ""}
        )
        pages = {
            "roadmap": {"phases": [], "outputs": [], "timeline_weeks": 4},
            "workshop_structure": {
                "modules": [],
                "agenda": [],
                "learning_objectives": [],
                "participant_roles": [],
                "participants_count": "4–8",
                "modules_count": 0,
                "exercises_count": 0,
                "deliverables_count": 0,
                "workshop_title": "",
                "workshop_date": "",
                "total_duration": "",
                "service_type_label": "",
            },
            "process_schema": {"asis_steps": [], "tobe_steps": [], "impacts": []},
            "presentation": {
                "presentation_title": business_name,
                "is_cover": True,
                "service_type_label": "Consulenza",
                "highlight_word": "",
                "presentation_subtitle": "",
                "presentation_tags": [],
                "toc": [],
                "key_points": [],
                "stats": [],
                "show_key_points": False,
                "show_stats": False,
                "current_slide": 1,
                "total_slides": 1,
                "current_section_index": 1,
                "section_eyebrow": "",
                "section_title": "",
                "progress_percent": 0,
                "presentation_date": datetime.utcnow().strftime("%d/%m/%Y"),
            },
        }
    else:  # digital_maintenance
        shared.update(
            {
                "analysis_date": datetime.utcnow().strftime("%d/%m/%Y"),
                "sector_label": "",
            }
        )
        pages = {
            "architecture": {"current_systems": [], "intervention_steps": []},
            "update_plan": {
                "systems": [],
                "update_items": [],
                "monthly_phases": [],
                "plan_period": "",
                "next_review_date": "",
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "completed_count": 0,
            },
            "monitoring_dashboard": {
                "kpis": [],
                "uptime_services": [],
                "planned_updates": [],
                "last_update": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
                "sla_response": 4,
            },
        }

    return {"shared": shared, **pages}
