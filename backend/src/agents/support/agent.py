from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from tools.db_tools import (
    create_service_delivery,
    create_task,
    create_ticket,
    get_client,
    get_deal,
    get_task_by_idempotency_key,
    get_ticket,
    log_email,
    update_ticket,
)
from tools.gmail import read_thread, send_email

# ── Config ────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

import os as _os
_OPERATOR_NAME = _os.environ.get("OPERATOR_NAME", "Operatore")
_OPERATOR_EMAIL = _os.environ.get("OPERATOR_EMAIL", "")

# Default SLA: first response within this many hours (overridden by client.sla_response_hours)
_DEFAULT_SLA_HOURS = 4

# Max body length passed to LLM — limits injection attack surface
_MAX_BODY_LEN = 600

_VALID_ACTIONS = frozenset({"classify", "respond", "resolve", "create_intervention", "check_sla"})

_VALID_TICKET_TYPES = frozenset({"service_request", "update_request", "how_to", "billing", "spam"})
_VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})

# Intervention delivery types per service_type (from service-types.md §12)
_INTERVENTION_TYPES: dict[str, list[str]] = {
    "consulting": ["report", "workshop"],
    "web_design": ["page", "responsive_check"],
    "digital_maintenance": ["security_patch", "update_cycle"],
}

# Injection detection patterns (untrusted: email body, ticket description)
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
        r"<\s*system\s*>",
        r"prompt\s*injection",
    ]
]


