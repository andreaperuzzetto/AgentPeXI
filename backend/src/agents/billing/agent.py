from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError
from tools.db_tools import (
    create_invoice,
    create_task,
    get_client,
    get_deal,
    get_lead,
    get_task_by_idempotency_key,
    log_email,
    update_invoice,
)
from tools.gmail import send_email

# ── Config ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[4]
_EMAIL_TEMPLATES_DIR = _ROOT / "config" / "templates" / "email"
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

_OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "Operatore")
_OPERATOR_EMAIL = os.environ.get("OPERATOR_EMAIL", "")

# IVA standard italiana
_DEFAULT_TAX_RATE_PCT = 22.00

# Trailing invoice is issued this many days after delivery
_TRAILING_DAYS = 30

# Escalate overdue invoices older than this many days past due_date
_ESCALATE_OVERDUE_DAYS = 15

# Billing split defaults (overridden by deal.deposit_pct)
_DEFAULT_DEPOSIT_PCT = 30
_TRAILING_PCT = 10  # always 10 %

_VALID_ACTIONS = frozenset({"create_invoice", "send_invoice", "send_reminder"})
_VALID_MILESTONES = frozenset({"deposit", "delivery", "trailing", "monthly"})
_VALID_REMINDER_TYPES = frozenset({"gentle", "due", "overdue"})

# Human-readable milestone labels
_MILESTONE_LABELS: dict[str, str] = {
    "deposit": "Acconto kickoff (30%)",
    "delivery": "Saldo consegna (60%)",
    "trailing": "Rata finale (10%)",
    "monthly": "Canone mensile",
}

# Service-type-specific delivery gate field names
_DELIVERY_GATE_AT: dict[str, str] = {
    "consulting": "consulting_approved_at",
    "web_design": "delivery_approved_at",
    "digital_maintenance": "delivery_approved_at",
}


