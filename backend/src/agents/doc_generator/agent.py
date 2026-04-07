from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import anthropic
import jinja2
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError
from tools.db_tools import (
    create_task,
    get_deal,
    get_lead,
    get_service_delivery,
    get_task_by_idempotency_key,
    update_service_delivery,
)
from tools.file_store import file_exists, upload_bytes, upload_file
from tools.mockup_renderer import VIEWPORT_DESKTOP, VIEWPORT_MOBILE, render_to_pdf, render_to_png
from tools.pdf_generator import render_pdf

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_TEMPLATES_DIR = _ROOT / "config" / "templates" / "artifacts"
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")

# ── Render specification ───────────────────────────────────────────────────────
# tool: "weasyprint" → render_pdf; "puppeteer" → render_to_png / render_to_pdf
# template: path relative to _TEMPLATES_DIR, or None → LLM generates full HTML
# outputs: list of output specs
#   ("pdf",)           → WeasyPrint A4 PDF
#   ("png", w, h)      → Puppeteer PNG at w×h
#   ("png_m", w, h)    → Puppeteer PNG mobile variant
#   ("pdf_p",)         → Puppeteer PDF
#   ("html",)          → Upload rendered HTML as-is

@dataclass(frozen=True)
class _Output:
    kind: str   # "pdf" | "png" | "png_m" | "pdf_p" | "html"
    width: int = 1440
    height: int = 900


@dataclass(frozen=True)
class _RenderSpec:
    tool: str             # "weasyprint" | "puppeteer"
    template: str | None  # relative to _TEMPLATES_DIR; None = LLM generates HTML
    outputs: tuple[_Output, ...] = field(default_factory=tuple)
    # For `page` type: render multiple templates in sequence
    extra_templates: tuple[str, ...] = field(default_factory=tuple)


_SPECS: dict[str, _RenderSpec] = {
    # ── Consulting ────────────────────────────────────────────────────────────
    "report": _RenderSpec(
        tool="weasyprint",
        template=None,       # LLM generates full HTML document
        outputs=(_Output("pdf"),),
    ),
    "workshop": _RenderSpec(
        tool="weasyprint",
        template="consulting/workshop_structure.html",
        outputs=(_Output("pdf"),),
    ),
    "roadmap": _RenderSpec(
        tool="weasyprint",
        template="consulting/roadmap.html",
        outputs=(_Output("pdf"),),
    ),
    "process_schema": _RenderSpec(
        tool="puppeteer",
        template="consulting/process_schema.html",
        outputs=(_Output("png", 1440, 900), _Output("pdf_p")),
    ),
    "presentation": _RenderSpec(
        tool="puppeteer",
        template="consulting/presentation.html",
        outputs=(_Output("png", 1440, 900), _Output("pdf_p")),
    ),
    # ── Web Design ────────────────────────────────────────────────────────────
    "wireframe": _RenderSpec(
        tool="puppeteer",
        template="web_design/landing.html",
        outputs=(_Output("png", 1440, 900),),
    ),
    "mockup": _RenderSpec(
        tool="puppeteer",
        template="web_design/landing.html",
        outputs=(_Output("png", 1440, 900), _Output("png_m", 390, 844)),
    ),
    "branding": _RenderSpec(
        tool="weasyprint",
        template=None,       # LLM generates branding guide HTML
        outputs=(_Output("pdf"),),
    ),
    "page": _RenderSpec(
        tool="puppeteer",
        template="web_design/landing.html",
        outputs=(_Output("png", 1440, 900), _Output("html")),
        extra_templates=(
            "web_design/about.html",
            "web_design/services.html",
            "web_design/contact.html",
        ),
    ),
    "responsive_check": _RenderSpec(
        tool="weasyprint",
        template=None,       # LLM generates responsive check report HTML
        outputs=(_Output("pdf"),),
    ),
    # ── Digital Maintenance ───────────────────────────────────────────────────
    "performance_audit": _RenderSpec(
        tool="weasyprint",
        template=None,       # LLM generates technical audit report HTML
        outputs=(_Output("pdf"),),
    ),
    "update_cycle": _RenderSpec(
        tool="weasyprint",
        template="digital_maintenance/update_plan.html",
        outputs=(_Output("pdf"),),
    ),
    "security_patch": _RenderSpec(
        tool="weasyprint",
        template=None,       # LLM generates security patch report HTML
        outputs=(_Output("pdf"),),
    ),
    "monitoring_setup": _RenderSpec(
        tool="puppeteer",
        template="digital_maintenance/monitoring_dashboard.html",
        outputs=(_Output("png", 1440, 900), _Output("pdf_p")),
    ),
}

