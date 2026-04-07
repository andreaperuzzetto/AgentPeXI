from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import anthropic
import jwt as pyjwt
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError
from tools.db_tools import (
    create_nps_record,
    create_task,
    get_client,
    get_deal,
    get_lead,
    get_task_by_idempotency_key,
    log_email,
    update_deal,
    update_lead,
)
from tools.gmail import send_email

# ── Config ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_EMAIL_TEMPLATES_DIR = _ROOT / "config" / "templates" / "email"
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")
_OPERATOR_EMAIL = os.environ.get("OPERATOR_EMAIL", "")
_BASE_URL = os.environ.get("BASE_URL", "http://localhost:3000")
_PORTAL_SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "")
_NPS_TOKEN_TTL_DAYS = 30

# Product name labels per service_type
_PRODUCT_NAMES: dict[str, str] = {
    "consulting": "Piano di Consulenza Operativa",
    "web_design": "Sito Web",
    "digital_maintenance": "Piano di Manutenzione Digitale",
}

# Upsell map: current_service_type → [next_service_type, ...]
_UPSELL_MAP: dict[str, list[str]] = {
    "consulting": ["web_design", "digital_maintenance"],
    "web_design": ["digital_maintenance", "consulting"],
    "digital_maintenance": ["consulting", "web_design"],
}

_VALID_ACTIONS = frozenset({"onboarding", "checkin", "nps", "upsell"})