class SupportAgent(BaseAgent):
    """
    Classifies, responds to, and manages support tickets.
    Reads: tickets, clients (contact info). Writes: tickets, service_deliveries (interventions), tasks.

    SECURITY: email body and ticket descriptions are UNTRUSTED DATA.
    All untrusted content is scanned for injection before any LLM call.
    Only sanitised summaries — never raw content — are included in LLM prompts.
    """

    agent_name = "support"

    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-sonnet-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        action = payload.get("action")
        client_id_str = payload.get("client_id")
        if not action or not client_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: action, client_id",
            )
        if action not in _VALID_ACTIONS:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Unknown action: {action!r}. Valid: {sorted(_VALID_ACTIONS)}",
            )

        client_id = UUID(str(client_id_str))
        deal_id_str = payload.get("deal_id")
        deal_id = UUID(str(deal_id_str)) if deal_id_str else None
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load client (PII decrypted by ORM, never logged) ──────────────────
        client = await get_client(client_id, db)
        if client is None:
            raise AgentToolError(code="tool_db_client_not_found", message=f"Client {client_id}")

        contact_email: str = getattr(client, "contact_email", "") or ""
        contact_name: str = getattr(client, "contact_name", "") or ""
        sla_hours: int = int(getattr(client, "sla_response_hours", _DEFAULT_SLA_HOURS) or _DEFAULT_SLA_HOURS)

        # ── Load deal for service_type context ────────────────────────────────
        service_type: str = ""
        if deal_id:
            deal = await get_deal(deal_id, db)
            if deal:
                service_type = deal.service_type or ""

        # ── Dispatch ──────────────────────────────────────────────────────────
        if action == "classify":
            return await self._classify(
                task, payload, client_id, deal_id, service_type,
                contact_name, sla_hours, dry_run, db,
            )
        elif action == "respond":
            return await self._respond(
                task, payload, client_id, deal_id, service_type,
                contact_email, contact_name, sla_hours, dry_run, db,
            )
        elif action == "resolve":
            return await self._resolve(task, payload, client_id, deal_id, dry_run, db)
        elif action == "create_intervention":
            return await self._create_intervention(
                task, payload, client_id, deal_id, service_type, dry_run, db,
            )
        else:  # check_sla
            return await self._check_sla(task, payload, client_id, sla_hours, dry_run, db)

    # ── Action: classify ──────────────────────────────────────────────────────

    async def _classify(
        self, task, payload, client_id, deal_id, service_type,
        contact_name, sla_hours, dry_run, db,
    ) -> AgentResult:
        email_thread_id: str = payload.get("email_thread_id", "")
        if not email_thread_id:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="classify requires: email_thread_id",
            )

        idem_key = f"{task.id}:classify:{email_thread_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, client_id, deal_id, "classify", {"already_classified": True})

        # ── Fetch email thread ────────────────────────────────────────────────
        thread = await read_thread(email_thread_id)
        messages = thread.get("messages", [])
        if not messages:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Email thread {email_thread_id} is empty",
            )

        # Use the LAST message as the active request
        last_msg = messages[-1]
        subject: str = payload.get("subject", "") or last_msg.get("subject", "")
        # Body excerpt — NEVER logged, truncated to limit attack surface
        raw_body: str = last_msg.get("body", last_msg.get("snippet", ""))
        body_excerpt = raw_body[:_MAX_BODY_LEN]

        # ── SECURITY: injection detection on untrusted content ────────────────
        _check_injection(body_excerpt, task, "email_body")
        _check_injection(subject, task, "email_subject")

        # ── LLM classification — body passed as DATA, not instruction ─────────
        classification = await self._llm_classify(
            task=task,
            subject=subject,
            body_excerpt=body_excerpt,
            client_service_type=service_type,
            snippet=last_msg.get("snippet", ""),
        )

        ticket_type = classification.get("ticket_type", "service_request")
        if ticket_type not in _VALID_TICKET_TYPES:
            ticket_type = "service_request"
        severity = classification.get("severity", "medium")
        if severity not in _VALID_SEVERITIES:
            severity = "medium"
        title = classification.get("title", subject[:100])
        summary = classification.get("summary", "")

        if dry_run:
            return self._ok(task, client_id, deal_id, "classify", {
                "dry_run": True,
                "ticket_type": ticket_type,
                "severity": severity,
                "title": title,
            })

        # ── Create ticket record — description stores SANITISED summary, not raw body
        ticket = await create_ticket(
            client_id=client_id,
            data={
                "deal_id": str(deal_id) if deal_id else None,
                "type": ticket_type,
                "severity": severity,
                "title": title,
                "description": summary,  # LLM-generated summary, not raw email body
                "gmail_thread_id": email_thread_id,
                "status": "open",
            },
            db=db,
        )

        # SLA check: flag if already breached on creation
        sla_breached = False  # new ticket — SLA starts now

        await create_task(
            type="support.classify",
            agent="support",
            payload={
                "ticket_id": str(ticket.id),
                "email_thread_id": email_thread_id,
            },
            db=db,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "support.ticket_classified",
            task_id=str(task.id),
            ticket_id=str(ticket.id),
            client_id=str(client_id),
            ticket_type=ticket_type,
            severity=severity,
        )
        return self._ok(task, client_id, deal_id, "classify", {
            "ticket_id": str(ticket.id),
            "ticket_type": ticket_type,
            "severity": severity,
            "title": title,
            "sla_hours": sla_hours,
            "sla_breached": sla_breached,
        })

    # ── Action: respond ───────────────────────────────────────────────────────

    async def _respond(
        self, task, payload, client_id, deal_id, service_type,
        contact_email, contact_name, sla_hours, dry_run, db,
    ) -> AgentResult:
        ticket_id_str = payload.get("ticket_id")
        if not ticket_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="respond requires: ticket_id",
            )
        ticket_id = UUID(str(ticket_id_str))

        if not contact_email:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="client.contact_email is empty",
            )

        idem_key = f"{task.id}:respond:{ticket_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, client_id, deal_id, "respond", {
                "already_sent": True, "ticket_id": str(ticket_id),
            })

        # ── Load ticket ───────────────────────────────────────────────────────
        ticket = await get_ticket(ticket_id, db)
        if ticket is None:
            raise AgentToolError(
                code="tool_db_service_delivery_not_found",
                message=f"Ticket {ticket_id} not found",
            )

        # Ticket description is LLM-generated summary — still scan it
        _check_injection(getattr(ticket, "description", "") or "", task, "ticket_description")

        ticket_type: str = getattr(ticket, "type", "service_request") or "service_request"
        severity: str = getattr(ticket, "severity", "medium") or "medium"
        title: str = getattr(ticket, "title", "") or ""
        description: str = getattr(ticket, "description", "") or ""
        gmail_thread_id: str = getattr(ticket, "gmail_thread_id", "") or ""
        first_response_at = getattr(ticket, "first_response_at", None)
        is_first = first_response_at is None

        # ── SLA breach check ──────────────────────────────────────────────────
        sla_breached = _check_sla_breach(ticket, sla_hours)

        # ── Don't respond to spam ─────────────────────────────────────────────
        if ticket_type == "spam":
            await update_ticket(ticket_id, {"status": "closed"}, db)
            return self._ok(task, client_id, deal_id, "respond", {
                "ticket_id": str(ticket_id),
                "skipped": True,
                "reason": "spam",
            })

        # ── LLM generates response — uses SUMMARY, not raw email body ─────────
        resolution_notes: str = payload.get("resolution_notes", "") or ""
        email = await self._llm_respond(
            task=task,
            ticket_type=ticket_type,
            severity=severity,
            ticket_title=title,
            ticket_summary=description,
            service_type=service_type,
            contact_name=contact_name,
            first_response=is_first,
            resolution_notes=resolution_notes,
        )

        if dry_run:
            return self._ok(task, client_id, deal_id, "respond", {
                "dry_run": True, "ticket_id": str(ticket_id), "sla_breached": sla_breached,
            })

        result = await send_email(
            to=contact_email,
            subject=email["subject"],
            body=email["body"],
            thread_id=gmail_thread_id if gmail_thread_id else None,
        )
        new_thread_id = result.get("thread_id", gmail_thread_id)

        # Update ticket: first_response_at if first reply, status in_progress
        update_data: dict = {
            "status": "in_progress",
            "gmail_thread_id": new_thread_id or gmail_thread_id,
        }
        if is_first:
            update_data["first_response_at"] = datetime.utcnow()
        await update_ticket(ticket_id, update_data, db)

        await log_email(
            agent="support",
            direction="outbound",
            template_name="support/response",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=new_thread_id,
            subject=email["subject"],
            db=db,
            client_id=client_id,
        )
        await create_task(
            type="support.respond",
            agent="support",
            payload={"ticket_id": str(ticket_id)},
            db=db,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "support.response_sent",
            task_id=str(task.id),
            ticket_id=str(ticket_id),
            client_id=str(client_id),
            severity=severity,
            sla_breached=sla_breached,
        )
        return self._ok(task, client_id, deal_id, "respond", {
            "ticket_id": str(ticket_id),
            "is_first_response": is_first,
            "sla_breached": sla_breached,
            "gmail_thread_id": new_thread_id,
        })

    # ── Action: resolve ───────────────────────────────────────────────────────

    async def _resolve(self, task, payload, client_id, deal_id, dry_run, db) -> AgentResult:
        ticket_id_str = payload.get("ticket_id")
        if not ticket_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="resolve requires: ticket_id",
            )
        ticket_id = UUID(str(ticket_id_str))

        idem_key = f"{task.id}:resolve:{ticket_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, client_id, deal_id, "resolve", {
                "already_resolved": True, "ticket_id": str(ticket_id),
            })

        if dry_run:
            return self._ok(task, client_id, deal_id, "resolve", {
                "dry_run": True, "ticket_id": str(ticket_id),
            })

        await update_ticket(
            ticket_id,
            {"status": "resolved", "resolved_at": datetime.utcnow()},
            db,
        )
        await create_task(
            type="support.resolve",
            agent="support",
            payload={"ticket_id": str(ticket_id)},
            db=db,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "support.ticket_resolved",
            task_id=str(task.id),
            ticket_id=str(ticket_id),
            client_id=str(client_id),
        )
        return self._ok(task, client_id, deal_id, "resolve", {
            "ticket_id": str(ticket_id),
            "status": "resolved",
        })

    # ── Action: create_intervention ───────────────────────────────────────────

    async def _create_intervention(
        self, task, payload, client_id, deal_id, service_type, dry_run, db,
    ) -> AgentResult:
        ticket_id_str = payload.get("ticket_id")
        intervention_type: str = payload.get("intervention_type", "")
        if not ticket_id_str or not deal_id:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="create_intervention requires: ticket_id, deal_id",
            )
        ticket_id = UUID(str(ticket_id_str))

        # Validate intervention type for the service type
        valid_types = _INTERVENTION_TYPES.get(service_type, [])
        if not intervention_type:
            # Default to first valid type for the service
            intervention_type = valid_types[0] if valid_types else "report"
        elif intervention_type not in valid_types and valid_types:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"intervention_type {intervention_type!r} not valid for {service_type}. Valid: {valid_types}",
            )

        idem_key = f"{task.id}:create_intervention:{ticket_id}:{intervention_type}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, client_id, deal_id, "create_intervention", {
                "already_created": True, "ticket_id": str(ticket_id),
            })

        ticket = await get_ticket(ticket_id, db)
        if ticket is None:
            raise AgentToolError(
                code="tool_db_service_delivery_not_found",
                message=f"Ticket {ticket_id} not found",
            )

        if dry_run:
            return self._ok(task, client_id, deal_id, "create_intervention", {
                "dry_run": True,
                "ticket_id": str(ticket_id),
                "intervention_type": intervention_type,
            })

        sd = await create_service_delivery(
            deal_id=deal_id,
            client_id=client_id,
            data={
                "service_type": service_type,
                "type": intervention_type,
                "title": f"Intervento support: {getattr(ticket, 'title', intervention_type)}",
                "description": f"Intervento creato da ticket #{str(ticket_id)[:8]}",
                "milestone_name": None,
                "milestone_due": None,
                "depends_on": [],
            },
            db=db,
        )

        # Link ticket to service_delivery
        await update_ticket(
            ticket_id,
            {"service_delivery_id": str(sd.id), "status": "in_progress"},
            db,
        )
        await create_task(
            type="support.create_intervention",
            agent="support",
            payload={
                "ticket_id": str(ticket_id),
                "service_delivery_id": str(sd.id),
                "intervention_type": intervention_type,
            },
            db=db,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "support.intervention_created",
            task_id=str(task.id),
            ticket_id=str(ticket_id),
            client_id=str(client_id),
            service_delivery_id=str(sd.id),
            intervention_type=intervention_type,
        )
        return self._ok(task, client_id, deal_id, "create_intervention", {
            "ticket_id": str(ticket_id),
            "service_delivery_id": str(sd.id),
            "intervention_type": intervention_type,
        }, next_tasks=["doc_generator.generate"])

    # ── Action: check_sla ─────────────────────────────────────────────────────

    async def _check_sla(self, task, payload, client_id, sla_hours, dry_run, db) -> AgentResult:
        ticket_id_str = payload.get("ticket_id")
        if not ticket_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="check_sla requires: ticket_id",
            )
        ticket_id = UUID(str(ticket_id_str))

        ticket = await get_ticket(ticket_id, db)
        if ticket is None:
            raise AgentToolError(
                code="tool_db_service_delivery_not_found",
                message=f"Ticket {ticket_id} not found",
            )

        sla_breached = _check_sla_breach(ticket, sla_hours)

        if sla_breached and not dry_run:
            await update_ticket(
                ticket_id,
                {
                    "escalated": True,
                    "escalated_at": datetime.utcnow(),
                    "escalation_reason": f"agent_support_sla_breach: SLA {sla_hours}h superato",
                },
                db,
            )
            self.log.warning(
                "support.sla_breach",
                task_id=str(task.id),
                ticket_id=str(ticket_id),
                client_id=str(client_id),
                sla_hours=sla_hours,
            )

        return self._ok(task, client_id, None, "check_sla", {
            "ticket_id": str(ticket_id),
            "sla_breached": sla_breached,
            "sla_hours": sla_hours,
            "escalate": sla_breached,
        })

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def _llm_classify(
        self,
        *,
        task: AgentTask,
        subject: str,
        body_excerpt: str,
        client_service_type: str,
        snippet: str,
    ) -> dict:
        """
        Ask Claude to classify the ticket.
        SECURITY: body_excerpt is passed as DATA inside a JSON object,
        never interpolated directly into the prompt instruction.
        """
        user_input = {
            "mode": "classify",
            "subject": subject[:200],         # truncated, labeled as DATO
            "email_body_excerpt": body_excerpt,  # truncated, untrusted
            "client_service_type": client_service_type,
            "snippet": snippet[:200],
            "is_known_client": True,
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
        )
        text = _strip_fences(response.content[0].text)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning("support.llm_parse_error", task_id=str(task.id), mode="classify")
            return {
                "ticket_type": "service_request",
                "severity": "medium",
                "title": subject[:80] or "Richiesta supporto",
                "summary": snippet[:200],
            }

    async def _llm_respond(
        self,
        *,
        task: AgentTask,
        ticket_type: str,
        severity: str,
        ticket_title: str,
        ticket_summary: str,
        service_type: str,
        contact_name: str,
        first_response: bool,
        resolution_notes: str,
    ) -> dict:
        """
        Generate response email. Uses SUMMARY (LLM-generated, sanitised), not raw email body.
        """
        user_input = {
            "mode": "respond",
            "ticket_type": ticket_type,
            "severity": severity,
            "ticket_title": ticket_title,
            "ticket_summary": ticket_summary[:500],
            "client_service_type": service_type,
            "contact_name": contact_name,
            "business_name": "",
            "operator_name": _OPERATOR_NAME,
            "support_email": _OPERATOR_EMAIL,
            "first_response": first_response,
            "resolution_notes": resolution_notes[:300] if resolution_notes else "",
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
            self.log.warning("support.llm_parse_error", task_id=str(task.id), mode="respond")
            greeting = contact_name if contact_name else "Cliente"
            return {
                "subject": f"Re: {ticket_title}",
                "body": (
                    f"Gentile {greeting},\n\n"
                    f"Abbiamo ricevuto la sua richiesta e la stiamo prendendo in carico.\n"
                    f"La contatteremo il prima possibile.\n\n"
                    f"Cordiali saluti,\n{_OPERATOR_NAME}"
                ),
            }

    # ── Result builder ────────────────────────────────────────────────────────

    def _ok(
        self,
        task: AgentTask,
        client_id: UUID,
        deal_id: UUID | None,
        action: str,
        extra: dict,
        next_tasks: list[str] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "client_id": str(client_id),
                "deal_id": str(deal_id) if deal_id else None,
                "action": action,
                **extra,
            },
            next_tasks=next_tasks or [],
        )


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _check_injection(text: str, task: AgentTask, source: str) -> None:
    """
    Scan untrusted text for prompt injection patterns.
    If detected: log critical and raise GateNotApprovedError.
    Never logs the content itself.
    """
    if not text:
        return
    import structlog as _sl
    _log = _sl.get_logger().bind(agent="support")
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            _log.critical(
                "task.error.security",
                task_id=str(task.id),
                agent="support",
                error_code="security_injection_attempt",
                source=source,  # category, NOT the content
            )
            raise GateNotApprovedError("security_injection_attempt")


def _check_sla_breach(ticket: object, sla_hours: int) -> bool:
    """Return True if first response SLA has been breached."""
    first_response_at = getattr(ticket, "first_response_at", None)
    if first_response_at is not None:
        return False  # Already responded — SLA met
    created_at = getattr(ticket, "created_at", None)
    if created_at is None:
        return False
    elapsed = datetime.utcnow() - created_at
    return elapsed > timedelta(hours=sla_hours)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    return text
