from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import anthropic
import jwt as pyjwt
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from tools.db_tools import (
    create_client,
    create_task,
    get_deal,
    get_latest_proposal,
    get_lead,
    get_task_by_idempotency_key,
    log_email,
    update_deal,
    update_proposal,
)
from tools.gmail import send_email

# ── Config ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_TEMPLATES_DIR = _ROOT / "config" / "templates" / "email"
_SECTORS: dict = __import__("yaml").safe_load(
    (_ROOT / "config" / "sectors.yaml").read_text()
)["sectors"]

_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")
_OPERATOR_EMAIL = os.environ.get("OPERATOR_EMAIL", "")
_PORTAL_SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "")
_BASE_URL = os.environ.get("BASE_URL", "http://localhost:3000")

# Portal token TTL
_PORTAL_TOKEN_TTL_HOURS = 72

# Negotiation limits (from service-types.md §6)
_MAX_AUTONOMOUS_ROUNDS = 2
_MAX_DISCOUNT_PCT = 15
_MAX_TIMELINE_EXTRA_WEEKS = 2

# Follow-up sequence template names
_FOLLOW_UP_TEMPLATES = {1: "follow_up_1", 2: "follow_up_2", 3: "follow_up_3"}
_MAX_FOLLOW_UPS = 3

# Injection detection patterns (from security.md)
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignora\s+(le\s+)?istruzioni",
        r"sei\s+ora\s+un\s+agente",
        r"nuovo\s+sistema\s+prompt",
        r"ignore\s+(all\s+)?previous",
        r"you\s+are\s+now",
        r"disregard\s+(all\s+)?",
        r"system\s*:\s*",
        r"assistant\s*:\s*",
    ]
]

_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()