class BillingAgent(BaseAgent):
    """
    Creates invoices, sends them, and issues payment reminders.
    Reads: deals, invoices, clients. Writes: invoices, tasks.

    Billing split: deposit=deal.deposit_pct (default 30%), trailing=10%, delivery=remainder.
    All amounts in cents (EUR). IVA 22% applied on top.
    """

    agent_name = "billing"

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

        service_type: str = deal.service_type
        total_price_cents: int = int(getattr(deal, "total_price_eur", 0) or 0)
        deposit_pct: int = int(getattr(deal, "deposit_pct", _DEFAULT_DEPOSIT_PCT) or _DEFAULT_DEPOSIT_PCT)
        payment_terms_days: int = int(getattr(deal, "payment_terms_days", 30) or 30)

        # Load lead for sector context
        lead_id = getattr(deal, "lead_id", None)
        lead = await get_lead(lead_id, db) if lead_id else None
        business_name: str = getattr(lead, "business_name", "") if lead else ""

        # ── Dispatch ──────────────────────────────────────────────────────────
        if action == "create_invoice":
            return await self._create_invoice(
                task=task,
                payload=payload,
                deal=deal,
                deal_id=deal_id,
                client_id=client_id,
                service_type=service_type,
                total_price_cents=total_price_cents,
                deposit_pct=deposit_pct,
                payment_terms_days=payment_terms_days,
                dry_run=dry_run,
                db=db,
            )
        elif action == "send_invoice":
            if not contact_email:
                raise AgentToolError(
                    code="validation_missing_payload_field",
                    message="client.contact_email is empty",
                )
            return await self._send_invoice(
                task=task,
                payload=payload,
                deal_id=deal_id,
                client_id=client_id,
                service_type=service_type,
                business_name=business_name,
                contact_email=contact_email,
                contact_name=contact_name,
                dry_run=dry_run,
                db=db,
            )
        else:  # send_reminder
            if not contact_email:
                raise AgentToolError(
                    code="validation_missing_payload_field",
                    message="client.contact_email is empty",
                )
            return await self._send_reminder(
                task=task,
                payload=payload,
                deal_id=deal_id,
                client_id=client_id,
                service_type=service_type,
                business_name=business_name,
                contact_email=contact_email,
                contact_name=contact_name,
                dry_run=dry_run,
                db=db,
            )

    # ── Action: create_invoice ────────────────────────────────────────────────

    async def _create_invoice(
        self,
        *,
        task: AgentTask,
        payload: dict,
        deal: object,
        deal_id: UUID,
        client_id: UUID,
        service_type: str,
        total_price_cents: int,
        deposit_pct: int,
        payment_terms_days: int,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        milestone: str = payload.get("milestone", "")
        if not milestone or milestone not in _VALID_MILESTONES:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Required: milestone in {sorted(_VALID_MILESTONES)}",
            )

        # ── Idempotency ───────────────────────────────────────────────────────
        idem_key = f"{task.id}:create_invoice:{deal_id}:{milestone}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, deal_id, client_id, "create_invoice", {
                "already_created": True,
                "milestone": milestone,
            })

        # ── Calculate amounts ─────────────────────────────────────────────────
        amount_cents = _milestone_amount(milestone, total_price_cents, deposit_pct)
        if amount_cents <= 0:
            raise AgentToolError(
                code="validation_invoice_amount_mismatch",
                message=f"Calculated amount ≤ 0 for milestone={milestone}, total={total_price_cents}",
            )

        # ── Calculate due date ────────────────────────────────────────────────
        due = _milestone_due_date(milestone, deal, payment_terms_days)

        if dry_run:
            return self._ok(task, deal_id, client_id, "create_invoice", {
                "dry_run": True,
                "milestone": milestone,
                "amount_cents": amount_cents,
                "due_date": due.isoformat(),
            })

        invoice = await create_invoice(
            deal_id=deal_id,
            client_id=client_id,
            data={
                "milestone": milestone,
                "amount_cents": amount_cents,
                "due_date": due,
                "tax_rate_pct": _DEFAULT_TAX_RATE_PCT,
            },
            db=db,
        )

        await create_task(
            type="billing.create_invoice",
            agent="billing",
            payload={
                "deal_id": str(deal_id),
                "milestone": milestone,
                "invoice_id": str(invoice.id),
            },
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "billing.invoice_created",
            task_id=str(task.id),
            deal_id=str(deal_id),
            invoice_id=str(invoice.id),
            milestone=milestone,
            amount_cents=amount_cents,
        )

        return self._ok(task, deal_id, client_id, "create_invoice", {
            "invoice_id": str(invoice.id),
            "invoice_number": getattr(invoice, "invoice_number", ""),
            "milestone": milestone,
            "amount_cents": amount_cents,
            "total_cents": getattr(invoice, "total_cents", None),
            "due_date": due.isoformat(),
            "status": "draft",
            "escalate": False,
        })

    # ── Action: send_invoice ──────────────────────────────────────────────────

    async def _send_invoice(
        self,
        *,
        task: AgentTask,
        payload: dict,
        deal_id: UUID,
        client_id: UUID,
        service_type: str,
        business_name: str,
        contact_email: str,
        contact_name: str,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        invoice_id_str = payload.get("invoice_id")
        if not invoice_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="send_invoice requires: invoice_id",
            )
        invoice_id = UUID(str(invoice_id_str))

        # Invoice data from payload (set by Orchestrator after create_invoice)
        invoice_number: str = payload.get("invoice_number", "")
        amount_cents: int = int(payload.get("amount_cents", 0))
        total_cents: int = int(payload.get("total_cents", 0)) or amount_cents
        due_date_str: str = payload.get("due_date", "")
        milestone: str = payload.get("milestone", "")

        idem_key = f"{task.id}:send_invoice:{invoice_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, deal_id, client_id, "send_invoice", {
                "already_sent": True,
                "invoice_id": str(invoice_id),
            })

        amount_eur = f"{amount_cents / 100:.2f}".replace(".", ",")
        total_eur = f"{total_cents / 100:.2f}".replace(".", ",")
        milestone_label = _MILESTONE_LABELS.get(milestone, milestone)
        due_fmt = _fmt_date(due_date_str)

        subject = f"Fattura {invoice_number} — {milestone_label} — {business_name}"
        body = _build_invoice_email(
            contact_name=contact_name,
            business_name=business_name,
            invoice_number=invoice_number,
            milestone_label=milestone_label,
            amount_eur=amount_eur,
            total_eur=total_eur,
            due_date=due_fmt,
            operator_name=_OPERATOR_NAME,
            operator_email=_OPERATOR_EMAIL,
        )

        if dry_run:
            return self._ok(task, deal_id, client_id, "send_invoice", {
                "dry_run": True,
                "invoice_id": str(invoice_id),
            })

        result = await send_email(to=contact_email, subject=subject, body=body)

        await update_invoice(invoice_id, {"status": "sent"}, db)
        await log_email(
            agent="billing",
            direction="outbound",
            template_name="billing/invoice",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=result.get("thread_id", ""),
            subject=subject,
            db=db,
            deal_id=deal_id,
            client_id=client_id,
        )
        await create_task(
            type="billing.send_invoice",
            agent="billing",
            payload={"invoice_id": str(invoice_id), "deal_id": str(deal_id)},
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "billing.invoice_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            invoice_id=str(invoice_id),
            invoice_number=invoice_number,
        )
        return self._ok(task, deal_id, client_id, "send_invoice", {
            "invoice_id": str(invoice_id),
            "invoice_number": invoice_number,
            "status": "sent",
            "gmail_thread_id": result.get("thread_id", ""),
            "escalate": False,
        })

    # ── Action: send_reminder ─────────────────────────────────────────────────

    async def _send_reminder(
        self,
        *,
        task: AgentTask,
        payload: dict,
        deal_id: UUID,
        client_id: UUID,
        service_type: str,
        business_name: str,
        contact_email: str,
        contact_name: str,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        invoice_id_str = payload.get("invoice_id")
        reminder_type: str = payload.get("reminder_type", "gentle")
        if not invoice_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="send_reminder requires: invoice_id",
            )
        if reminder_type not in _VALID_REMINDER_TYPES:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"reminder_type must be one of {sorted(_VALID_REMINDER_TYPES)}",
            )

        invoice_id = UUID(str(invoice_id_str))
        invoice_number: str = payload.get("invoice_number", "")
        amount_cents: int = int(payload.get("amount_cents", 0))
        total_cents: int = int(payload.get("total_cents", 0)) or amount_cents
        due_date_str: str = payload.get("due_date", "")
        milestone: str = payload.get("milestone", "")
        reminder_count: int = int(payload.get("reminder_count", 0))

        # Calculate days delta (for subject/body context)
        days_delta = _days_delta(due_date_str, reminder_type)

        # ── Overdue escalation check ──────────────────────────────────────────
        escalate = False
        if reminder_type == "overdue" and days_delta >= _ESCALATE_OVERDUE_DAYS:
            escalate = True
            self.log.warning(
                "billing.payment_overdue",
                task_id=str(task.id),
                deal_id=str(deal_id),
                invoice_id=str(invoice_id),
                invoice_number=invoice_number,
                days_overdue=days_delta,
            )

        idem_key = f"{task.id}:send_reminder:{invoice_id}:{reminder_type}:{reminder_count}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return self._ok(task, deal_id, client_id, "send_reminder", {
                "already_sent": True,
                "invoice_id": str(invoice_id),
                "escalate": escalate,
            })

        amount_eur = f"{amount_cents / 100:.2f}".replace(".", ",")
        milestone_label = _MILESTONE_LABELS.get(milestone, milestone)
        due_fmt = _fmt_date(due_date_str)

        # ── LLM personalizes the reminder ─────────────────────────────────────
        template_body = _load_template("billing/payment_reminder")
        variables = {
            "contact_name": contact_name,
            "business_name": business_name,
            "operator_name": _OPERATOR_NAME,
            "amount_eur": amount_eur,
            "due_date": due_fmt,
            "invoice_number": invoice_number,
            "milestone_label": milestone_label,
            "reminder_type": reminder_type,
            "days_delta": days_delta,
            "service_type": service_type,
        }
        email = await self._personalize_reminder(task, template_body, variables)

        if dry_run:
            return self._ok(task, deal_id, client_id, "send_reminder", {
                "dry_run": True,
                "invoice_id": str(invoice_id),
                "reminder_type": reminder_type,
                "escalate": escalate,
            })

        result = await send_email(to=contact_email, subject=email["subject"], body=email["body"])

        new_reminder_count = reminder_count + 1
        await update_invoice(
            invoice_id,
            {
                "reminder_count": new_reminder_count,
                "last_reminder_at": datetime.utcnow(),
                **({"status": "overdue"} if reminder_type == "overdue" else {}),
            },
            db,
        )
        await log_email(
            agent="billing",
            direction="outbound",
            template_name=f"billing/payment_reminder_{reminder_type}",
            gmail_message_id=result.get("message_id", ""),
            gmail_thread_id=result.get("thread_id", ""),
            subject=email["subject"],
            db=db,
            deal_id=deal_id,
            client_id=client_id,
        )
        await create_task(
            type=f"billing.send_reminder_{reminder_type}",
            agent="billing",
            payload={
                "invoice_id": str(invoice_id),
                "deal_id": str(deal_id),
                "reminder_type": reminder_type,
                "reminder_count": new_reminder_count,
            },
            db=db,
            deal_id=deal_id,
            client_id=client_id,
            idempotency_key=idem_key,
        )

        self.log.info(
            "billing.reminder_sent",
            task_id=str(task.id),
            deal_id=str(deal_id),
            invoice_id=str(invoice_id),
            invoice_number=invoice_number,
            reminder_type=reminder_type,
            reminder_count=new_reminder_count,
            escalate=escalate,
        )
        return self._ok(task, deal_id, client_id, "send_reminder", {
            "invoice_id": str(invoice_id),
            "invoice_number": invoice_number,
            "reminder_type": reminder_type,
            "reminder_count": new_reminder_count,
            "escalate": escalate,
            "gmail_thread_id": result.get("thread_id", ""),
        })

    # ── LLM helper ────────────────────────────────────────────────────────────

    async def _personalize_reminder(
        self,
        task: AgentTask,
        template_body: str,
        variables: dict,
    ) -> dict:
        """LLM personalizes the payment reminder email. Returns {subject, body}."""
        user_input = {
            "template_body": template_body,
            "variables": variables,
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=768,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
        )
        text = _strip_fences(response.content[0].text)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            self.log.warning(
                "billing.llm_parse_error",
                task_id=str(task.id),
                reminder_type=variables.get("reminder_type"),
            )
            # Fallback: manual substitution
            body = template_body
            for k, v in variables.items():
                body = body.replace("{{" + k + "}}", str(v) if v else "")
            invoice_number = variables.get("invoice_number", "")
            return {
                "subject": f"Promemoria pagamento — Fattura {invoice_number}",
                "body": body,
            }

    # ── Result builder ────────────────────────────────────────────────────────

    def _ok(
        self,
        task: AgentTask,
        deal_id: UUID,
        client_id: UUID,
        action: str,
        extra: dict,
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
        )


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _milestone_amount(
    milestone: str,
    total_cents: int,
    deposit_pct: int,
) -> int:
    """
    Calculate the invoice amount in cents for the given milestone.
    Split: deposit=deposit_pct%, trailing=10%, delivery=remainder.
    The Billing Agent always reads deposit_pct from deal, not from pricing.yaml defaults.
    """
    if total_cents <= 0:
        return 0
    if milestone == "deposit":
        return total_cents * deposit_pct // 100
    if milestone == "trailing":
        return total_cents * _TRAILING_PCT // 100
    if milestone == "delivery":
        delivery_pct = 100 - deposit_pct - _TRAILING_PCT
        delivered = total_cents * delivery_pct // 100
        # Ensure deposit + delivery + trailing == total (avoid rounding gap)
        deposit = total_cents * deposit_pct // 100
        trailing = total_cents * _TRAILING_PCT // 100
        return total_cents - deposit - trailing
    if milestone == "monthly":
        # For monthly billing, total_cents IS the monthly amount
        return total_cents
    return 0