# ── Page name → template file (for multi-page `page` type) ───────────────────
_PAGE_TEMPLATES: list[str] = [
    "web_design/landing.html",
    "web_design/about.html",
    "web_design/services.html",
    "web_design/contact.html",
]
_PAGE_NAMES: list[str] = ["landing", "about", "services", "contact"]


class DocGeneratorAgent(BaseAgent):
    """
    Generates actual service deliverables (PDFs/PNGs) and uploads them to MinIO.
    Reads: service_deliveries, deals, leads. Writes: service_deliveries, tasks, MinIO.

    SECURITY: Only accesses client_id from task.payload — never cross-client access.
    """

    agent_name = "doc_generator"

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

        # ── SECURITY: client isolation — never access another client's data ───
        if str(sd.client_id) != str(client_id):
            self.log.critical(
                "task.error.security",
                task_id=str(task.id),
                agent="doc_generator",
                error_code="security_unauthorized_workspace_access",
                source="service_delivery",
            )
            raise AgentToolError(
                code="security_unauthorized_workspace_access",
                message="client_id mismatch — unauthorized access attempt blocked",
            )

        # ── Idempotency: skip if already has artifacts ────────────────────────
        existing_artifacts = getattr(sd, "artifact_paths", None) or []
        idem_key = f"{task.id}:generate:{sd_id}"
        if existing_artifacts:
            self.log.info(
                "doc_generator.already_generated",
                task_id=str(task.id),
                sd_id=str(sd_id),
                artifacts=len(existing_artifacts),
            )
            return _ok_result(task, sd, existing_artifacts)

        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            # Previously started but artifacts might not be there yet (partial run)
            return _ok_result(task, sd, existing_artifacts)

        # ── Determine render spec ─────────────────────────────────────────────
        delivery_type: str = sd.type
        spec = _SPECS.get(delivery_type)
        if spec is None:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Unknown delivery_type: {delivery_type!r}",
            )

        # ── Load deal + lead context (no PII logged) ──────────────────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        lead_id = getattr(deal, "lead_id", None)
        lead = await get_lead(lead_id, db) if lead_id else None

        service_type: str = sd.service_type
        version: int = int(getattr(sd, "rejection_count", 0) or 0) + 1
        today = datetime.utcnow().strftime("%d/%m/%Y")

        # ── Generate content via LLM ──────────────────────────────────────────
        llm_mode = "html_document" if spec.template is None else "template_context"
        content = await self._generate_content(
            task=task,
            lead=lead,
            deal=deal,
            delivery_type=delivery_type,
            service_type=service_type,
            mode=llm_mode,
            today=today,
        )

        if dry_run:
            return AgentResult(
                task_id=task.id,
                success=True,
                output={
                    "service_delivery_id": str(sd_id),
                    "deal_id": str(deal_id),
                    "client_id": str(client_id),
                    "delivery_type": delivery_type,
                    "version": version,
                    "dry_run": True,
                    "artifact_paths": [],
                },
            )

        # ── Render + upload ───────────────────────────────────────────────────
        tmpdir = tempfile.mkdtemp(prefix="agentpexi_docgen_")
        artifact_keys: list[str] = []
        try:
            artifact_keys = await self._render_and_upload(
                tmpdir=tmpdir,
                spec=spec,
                content=content,
                llm_mode=llm_mode,
                deal_id=deal_id,
                service_type=service_type,
                delivery_type=delivery_type,
                version=version,
                task=task,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        # ── Update service_delivery: artifacts set, status → review ──────────
        await update_service_delivery(
            sd_id,
            {
                "artifact_paths": artifact_keys,
                "status": "review",
                "assigned_at": datetime.utcnow(),
            },
            db,
        )

        # ── Register idempotency key ──────────────────────────────────────────
        await create_task(
            type="doc_generator.generate",
            agent="doc_generator",
            payload={
                "service_delivery_id": str(sd_id),
                "deal_id": str(deal_id),
                "version": version,
            },
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "doc_generator.generated",
            task_id=str(task.id),
            sd_id=str(sd_id),
            deal_id=str(deal_id),
            delivery_type=delivery_type,
            version=version,
            artifacts=len(artifact_keys),
        )

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "service_delivery_id": str(sd_id),
                "deal_id": str(deal_id),
                "client_id": str(client_id),
                "delivery_type": delivery_type,
                "version": version,
                "artifact_paths": artifact_keys,
            },
            artifacts=artifact_keys,
            next_tasks=["delivery_tracker.review"],
        )

    # ── Render + upload dispatcher ────────────────────────────────────────────

    async def _render_and_upload(
        self,
        *,
        tmpdir: str,
        spec: _RenderSpec,
        content: Any,          # dict (template context) or str (full HTML)
        llm_mode: str,
        deal_id: UUID,
        service_type: str,
        delivery_type: str,
        version: int,
        task: AgentTask,
    ) -> list[str]:
        """Render document(s) and upload to MinIO. Returns list of artifact keys."""
        artifact_keys: list[str] = []
        minio_prefix = f"clients/{deal_id}/artifacts/{service_type}"

        if delivery_type == "page":
            # Multi-page: render all 4 web_design pages
            all_templates = [spec.template] + list(spec.extra_templates)
            for tmpl_rel, page_name in zip(all_templates, _PAGE_NAMES):
                tmpl_path = _TEMPLATES_DIR / tmpl_rel
                rendered_html = _render_jinja2(tmpl_path, content)

                # PNG 1440×900
                html_tmp = os.path.join(tmpdir, f"{page_name}.html")
                png_tmp = os.path.join(tmpdir, f"{page_name}.png")
                with open(html_tmp, "w", encoding="utf-8") as f:
                    f.write(rendered_html)

                await render_to_png(
                    html_path=html_tmp,
                    output_path=png_tmp,
                    viewport_width=1440,
                    viewport_height=900,
                )
                png_key = f"{minio_prefix}/{delivery_type}_v{version}_{page_name}_desktop.png"
                await upload_file(png_tmp, png_key)
                artifact_keys.append(png_key)

                # HTML file (rendered, not template)
                html_key = f"{minio_prefix}/{delivery_type}_v{version}_{page_name}.html"
                await upload_bytes(
                    rendered_html.encode("utf-8"),
                    html_key,
                    content_type="text/html",
                )
                artifact_keys.append(html_key)

            return artifact_keys

        # ── Single-template or LLM-generated HTML ─────────────────────────────
        if llm_mode == "html_document":
            # WeasyPrint from LLM-generated full HTML
            html_str: str = content if isinstance(content, str) else ""
            html_tmp = os.path.join(tmpdir, "document.html")
            pdf_tmp = os.path.join(tmpdir, "document.pdf")
            with open(html_tmp, "w", encoding="utf-8") as f:
                f.write(html_str)

            # Use render_pdf with the generated HTML as both template + empty context
            await render_pdf(
                template_path=html_tmp,
                context={},
                output_path=pdf_tmp,
                base_url=tmpdir,
            )
            pdf_key = f"{minio_prefix}/{delivery_type}_v{version}.pdf"
            await upload_file(pdf_tmp, pdf_key)
            artifact_keys.append(pdf_key)
            return artifact_keys

        # ── Template-based render ─────────────────────────────────────────────
        ctx: dict = content if isinstance(content, dict) else {}
        tmpl_path = _TEMPLATES_DIR / spec.template

        for out in spec.outputs:
            if out.kind == "pdf":
                # WeasyPrint PDF
                pdf_tmp = os.path.join(tmpdir, f"{delivery_type}.pdf")
                await render_pdf(
                    template_path=str(tmpl_path),
                    context=ctx,
                    output_path=pdf_tmp,
                    base_url=str(tmpl_path.parent),
                )
                pdf_key = f"{minio_prefix}/{delivery_type}_v{version}.pdf"
                await upload_file(pdf_tmp, pdf_key)
                artifact_keys.append(pdf_key)

            elif out.kind == "png":
                # Puppeteer PNG (desktop or custom size)
                rendered_html = _render_jinja2(tmpl_path, ctx)
                html_tmp = os.path.join(tmpdir, f"{delivery_type}.html")
                png_tmp = os.path.join(tmpdir, f"{delivery_type}_desktop.png")
                with open(html_tmp, "w", encoding="utf-8") as f:
                    f.write(rendered_html)
                await render_to_png(
                    html_path=html_tmp,
                    output_path=png_tmp,
                    viewport_width=out.width,
                    viewport_height=out.height,
                )
                png_key = f"{minio_prefix}/{delivery_type}_v{version}_desktop.png"
                await upload_file(png_tmp, png_key)
                artifact_keys.append(png_key)

            elif out.kind == "png_m":
                # Puppeteer PNG mobile
                rendered_html = _render_jinja2(tmpl_path, ctx)
                html_tmp = os.path.join(tmpdir, f"{delivery_type}_m.html")
                png_tmp = os.path.join(tmpdir, f"{delivery_type}_mobile.png")
                with open(html_tmp, "w", encoding="utf-8") as f:
                    f.write(rendered_html)
                await render_to_png(
                    html_path=html_tmp,
                    output_path=png_tmp,
                    viewport_width=out.width,
                    viewport_height=out.height,
                )
                png_key = f"{minio_prefix}/{delivery_type}_v{version}_mobile.png"
                await upload_file(png_tmp, png_key)
                artifact_keys.append(png_key)

            elif out.kind == "pdf_p":
                # Puppeteer PDF
                rendered_html = _render_jinja2(tmpl_path, ctx)
                html_tmp = os.path.join(tmpdir, f"{delivery_type}_print.html")
                pdf_tmp = os.path.join(tmpdir, f"{delivery_type}_print.pdf")
                with open(html_tmp, "w", encoding="utf-8") as f:
                    f.write(rendered_html)
                await render_to_pdf(
                    html_path=html_tmp,
                    output_path=pdf_tmp,
                    format="A4",
                    print_background=True,
                )
                pdf_key = f"{minio_prefix}/{delivery_type}_v{version}.pdf"
                await upload_file(pdf_tmp, pdf_key)
                artifact_keys.append(pdf_key)

        return artifact_keys

    # ── LLM content generation ────────────────────────────────────────────────

    async def _generate_content(
        self,
        *,
        task: AgentTask,
        lead: Any | None,
        deal: Any,
        delivery_type: str,
        service_type: str,
        mode: str,
        today: str,
    ) -> dict | str:
        """
        Generate template context dict (mode='template_context')
        or full HTML string (mode='html_document').
        Input: no PII — only public business context.
        """
        user_input = {
            "mode": mode,
            "delivery_type": delivery_type,
            "service_type": service_type,
            "business_name": getattr(lead, "business_name", "") if lead else "",
            "sector": getattr(lead, "sector", "") if lead else "",
            "sector_label": _sector_label(getattr(lead, "sector", "") if lead else ""),
            "gap_summary": getattr(lead, "gap_summary", "") if lead else "",
            "estimated_value_eur": int(getattr(lead, "estimated_value_eur", 0) or 0) if lead else 0,
            "today_date": today,
            "operator_name": _OPERATOR_NAME,
        }

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
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
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning(
                "doc_generator.llm_parse_error",
                task_id=str(task.id),
                delivery_type=delivery_type,
                mode=mode,
            )
            if mode == "html_document":
                return _fallback_html(delivery_type, user_input)
            return _fallback_context(delivery_type, user_input)

        if mode == "html_document":
            return parsed.get("html", _fallback_html(delivery_type, user_input))
        return parsed


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _render_jinja2(template_path: Path, context: dict) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_path.parent)),
        autoescape=False,
        undefined=jinja2.Undefined,
    )
    return env.get_template(template_path.name).render(**context)