class SalesAgent(BaseAgent):
    """
    Sends proposal emails, handles follow-ups and client negotiations.
    Reads: deals, proposals, leads. Writes: deals.status, email_log, tasks, clients (on approval).

    RULE: Never sends email without deal.proposal_human_approved = true (read from DB).
    """

    agent_name = "sales"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        deal_id_str = payload.get("deal_id")
        action = payload.get("action")
        if not deal_id_str or not action:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: deal_id, action",
            )
        if action not in ("send_proposal", "follow_up", "handle_response"):
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Unknown action: {action!r}",
            )

        deal_id = UUID(str(deal_id_str))
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load deal — ALWAYS from DB (gate flag must be fresh) ──────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        # ── GATE 1: must be approved before ANY email action ──────────────────
        if not deal.proposal_human_approved:
            raise GateNotApprovedError(
                "GATE 1 non approvato — impossibile inviare email al cliente"
            )

        # ── Dispatch ──────────────────────────────────────────────────────────
        if action == "send_proposal":
            return await self._send_proposal(task, payload, deal, deal_id, dry_run, db)
        elif action == "follow_up":
            return await self._send_follow_up(task, payload, deal, deal_id, dry_run, db)
        else:  # handle_response
            return await self._handle_response(task, payload, deal, deal_id, dry_run, db)

    # ── Action: send_proposal ─────────────────────────────────────────────────

    async def _send_proposal(
        self,
        task: AgentTask,
        payload: dict,
        deal: object,
        deal_id: UUID,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        lead_id_str = payload.get("lead_id")
        proposal_id_str = payload.get("proposal_id")
        contact_email: str = payload.get("contact_email", "")
        contact_name: str = payload.get("contact_name", "")

        if not lead_id_str or not proposal_id_str or not contact_email:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="send_proposal requires: lead_id, proposal_id, contact_email",
            )

        lead_id = UUID(str(lead_id_str))
        proposal_id = UUID(str(proposal_id_str))

        lead = await get_lead(lead_id, db)
        if lead is None:
            raise AgentToolError(code="tool_db_lead_not_found", message=f"Lead {lead_id}")

        proposal = await get_latest_proposal(deal_id, db)
        if proposal is None or str(proposal.id) != str(proposal_id):
            raise AgentToolError(
                code="tool_db_proposal_not_found",
                message=f"Proposal {proposal_id} for deal {deal_id}",
            )

        # ── Idempotency: already sent? ────────────────────────────────────────
        idem_key = f"{task.id}:send_proposal:{proposal_id}"
        if not dry_run:
            if getattr(proposal, "sent_at", None) is not None:
                self.log.info(
                    "sales.proposal_already_sent",
                    task_id=str(task.id),
                    deal_id=str(deal_id),
                )
                return self._ok_result(
                    task, deal_id, "send_proposal",
                    {"already_sent": True, "proposal_id": str(proposal_id)},
                    next_tasks=["sales.follow_up"],
                )
            if await get_task_by_idempotency_key(idem_key, db) is not None:
                return self._ok_result(
                    task, deal_id, "send_proposal",
                    {"already_sent": True, "proposal_id": str(proposal_id)},
                    next_tasks=["sales.follow_up"],
                )

        # ── Generate portal JWT ───────────────────────────────────────────────
        portal_token = _generate_portal_token(str(proposal_id), str(deal_id), gate="proposal")
        portal_url = f"{_BASE_URL}/portal/{portal_token}"
        portal_expires = datetime.utcnow() + timedelta(hours=_PORTAL_TOKEN_TTL_HOURS)

        # ── Personalize email body via LLM ────────────────────────────────────
        template_body = _load_template("proposal_send")
        sector_label = _sector_label(getattr(lead, "sector", ""))
        service_type = deal.service_type
        variables = {
            "business_name": getattr(lead, "business_name", ""),
            "contact_name": contact_name,
            "operator_name": _OPERATOR_NAME,
            "sector_label": sector_label,
            "portal_url": portal_url,
            "proposal_summary": "",  # LLM will generate it
            "timeline_weeks": getattr(proposal, "timeline_weeks", 4) or 4,
            "estimated_value_eur": int(getattr(lead, "estimated_value_eur", 0) or 0),
            "service_type": service_type,
        }
        email_content = await self._personalize_email(
            task, "proposal_send", template_body, variables
        )

        if dry_run:
            return self._ok_result(
                task, deal_id, "send_proposal",
                {"dry_run": True, "portal_url": portal_url, "proposal_id": str(proposal_id)},
                next_tasks=["sales.follow_up"],
            )

        # ── Send email — contact_email is PII, never logged ───────────────────
        send_result = await send_email(
            to=contact_email,
            subject=email_content["subject"],
            body=email_content["body"],
        )
        gmail_message_id: str = send_result.get("message_id", "")
        gmail_thread_id: str = send_result.get("thread_id", "")

        # ── Save portal token on proposal ─────────────────────────────────────
        await update_proposal(
            proposal_id,
            {
                "sent_at": datetime.utcnow(),
                "portal_link_token": portal_token,
                "portal_link_expires": portal_expires,
            },
            db,
        )

        # ── Update deal status ────────────────────────────────────────────────
        await update_deal(deal_id, {"status": "proposal_sent"}, db)

        # ── Log email (no PII — no address logged) ────────────────────────────
        await log_email(
            agent="sales",
            direction="outbound",
            template_name="proposal_send",
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            subject=email_content["subject"],
            db=db,
            deal_id=deal_id,
        )

        # ── Register idempotency key ──────────────────────────────────────────
        await create_task(
            type="sales.send_proposal",
            agent="sales",
            payload={"deal_id": str(deal_id), "proposal_id": str(proposal_id)},
            db=db,
            deal_id=deal_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "sales.proposal_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            proposal_id=str(proposal_id),
            gmail_message_id=gmail_message_id,
        )

        return self._ok_result(
            task, deal_id, "send_proposal",
            {
                "proposal_id": str(proposal_id),
                "portal_url": portal_url,
                "gmail_thread_id": gmail_thread_id,
            },
            next_tasks=["sales.follow_up"],
        )

    # ── Action: follow_up ─────────────────────────────────────────────────────

    async def _send_follow_up(
        self,
        task: AgentTask,
        payload: dict,
        deal: object,
        deal_id: UUID,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        follow_up_number: int = int(payload.get("follow_up_number", 1))
        contact_email: str = payload.get("contact_email", "")
        contact_name: str = payload.get("contact_name", "")
        gmail_thread_id: str = payload.get("gmail_thread_id", "")

        if not contact_email:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="follow_up requires: contact_email",
            )
        if follow_up_number not in _FOLLOW_UP_TEMPLATES:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"follow_up_number must be 1-3, got {follow_up_number}",
            )

        # Max follow-ups reached → deal lost
        if follow_up_number > _MAX_FOLLOW_UPS:
            self.log.warning(
                "sales.max_followups_reached",
                task_id=str(task.id),
                deal_id=str(deal_id),
            )
            if not dry_run:
                await update_deal(deal_id, {"status": "lost", "lost_reason": "no_response_after_3_followups"}, db)
            raise GateNotApprovedError("agent_sales_client_lost")

        # ── Idempotency ───────────────────────────────────────────────────────
        idem_key = f"{task.id}:follow_up:{deal_id}:{follow_up_number}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok_result(
                task, deal_id, f"follow_up_{follow_up_number}",
                {"already_sent": True, "follow_up_number": follow_up_number},
            )

        # ── Load lead for context ─────────────────────────────────────────────
        lead_id_str = payload.get("lead_id")
        business_name = ""
        sector = ""
        if lead_id_str:
            lead = await get_lead(UUID(str(lead_id_str)), db)
            if lead:
                business_name = getattr(lead, "business_name", "")
                sector = getattr(lead, "sector", "")

        # ── Load portal URL from proposal ─────────────────────────────────────
        proposal = await get_latest_proposal(deal_id, db)
        portal_url = ""
        if proposal and getattr(proposal, "portal_link_token", None):
            portal_url = f"{_BASE_URL}/portal/{proposal.portal_link_token}"

        # ── Personalize email ─────────────────────────────────────────────────
        template_name = _FOLLOW_UP_TEMPLATES[follow_up_number]
        template_body = _load_template(template_name)
        variables = {
            "business_name": business_name,
            "contact_name": contact_name,
            "operator_name": _OPERATOR_NAME,
            "sector_label": _sector_label(sector),
            "portal_url": portal_url,
        }
        email_content = await self._personalize_email(task, template_name, template_body, variables)

        if dry_run:
            return self._ok_result(
                task, deal_id, f"follow_up_{follow_up_number}",
                {"dry_run": True, "follow_up_number": follow_up_number},
            )

        # ── Send email — no PII logged ────────────────────────────────────────
        send_result = await send_email(
            to=contact_email,
            subject=email_content["subject"],
            body=email_content["body"],
            thread_id=gmail_thread_id if gmail_thread_id else None,
        )
        new_thread_id: str = send_result.get("thread_id", gmail_thread_id)

        await log_email(
            agent="sales",
            direction="outbound",
            template_name=template_name,
            gmail_message_id=send_result.get("message_id", ""),
            gmail_thread_id=new_thread_id,
            subject=email_content["subject"],
            db=db,
            deal_id=deal_id,
        )
        await create_task(
            type=f"sales.follow_up_{follow_up_number}",
            agent="sales",
            payload={"deal_id": str(deal_id), "follow_up_number": follow_up_number},
            db=db,
            deal_id=deal_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "sales.follow_up_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            follow_up_number=follow_up_number,
        )

        return self._ok_result(
            task, deal_id, f"follow_up_{follow_up_number}",
            {"follow_up_number": follow_up_number, "gmail_thread_id": new_thread_id},
        )

    # ── Action: handle_response ───────────────────────────────────────────────

    async def _handle_response(
        self,
        task: AgentTask,
        payload: dict,
        deal: object,
        deal_id: UUID,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        client_response: str = payload.get("client_response", "")
        client_notes: str = payload.get("client_notes", "")
        contact_email: str = payload.get("contact_email", "")
        contact_name: str = payload.get("contact_name", "")
        gmail_thread_id: str = payload.get("gmail_thread_id", "")
        lead_id_str = payload.get("lead_id")

        if not client_response:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="handle_response requires: client_response",
            )

        # ── Injection check on untrusted client content ───────────────────────
        if _contains_injection(client_notes):
            self.log.critical(
                "task.error.security",
                task_id=str(task.id),
                deal_id=str(deal_id),
                agent="sales",
                error_code="security_injection_attempt",
                source="client_notes",
            )
            raise GateNotApprovedError("security_injection_attempt")

        # ── Handle: client approved ───────────────────────────────────────────
        if client_response == "approved":
            if dry_run:
                return self._ok_result(
                    task, deal_id, "client_approved",
                    {"dry_run": True, "client_response": "approved"},
                    next_tasks=["delivery_orchestrator.plan"],
                )

            # Create client record
            lead_id = UUID(str(lead_id_str)) if lead_id_str else None
            if lead_id is None:
                raise AgentToolError(
                    code="validation_missing_payload_field",
                    message="handle_response approved requires: lead_id",
                )
            client = await create_client(lead_id, deal_id, db)
            await update_deal(deal_id, {"status": "client_approved"}, db)

            self.log.info(
                "sales.client_approved",
                task_id=str(task.id),
                deal_id=str(deal_id),
                client_id=str(client.id),
            )
            return self._ok_result(
                task, deal_id, "client_approved",
                {"client_response": "approved", "client_id": str(client.id)},
                next_tasks=["delivery_orchestrator.plan"],
            )

        # ── Handle: client rejected ───────────────────────────────────────────
        if client_response == "rejected":
            if not dry_run:
                await update_deal(
                    deal_id,
                    {"status": "lost", "lost_reason": client_notes[:500] if client_notes else None},
                    db,
                )
            self.log.info(
                "sales.deal_lost",
                task_id=str(task.id),
                deal_id=str(deal_id),
            )
            return self._ok_result(
                task, deal_id, "client_rejected",
                {"client_response": "rejected"},
            )

        # ── Handle: negotiating ───────────────────────────────────────────────
        if client_response == "negotiating":
            negotiation_round: int = int(payload.get("negotiation_round", 1))

            if negotiation_round > _MAX_AUTONOMOUS_ROUNDS:
                self.log.warning(
                    "sales.max_negotiation_rounds",
                    task_id=str(task.id),
                    deal_id=str(deal_id),
                    rounds=negotiation_round,
                )
                raise GateNotApprovedError("agent_sales_max_negotiation_rounds")

            if not contact_email:
                raise AgentToolError(
                    code="validation_missing_payload_field",
                    message="negotiating response requires: contact_email",
                )

            # Load context for negotiation
            lead = None
            if lead_id_str:
                lead = await get_lead(UUID(str(lead_id_str)), db)
            proposal = await get_latest_proposal(deal_id, db)
            portal_url = ""
            if proposal and getattr(proposal, "portal_link_token", None):
                portal_url = f"{_BASE_URL}/portal/{proposal.portal_link_token}"

            current_price_eur = int(getattr(proposal, "pricing_json", {}).get("total_eur", 0) if proposal and getattr(proposal, "pricing_json", None) else 0)
            service_type = deal.service_type

            # ── Generate negotiation response via LLM ─────────────────────────
            neg_result = await self._generate_negotiation_response(
                task=task,
                service_type=service_type,
                sector_label=_sector_label(getattr(lead, "sector", "") if lead else ""),
                client_notes=client_notes,
                current_price_eur=current_price_eur,
                negotiation_round=negotiation_round,
                contact_name=contact_name,
                portal_url=portal_url,
            )

            if not neg_result.get("is_within_autonomous_bounds", True):
                # Escalate to operator
                if not dry_run:
                    await update_deal(deal_id, {"status": "negotiating"}, db)
                raise GateNotApprovedError("agent_sales_max_negotiation_rounds")

            if dry_run:
                return self._ok_result(
                    task, deal_id, "negotiation_response",
                    {"dry_run": True, "negotiation_round": negotiation_round},
                )

            # ── Send negotiation response ──────────────────────────────────────
            send_result = await send_email(
                to=contact_email,
                subject=neg_result["subject"],
                body=neg_result["body"],
                thread_id=gmail_thread_id if gmail_thread_id else None,
            )
            new_thread_id = send_result.get("thread_id", gmail_thread_id)

            await update_deal(deal_id, {"status": "negotiating"}, db)
            await log_email(
                agent="sales",
                direction="outbound",
                template_name="negotiation_response",
                gmail_message_id=send_result.get("message_id", ""),
                gmail_thread_id=new_thread_id,
                subject=neg_result["subject"],
                db=db,
                deal_id=deal_id,
            )

            idem_key = f"{task.id}:negotiation:{deal_id}:{negotiation_round}"
            await create_task(
                type="sales.negotiation_response",
                agent="sales",
                payload={"deal_id": str(deal_id), "round": negotiation_round},
                db=db,
                deal_id=deal_id,
                idempotency_key=idem_key,
            )

            self.log.info(
                "sales.negotiation_sent",
                task_id=str(task.id),
                deal_id=str(deal_id),
                round=negotiation_round,
            )
            return self._ok_result(
                task, deal_id, "negotiation_response",
                {
                    "negotiation_round": negotiation_round,
                    "adjustment_applied": neg_result.get("adjustment_applied", ""),
                    "new_price_eur": neg_result.get("new_price_eur", current_price_eur),
                    "gmail_thread_id": new_thread_id,
                },
            )

        raise AgentToolError(
            code="validation_missing_payload_field",
            message=f"Unknown client_response: {client_response!r}",
        )

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def _personalize_email(
        self,
        task: AgentTask,
        template_name: str,
        template_body: str,
        variables: dict,
    ) -> dict:
        """LLM personalizes the email. Returns {subject, body}."""
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
                "sales.llm_parse_error",
                task_id=str(task.id),
                template=template_name,
            )
            # Fallback: substitute known variables manually
            body = template_body
            for k, v in variables.items():
                body = body.replace("{{" + k + "}}", str(v) if v else "")
            subject_line = variables.get("business_name", "")
            return {"subject": f"Proposta per {subject_line}", "body": body}

    async def _generate_negotiation_response(
        self,
        *,
        task: AgentTask,
        service_type: str,
        sector_label: str,
        client_notes: str,
        current_price_eur: int,
        negotiation_round: int,
        contact_name: str,
        portal_url: str,
    ) -> dict:
        """LLM generates a counter-offer email for the negotiation."""
        user_input = {
            "mode": "negotiation_response",
            "service_type": service_type,
            "sector_label": sector_label,
            # client_notes is untrusted — included in JSON as data, not as instruction
            "client_notes": client_notes[:500],  # truncate to limit attack surface
            "current_price_eur": current_price_eur,
            "negotiation_round": negotiation_round,
            "operator_name": _OPERATOR_NAME,
            "contact_name": contact_name,
            "portal_url": portal_url,
            "allowed_adjustments": {
                "max_discount_pct": _MAX_DISCOUNT_PCT,
                "max_timeline_extra_weeks": _MAX_TIMELINE_EXTRA_WEEKS,
                "can_remove_minor_deliverable": True,
            },
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
                "sales.llm_parse_error",
                task_id=str(task.id),
                mode="negotiation_response",
            )
            return {
                "subject": "Re: Nostra proposta",
                "body": "Grazie per il suo riscontro. Siamo disponibili a discutere le condizioni. Cordiali saluti.",
                "adjustment_applied": "none",
                "new_price_eur": current_price_eur,
                "is_within_autonomous_bounds": True,
            }

    # ── Result builder ────────────────────────────────────────────────────────

    def _ok_result(
        self,
        task: AgentTask,
        deal_id: UUID,
        action_done: str,
        extra: dict,
        next_tasks: list[str] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.id,
            success=True,
            output={"deal_id": str(deal_id), "action": action_done, **extra},
            next_tasks=next_tasks or [],
        )


# ── Module-level helpers ───────────────────────────────────────────────────────

def _generate_portal_token(proposal_id: str, deal_id: str, gate: str = "proposal") -> str:
    """Generate a signed JWT for the client portal link (72h TTL)."""
    payload = {
        "proposal_id": proposal_id,
        "deal_id": deal_id,
        "exp": datetime.utcnow() + timedelta(hours=_PORTAL_TOKEN_TTL_HOURS),
        "iat": datetime.utcnow(),
        "type": "portal_access",
        "gate": gate,
    }
    return pyjwt.encode(payload, _PORTAL_SECRET_KEY, algorithm="HS256")


def _load_template(template_name: str) -> str:
    """Load email template body (strips YAML frontmatter)."""
    path = _TEMPLATES_DIR / f"{template_name}.md"
    raw = path.read_text(encoding="utf-8")
    # Strip frontmatter (--- ... ---)
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else raw
    return raw


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def _contains_injection(text: str) -> bool:
    """Detect prompt injection attempts in untrusted client content."""
    if not text:
        return False
    return any(p.search(text) for p in _INJECTION_PATTERNS)


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