def _milestone_due_date(
    milestone: str,
    deal: object,
    payment_terms_days: int,
) -> date:
    """Calculate the invoice due date for the given milestone."""
    today = date.today()

    def _to_date(ts: object | None) -> date:
        if ts is None:
            return today
        if isinstance(ts, datetime):
            return ts.date()
        if isinstance(ts, date):
            return ts
        return today

    if milestone == "deposit":
        base = _to_date(getattr(deal, "kickoff_confirmed_at", None))
        return base + timedelta(days=payment_terms_days)

    if milestone == "delivery":
        service_type: str = getattr(deal, "service_type", "")
        gate_field = _DELIVERY_GATE_AT.get(service_type, "delivery_approved_at")
        base = _to_date(getattr(deal, gate_field, None))
        return base + timedelta(days=payment_terms_days)

    if milestone == "trailing":
        service_type = getattr(deal, "service_type", "")
        gate_field = _DELIVERY_GATE_AT.get(service_type, "delivery_approved_at")
        base = _to_date(getattr(deal, gate_field, None))
        return base + timedelta(days=_TRAILING_DAYS)

    if milestone == "monthly":
        # Due on the 1st of next month
        if today.month == 12:
            return date(today.year + 1, 1, 1)
        return date(today.year, today.month + 1, 1)

    return today + timedelta(days=payment_terms_days)