class AccountManagerAgent(BaseAgent):
    """
    Manages post-sale client relationship: onboarding, check-in, NPS, upsell.
    Reads: clients, deals, nps_records. Writes: nps_records, tasks, leads (upsell).
    Loads contact info directly from DB — never expects PII in payload.
    """

    agent_name = "account_manager"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        deal_id_str = payload.get("deal_id")
        client_id_str = payload.get("client_id")
        action = payload.get("action")
        if not deal_id_str or not client_id_str or not action:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: deal_id, client_id, action",
            )
        if action not in _VALID_ACTIONS:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Unknown action: {action!r}. Valid: {sorted(_VALID_ACTIONS)}",
            )

        deal_id = UUID(str(deal_id_str))
        client_id = UUID(str(client_id_str))
        gmail_thread_id: str = payload.get("gmail_thread_id", "") or ""
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load deal + client ────────────────────────────────────────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        client = await get_client(client_id, db)
        if client is None:
            raise AgentToolError(code="tool_db_client_not_found", message=f"Client {client_id}")

        # PII loaded from DB (decrypted by ORM) — never logged
        contact_email: str = getattr(client, "contact_email", "") or ""
        contact_name: str = getattr(client, "contact_name", "") or ""
        if not contact_email:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="client.contact_email is empty — cannot send email",
            )

        service_type: str = deal.service_type
        product_name: str = _PRODUCT_NAMES.get(service_type, service_type.replace("_", " ").title())

        # Load lead for sector context (no PII needed — sector is safe)
        lead_id = getattr(deal, "lead_id", None)
        lead = await get_lead(lead_id, db) if lead_id else None
        sector: str = getattr(lead, "sector", "") if lead else ""
        sector_label: str = _sector_label(sector)
        business_name: str = getattr(lead, "business_name", "") if lead else ""

        # ── Dispatch ──────────────────────────────────────────────────────────
        if action == "onboarding":
            return await self._onboarding(
                task, deal, deal_id, client_id, service_type, product_name,
                contact_email, contact_name, business_name, sector_label,
                gmail_thread_id, dry_run, db,
            )
        elif action == "checkin":
            return await self._checkin(
                task, deal_id, client_id, service_type, product_name,
                contact_email, contact_name, business_name, sector_label,
                gmail_thread_id, dry_run, db,
            )
        elif action == "nps":
            return await self._nps(
                task, deal_id, client_id, service_type, product_name,
                contact_email, contact_name, business_name, sector_label,
                gmail_thread_id, dry_run, db,
            )
        else:  # upsell
            return await self._upsell(
                task, deal, deal_id, client_id, lead, service_type, product_name,
                contact_email, contact_name, business_name, sector_label,
                gmail_thread_id, dry_run, db,
            )

    # ── Action: onboarding ────────────────────────────────────────────────────

    async def _onboarding(
        self, task, deal, deal_id, client_id, service_type, product_name,
        contact_email, contact_name, business_name, sector_label,
        gmail_thread_id, dry_run, db,
    ) -> AgentResult:
        idem_key = f"{task.id}:onboarding:{deal_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db):
            return self._ok(task, deal_id, client_id, "onboarding", {"already_sent": True})

        docs_url = f"{_BASE_URL}/deals/{deal_id}/deliverables"
        template_body = _load_template("post_sale/onboarding")
        variables = {
            "contact_name": contact_name,
            "business_name": business_name,
            "product_name": product_name,
            "operator_name": _OPERATOR_NAME,
            "support_email": _OPERATOR_EMAIL,
            "docs_url": docs_url,
            "service_type": service_type,
            "sector_label": sector_label,
            "nps_url": "",
        }
        email = await self._personalize(task, "post_sale/onboarding", template_body, variables)

        if dry_run:
            return self._ok(task, deal_id, client_id, "onboarding", {"dry_run": True})

        result = await send_email(
            to=contact_email,
            subject=email["subject"],
            body=email["body"],
            thread_id=gmail_thread_id or None,
        )
        thread_id = result.get("thread_id", gmail_thread_id)

        # Deal active
        await update_deal(deal_id, {"status": "active"}, db)

        await log_email(
            agent="account_manager",
            direction="outbound",
            template_name="post_sale/onboarding",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=thread_id,
            subject=email["subject"],
            db=db,
            deal_id=deal_id,
            client_id=client_id,
        )
        await create_task(
            type="account_manager.onboarding",
            agent="account_manager",
            payload={"deal_id": str(deal_id)},
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "account_manager.onboarding_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            client_id=str(client_id),
            service_type=service_type,
        )
        return self._ok(task, deal_id, client_id, "onboarding", {
            "gmail_thread_id": thread_id,
            "deal_status": "active",
        })

    # ── Action: checkin ───────────────────────────────────────────────────────

    async def _checkin(
        self, task, deal_id, client_id, service_type, product_name,
        contact_email, contact_name, business_name, sector_label,
        gmail_thread_id, dry_run, db,
    ) -> AgentResult:
        idem_key = f"{task.id}:checkin:{deal_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db):
            return self._ok(task, deal_id, client_id, "checkin", {"already_sent": True})

        template_body = _load_template("post_sale/checkin")
        variables = {
            "contact_name": contact_name,
            "business_name": business_name,
            "product_name": product_name,
            "operator_name": _OPERATOR_NAME,
            "support_email": _OPERATOR_EMAIL,
            "service_type": service_type,
            "sector_label": sector_label,
            "docs_url": "",
            "nps_url": "",
        }
        email = await self._personalize(task, "post_sale/checkin", template_body, variables)

        if dry_run:
            return self._ok(task, deal_id, client_id, "checkin", {"dry_run": True})

        result = await send_email(
            to=contact_email,
            subject=email["subject"],
            body=email["body"],
            thread_id=gmail_thread_id or None,
        )
        thread_id = result.get("thread_id", gmail_thread_id)

        await log_email(
            agent="account_manager",
            direction="outbound",
            template_name="post_sale/checkin",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=thread_id,
            subject=email["subject"],
            db=db,
            deal_id=deal_id,
            client_id=client_id,
        )
        await create_task(
            type="account_manager.checkin",
            agent="account_manager",
            payload={"deal_id": str(deal_id)},
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "account_manager.checkin_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            client_id=str(client_id),
        )
        return self._ok(task, deal_id, client_id, "checkin", {"gmail_thread_id": thread_id})

    # ── Action: nps ───────────────────────────────────────────────────────────

    async def _nps(
        self, task, deal_id, client_id, service_type, product_name,
        contact_email, contact_name, business_name, sector_label,
        gmail_thread_id, dry_run, db,
    ) -> AgentResult:
        idem_key = f"{task.id}:nps:{deal_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db):
            return self._ok(task, deal_id, client_id, "nps", {"already_sent": True})

        if dry_run:
            return self._ok(task, deal_id, client_id, "nps", {"dry_run": True})

        # Create NPS record
        nps_record = await create_nps_record(
            client_id=client_id,
            deal_id=deal_id,
            trigger="30d",
            db=db,
        )

        # Generate NPS survey token (30-day TTL)
        nps_token = _generate_nps_token(nps_record.id, client_id, deal_id)
        nps_url = f"{_BASE_URL}/portal/nps/{nps_token}"

        template_body = _load_template("post_sale/nps_survey")
        variables = {
            "contact_name": contact_name,
            "business_name": business_name,
            "product_name": product_name,
            "operator_name": _OPERATOR_NAME,
            "support_email": _OPERATOR_EMAIL,
            "nps_url": nps_url,
            "service_type": service_type,
            "sector_label": sector_label,
            "docs_url": "",
        }
        email = await self._personalize(task, "post_sale/nps_survey", template_body, variables)

        result = await send_email(
            to=contact_email,
            subject=email["subject"],
            body=email["body"],
            thread_id=gmail_thread_id or None,
        )
        thread_id = result.get("thread_id", gmail_thread_id)

        await log_email(
            agent="account_manager",
            direction="outbound",
            template_name="post_sale/nps_survey",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=thread_id,
            subject=email["subject"],
            db=db,
            deal_id=deal_id,
            client_id=client_id,
        )
        await create_task(
            type="account_manager.nps",
            agent="account_manager",
            payload={"deal_id": str(deal_id), "nps_id": str(nps_record.id)},
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "account_manager.nps_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            client_id=str(client_id),
            nps_id=str(nps_record.id),
        )
        return self._ok(task, deal_id, client_id, "nps", {
            "nps_id": str(nps_record.id),
            "gmail_thread_id": thread_id,
        })

    # ── Action: upsell ────────────────────────────────────────────────────────

    async def _upsell(
        self, task, deal, deal_id, client_id, lead, service_type, product_name,
        contact_email, contact_name, business_name, sector_label,
        gmail_thread_id, dry_run, db,
    ) -> AgentResult:
        idem_key = f"{task.id}:upsell:{deal_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db):
            return self._ok(task, deal_id, client_id, "upsell", {"already_sent": True})

        # Determine primary upsell service type
        upsell_candidates = _UPSELL_MAP.get(service_type, [])
        if not upsell_candidates:
            raise AgentToolError(
                code="validation_invalid_service_type",
                message=f"No upsell candidates for service_type: {service_type}",
            )
        upsell_service_type = upsell_candidates[0]

        # Compute months since delivery (approximate from deal dates)
        delivered_at = getattr(deal, "updated_at", None) or datetime.utcnow()
        months_since = max(1, (datetime.utcnow() - delivered_at).days // 30)

        # LLM generates upsell email
        email = await self._generate_upsell_email(
            task=task,
            current_service_type=service_type,
            upsell_service_type=upsell_service_type,
            business_name=business_name,
            contact_name=contact_name,
            sector_label=sector_label,
            delivered_product_name=product_name,
            months_since_delivery=months_since,
        )

        if dry_run:
            return self._ok(task, deal_id, client_id, "upsell", {
                "dry_run": True,
                "upsell_service_type": upsell_service_type,
            })

        result = await send_email(
            to=contact_email,
            subject=email["subject"],
            body=email["body"],
            thread_id=gmail_thread_id or None,
        )
        thread_id = result.get("thread_id", gmail_thread_id)

        await log_email(
            agent="account_manager",
            direction="outbound",
            template_name=f"upsell_{upsell_service_type}",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=thread_id,
            subject=email["subject"],
            db=db,
            deal_id=deal_id,
            client_id=client_id,
        )

        # Flag upsell on lead record for pipeline tracking (lead.status → "discovered" again)
        if lead is not None:
            try:
                await update_lead(
                    lead.id,
                    {
                        "status": "discovered",
                        "suggested_service_type": upsell_service_type,
                        "source": "upsell",
                    },
                    db,
                )
            except Exception:
                # Non-critical: upsell tracking failure doesn't block the email
                self.log.warning(
                    "account_manager.upsell_lead_update_failed",
                    task_id=str(task.id),
                    deal_id=str(deal_id),
                )

        await create_task(
            type="account_manager.upsell",
            agent="account_manager",
            payload={
                "deal_id": str(deal_id),
                "upsell_service_type": upsell_service_type,
            },
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "account_manager.upsell_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            client_id=str(client_id),
            upsell_service_type=upsell_service_type,
        )
        return self._ok(task, deal_id, client_id, "upsell", {
            "upsell_service_type": upsell_service_type,
            "upsell_summary": email.get("upsell_summary", ""),
            "gmail_thread_id": thread_id,
        })

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def _personalize(
        self,
        task: AgentTask,
        template_name: str,
        template_body: str,
        variables: dict,
    ) -> dict:
        """LLM personalizes an email template. Returns {subject, body}."""
        user_input = {
            "mode": "personalize_email",
            "template_name": template_name,
            "template_body": template_body,
            "variables": variables,
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
        )
        text = _strip_fences(response.content[0].text)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning(
                "account_manager.llm_parse_error",
                task_id=str(task.id),
                template=template_name,
            )
            # Fallback: manual substitution
            body = template_body
            for k, v in variables.items():
                body = body.replace("{{" + k + "}}", str(v) if v else "")
            product_name = variables.get("product_name", "")
            return {
                "subject": f"Aggiornamento: {product_name}",
                "body": body,
            }

    async def _generate_upsell_email(
        self,
        *,
        task: AgentTask,
        current_service_type: str,
        upsell_service_type: str,
        business_name: str,
        contact_name: str,
        sector_label: str,
        delivered_product_name: str,
        months_since_delivery: int,
    ) -> dict:
        """LLM generates upsell email. Returns {subject, body, upsell_summary}."""
        user_input = {
            "mode": "generate_upsell",
            "current_service_type": current_service_type,
            "upsell_service_type": upsell_service_type,
            "business_name": business_name,
            "contact_name": contact_name,
            "sector_label": sector_label,
            "operator_name": _OPERATOR_NAME,
            "support_email": _OPERATOR_EMAIL,
            "delivered_product_name": delivered_product_name,
            "months_since_delivery": months_since_delivery,
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
        )
        text = _strip_fences(response.content[0].text)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning(
                "account_manager.llm_parse_error",
                task_id=str(task.id),
                mode="generate_upsell",
            )
            upsell_label = _PRODUCT_NAMES.get(upsell_service_type, upsell_service_type)
            return {
                "subject": f"Un nuovo servizio per {business_name}",
                "body": (
                    f"Gentile {contact_name or 'Cliente'},\n\n"
                    f"Siamo lieti di proporle il nostro servizio di {upsell_label}.\n\n"
                    f"Cordiali saluti,\n{_OPERATOR_NAME}"
                ),
                "upsell_summary": f"Proposta di {upsell_label} per {business_name}.",
            }

    # ── Result builder ────────────────────────────────────────────────────────

    def _ok(
        self,
        task: AgentTask,
        deal_id: UUID,
        client_id: UUID,
        action: str,
        extra: dict,
        next_tasks: list[str] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "deal_id": str(deal_id),
                "client_id": str(client_id),
                "action": action,
                **extra,
            },
            next_tasks=next_tasks or [],
        )


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _generate_nps_token(nps_id: UUID, client_id: UUID, deal_id: UUID) -> str:
    """Generate a signed JWT for the NPS survey link (30-day TTL)."""
    payload = {
        "nps_id": str(nps_id),
        "client_id": str(client_id),
        "deal_id": str(deal_id),
        "exp": datetime.utcnow() + timedelta(days=_NPS_TOKEN_TTL_DAYS),
        "iat": datetime.utcnow(),
        "type": "nps_survey",
    }
    return pyjwt.encode(payload, _PORTAL_SECRET_KEY, algorithm="HS256")


def _load_template(template_name: str) -> str:
    """Load email template body, stripping YAML frontmatter."""
    parts = template_name.split("/")
    path = _EMAIL_TEMPLATES_DIR.joinpath(*parts).with_suffix(".md")
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        chunks = raw.split("---", 2)
        return chunks[2].strip() if len(chunks) >= 3 else raw
    return raw


def _strip_fences(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


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