def _ok_result(task: AgentTask, sd: Any, artifact_keys: list[str]) -> AgentResult:
    return AgentResult(
        task_id=task.id,
        success=True,
        output={
            "service_delivery_id": str(sd.id),
            "delivery_type": sd.type,
            "artifact_paths": artifact_keys,
            "skipped": True,
        },
        artifacts=artifact_keys,
        next_tasks=["delivery_tracker.review"],
    )


def _fallback_context(delivery_type: str, ctx: dict) -> dict:
    """Minimal context so Jinja2 doesn't raise KeyError."""
    business_name = ctx.get("business_name", "")
    operator_name = ctx.get("operator_name", _OPERATOR_NAME)
    today = ctx.get("today_date", datetime.utcnow().strftime("%d/%m/%Y"))
    return {
        "business_name": business_name,
        "operator_name": operator_name,
        "sector_label": ctx.get("sector_label", ""),
        "proposal_date": today,
        "analysis_date": today,
        "last_update": today,
        "timeline_weeks": 4,
        "phases": [],
        "outputs": [],
        "asis_steps": [],
        "tobe_steps": [],
        "impacts": [],
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
        "current_systems": [],
        "intervention_steps": [],
        "systems": [],
        "update_items": [],
        "monthly_phases": [],
        "plan_period": "",
        "next_review_date": "",
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "completed_count": 0,
        "kpis": [],
        "uptime_services": [],
        "planned_updates": [],
        "sla_response": 4,
        # web_design
        "brand_primary": "#0f172a",
        "brand_accent": "#0ea5e9",
        "brand_secondary": "#1e3a5f",
        "nav_links": [],
        "hero_headline": business_name,
        "hero_subtext": "",
        "hero_cta_label": "Scopri di più",
        "services_title": "Servizi",
        "services": [],
        "cta_headline": "",
        "cta_subtext": "",
        "cta_button_label": "Contattaci",
        "about_description": "",
        "hero_stats": [],
        "company_values": [],
        "team_members": [],
        "services_hero_subtitle": "",
        "process_steps": [],
        "contact_intro": "",
        "opening_hours": {},
        "business_city": "",
        "business_phone": "+39 XXX XXX XXXX",
        "business_email": "info@example.it",
        "footer_tagline": "",
        # presentation
        "presentation_title": business_name,
        "is_cover": True,
        "service_type_label": "",
        "highlight_word": "",
        "presentation_subtitle": "",
        "presentation_tags": [],
        "current_slide": 1,
        "total_slides": 1,
        "toc": [],
        "current_section_index": 1,
        "section_eyebrow": "",
        "section_title": "",
        "show_key_points": False,
        "key_points": [],
        "show_stats": False,
        "stats": [],
        "progress_percent": 0,
        "presentation_date": today,
    }


def _fallback_html(delivery_type: str, ctx: dict) -> str:
    """Minimal WeasyPrint-compatible HTML when LLM fails."""
    business_name = ctx.get("business_name", "")
    operator_name = ctx.get("operator_name", _OPERATOR_NAME)
    today = ctx.get("today_date", datetime.utcnow().strftime("%d/%m/%Y"))
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<style>
@page {{ size: A4; margin: 2cm; }}
body {{ font-family: Arial, sans-serif; font-size: 11pt; color: #1a1a2e; }}
h1 {{ color: #0ea5e9; font-size: 18pt; margin-bottom: 1cm; }}
.meta {{ color: #666; font-size: 9pt; margin-bottom: 2cm; }}
p {{ line-height: 1.6; margin-bottom: 0.5cm; }}
</style>
</head>
<body>
<h1>{delivery_type.replace("_", " ").title()} — {business_name}</h1>
<div class="meta">Data: {today} — Preparato da: {operator_name}</div>
<p>Documento in elaborazione. Il contenuto dettagliato sarà disponibile nella versione finale.</p>
</body>
</html>"""


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