def _days_delta(due_date_str: str, reminder_type: str) -> int:
    """Return positive days until due (gentle/due) or days past due (overdue)."""
    if not due_date_str:
        return 0
    try:
        due = date.fromisoformat(due_date_str)
    except ValueError:
        return 0
    delta = (date.today() - due).days  # positive = past due
    if reminder_type == "gentle":
        return abs(delta)  # days until due
    return max(0, delta)  # days overdue


def _fmt_date(date_str: str) -> str:
    """Format ISO date to Italian locale (dd/mm/yyyy)."""
    if not date_str:
        return ""
    try:
        d = date.fromisoformat(date_str)
        return d.strftime("%d/%m/%Y")
    except ValueError:
        return date_str


def _build_invoice_email(
    *,
    contact_name: str,
    business_name: str,
    invoice_number: str,
    milestone_label: str,
    amount_eur: str,
    total_eur: str,
    due_date: str,
    operator_name: str,
    operator_email: str,
) -> str:
    """Build a professional invoice notification email body (no LLM needed)."""
    greeting = contact_name if contact_name else "Gentilissimo/a"
    return (
        f"Gentile {greeting},\n\n"
        f"Le inviamo la fattura n. {invoice_number} relativa a: {milestone_label}.\n\n"
        f"Importo (imponibile): €{amount_eur}\n"
        f"Totale con IVA 22%: €{total_eur}\n"
        f"Scadenza: {due_date}\n\n"
        f"Per effettuare il pagamento o per qualsiasi chiarimento, "
        f"risponda a questa email o scriva a {operator_email}.\n\n"
        f"Cordiali saluti,\n{operator_name}"
    )


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
