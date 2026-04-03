from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.client import Client
from db.models.deal import Deal
from db.models.delivery_report import DeliveryReport
from db.models.email_log import EmailLog
from db.models.invoice import Invoice
from db.models.lead import Lead
from db.models.nps_record import NpsRecord
from db.models.proposal import Proposal
from db.models.service_delivery import ServiceDelivery
from db.models.task import Task
from db.models.ticket import Ticket


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LeadAlreadyExistsError(Exception):
    pass


class MaxProposalVersionsError(Exception):
    pass


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


async def get_lead(lead_id: UUID, db: AsyncSession) -> Lead | None:
    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_lead_by_place_id(google_place_id: str, db: AsyncSession) -> Lead | None:
    result = await db.execute(
        select(Lead).where(
            Lead.google_place_id == google_place_id, Lead.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def create_lead(data: dict, db: AsyncSession) -> Lead:
    existing = await get_lead_by_place_id(data["google_place_id"], db)
    if existing is not None:
        raise LeadAlreadyExistsError(
            f"Lead with google_place_id={data['google_place_id']} already exists"
        )
    lead = Lead(**data)
    db.add(lead)
    await db.flush()
    await db.refresh(lead)
    return lead


async def update_lead(lead_id: UUID, data: dict, db: AsyncSession) -> Lead:
    data["updated_at"] = datetime.utcnow()
    await db.execute(update(Lead).where(Lead.id == lead_id).values(**data))
    await db.flush()
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------


async def get_deal(deal_id: UUID, db: AsyncSession) -> Deal | None:
    result = await db.execute(
        select(Deal).where(Deal.id == deal_id, Deal.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def update_deal(deal_id: UUID, data: dict, db: AsyncSession) -> Deal:
    data["updated_at"] = datetime.utcnow()
    await db.execute(update(Deal).where(Deal.id == deal_id).values(**data))
    await db.flush()
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    return result.scalar_one()


async def create_deal(lead_id: UUID, service_type: str, db: AsyncSession) -> Deal:
    lead = await get_lead(lead_id, db)
    deal = Deal(
        lead_id=lead_id,
        service_type=service_type,
        sector=lead.sector if lead else "",
    )
    db.add(deal)
    await db.flush()
    await db.refresh(deal)
    return deal


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


async def get_client(client_id: UUID, db: AsyncSession) -> Client | None:
    result = await db.execute(
        select(Client).where(Client.id == client_id, Client.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def create_client(lead_id: UUID, deal_id: UUID, db: AsyncSession) -> Client:
    lead = await get_lead(lead_id, db)
    deal = await get_deal(deal_id, db)
    client = Client(
        lead_id=lead_id,
        business_name=lead.business_name if lead else "",
        vat_number=lead.vat_number if lead else None,
        address=lead.address if lead else None,
        city=lead.city if lead else None,
        region=lead.region if lead else None,
        country=lead.country if lead else "IT",
    )
    db.add(client)
    await db.flush()
    await db.refresh(client)

    await create_client_schema(client.id, db)

    service_type = deal.service_type if deal else "consulting"
    _init_client_workspace(client.id, service_type)

    return client


async def create_client_schema(client_id: UUID, db: AsyncSession) -> str:
    schema_name = f"client_{str(client_id).replace('-', '')}"
    await db.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    await db.execute(
        update(Client)
        .where(Client.id == client_id)
        .values(db_schema_name=schema_name, updated_at=datetime.utcnow())
    )
    await db.flush()
    return schema_name


def _init_client_workspace(client_id: UUID, service_type: str) -> Path:
    workspace = Path(os.environ["CLIENT_WORKSPACE_ROOT"]) / str(client_id)
    common_dirs = ["docs", "deliverables"]
    service_dirs: dict[str, list[str]] = {
        "consulting": ["reports", "workshops", "roadmaps"],
        "web_design": ["mockups", "assets", "pages"],
        "digital_maintenance": ["audits", "updates", "monitoring"],
    }
    for subdir in common_dirs + service_dirs.get(service_type, []):
        (workspace / subdir).mkdir(parents=True, exist_ok=True)
    return workspace


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


async def get_proposal(proposal_id: UUID, db: AsyncSession) -> Proposal | None:
    result = await db.execute(
        select(Proposal).where(
            Proposal.id == proposal_id, Proposal.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def get_latest_proposal(deal_id: UUID, db: AsyncSession) -> Proposal | None:
    result = await db.execute(
        select(Proposal)
        .where(Proposal.deal_id == deal_id, Proposal.deleted_at.is_(None))
        .order_by(Proposal.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_proposal(deal_id: UUID, data: dict, db: AsyncSession) -> Proposal:
    version_result = await db.execute(
        select(func.max(Proposal.version)).where(
            Proposal.deal_id == deal_id, Proposal.deleted_at.is_(None)
        )
    )
    current_max: int | None = version_result.scalar_one_or_none()
    next_version = (current_max or 0) + 1
    if next_version > 5:
        raise MaxProposalVersionsError(
            f"Maximum proposal versions (5) reached for deal_id={deal_id}"
        )
    proposal = Proposal(deal_id=deal_id, version=next_version, **data)
    db.add(proposal)
    await db.flush()
    await db.refresh(proposal)
    return proposal


async def update_proposal(proposal_id: UUID, data: dict, db: AsyncSession) -> Proposal:
    data["updated_at"] = datetime.utcnow()
    await db.execute(update(Proposal).where(Proposal.id == proposal_id).values(**data))
    await db.flush()
    result = await db.execute(select(Proposal).where(Proposal.id == proposal_id))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


async def create_task(
    type: str,
    agent: str,
    payload: dict,
    db: AsyncSession,
    deal_id: UUID | None = None,
    client_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> Task:
    task = Task(
        type=type,
        agent=agent,
        payload=payload,
        deal_id=deal_id,
        client_id=client_id,
        idempotency_key=idempotency_key,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


async def update_task(task_id: UUID, data: dict, db: AsyncSession) -> Task:
    data["updated_at"] = datetime.utcnow()
    await db.execute(update(Task).where(Task.id == task_id).values(**data))
    await db.flush()
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one()


async def get_task_by_idempotency_key(key: str, db: AsyncSession) -> Task | None:
    result = await db.execute(
        select(Task).where(Task.idempotency_key == key, Task.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Service Deliveries
# ---------------------------------------------------------------------------


async def create_service_delivery(
    deal_id: UUID, client_id: UUID, data: dict, db: AsyncSession
) -> ServiceDelivery:
    sd = ServiceDelivery(deal_id=deal_id, client_id=client_id, **data)
    db.add(sd)
    await db.flush()
    await db.refresh(sd)
    return sd


async def update_service_delivery(
    sd_id: UUID, data: dict, db: AsyncSession
) -> ServiceDelivery:
    data["updated_at"] = datetime.utcnow()
    await db.execute(
        update(ServiceDelivery).where(ServiceDelivery.id == sd_id).values(**data)
    )
    await db.flush()
    result = await db.execute(
        select(ServiceDelivery).where(ServiceDelivery.id == sd_id)
    )
    return result.scalar_one()


async def get_service_deliveries_for_deal(
    deal_id: UUID, db: AsyncSession
) -> list[ServiceDelivery]:
    result = await db.execute(
        select(ServiceDelivery).where(
            ServiceDelivery.deal_id == deal_id,
            ServiceDelivery.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def get_service_delivery(
    sd_id: UUID, db: AsyncSession
) -> ServiceDelivery | None:
    result = await db.execute(
        select(ServiceDelivery).where(
            ServiceDelivery.id == sd_id, ServiceDelivery.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Delivery Reports
# ---------------------------------------------------------------------------


async def create_delivery_report(
    service_delivery_id: UUID,
    client_id: UUID,
    approved: bool,
    completeness_pct: float,
    blocking_issues: list[dict],
    notes: list[dict],
    report_path: str,
    reviewer_agent: str,
    db: AsyncSession,
) -> DeliveryReport:
    report = DeliveryReport(
        service_delivery_id=service_delivery_id,
        client_id=client_id,
        approved=approved,
        completeness_pct=completeness_pct,
        blocking_issues=blocking_issues,
        notes=notes,
        report_path=report_path,
        reviewer_agent=reviewer_agent,
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)
    return report


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


async def create_invoice(
    deal_id: UUID, client_id: UUID, data: dict, db: AsyncSession
) -> Invoice:
    year = date.today().year
    count_result = await db.execute(
        select(func.count(Invoice.id)).where(
            func.extract("year", Invoice.created_at) == year,
            Invoice.deleted_at.is_(None),
        )
    )
    count: int = count_result.scalar_one() or 0
    invoice_number = f"{year}-{count + 1:03d}"

    invoice = Invoice(
        deal_id=deal_id,
        client_id=client_id,
        invoice_number=invoice_number,
        **data,
    )
    db.add(invoice)
    await db.flush()
    await db.refresh(invoice)
    return invoice


async def update_invoice(invoice_id: UUID, data: dict, db: AsyncSession) -> Invoice:
    data["updated_at"] = datetime.utcnow()
    await db.execute(update(Invoice).where(Invoice.id == invoice_id).values(**data))
    await db.flush()
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


async def create_ticket(client_id: UUID, data: dict, db: AsyncSession) -> Ticket:
    ticket = Ticket(client_id=client_id, **data)
    db.add(ticket)
    await db.flush()
    await db.refresh(ticket)
    return ticket


async def update_ticket(ticket_id: UUID, data: dict, db: AsyncSession) -> Ticket:
    data["updated_at"] = datetime.utcnow()
    await db.execute(update(Ticket).where(Ticket.id == ticket_id).values(**data))
    await db.flush()
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    return result.scalar_one()


async def get_ticket(ticket_id: UUID, db: AsyncSession) -> Ticket | None:
    result = await db.execute(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# NPS Records
# ---------------------------------------------------------------------------


async def create_nps_record(
    client_id: UUID, deal_id: UUID, trigger: str, db: AsyncSession
) -> NpsRecord:
    record = NpsRecord(
        client_id=client_id,
        deal_id=deal_id,
        trigger=trigger,
        sent_at=datetime.utcnow(),
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def update_nps_record(
    nps_id: UUID, score: int, comment: str, db: AsyncSession
) -> NpsRecord:
    await db.execute(
        update(NpsRecord)
        .where(NpsRecord.id == nps_id)
        .values(score=score, comment=comment, responded_at=datetime.utcnow(), updated_at=datetime.utcnow())
    )
    await db.flush()
    result = await db.execute(select(NpsRecord).where(NpsRecord.id == nps_id))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Email Log
# ---------------------------------------------------------------------------


async def log_email(
    agent: str,
    direction: str,
    template_name: str | None,
    gmail_message_id: str | None,
    gmail_thread_id: str | None,
    subject: str | None,
    db: AsyncSession,
    deal_id: UUID | None = None,
    client_id: UUID | None = None,
    task_id: UUID | None = None,
) -> EmailLog:
    entry = EmailLog(
        agent=agent,
        direction=direction,
        template_name=template_name,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
        subject=subject,
        deal_id=deal_id,
        client_id=client_id,
        task_id=task_id,
        sent_at=datetime.utcnow() if direction == "outbound" else None,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return entry


# ---------------------------------------------------------------------------
# Private — task lifecycle (uso esclusivo di BaseAgent.run())
# ---------------------------------------------------------------------------


async def _mark_task_running(task_id: UUID, db: AsyncSession) -> None:
    await db.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(status="running", started_at=datetime.utcnow(), updated_at=datetime.utcnow())
    )
    await db.flush()


async def _mark_task_blocked(
    task_id: UUID, blocked_reason: str, db: AsyncSession
) -> None:
    await db.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(
            status="blocked",
            blocked_reason=blocked_reason,
            updated_at=datetime.utcnow(),
        )
    )
    await db.flush()


async def _mark_task_failed(task_id: UUID, error: str, db: AsyncSession) -> None:
    await db.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(
            status="failed",
            error=error,
            completed_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    await db.flush()


async def _mark_task_completed(
    task_id: UUID, output: dict, db: AsyncSession
) -> None:
    await db.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(
            status="completed",
            output=output,
            completed_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    await db.flush()
