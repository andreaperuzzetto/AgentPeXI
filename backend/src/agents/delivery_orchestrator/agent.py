from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import BaseAgent
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from tools.db_tools import (
    create_service_delivery,
    create_task,
    get_deal,
    get_latest_proposal,
    get_lead,
    get_service_deliveries_for_deal,
    get_task_by_idempotency_key,
    update_deal,
    update_service_delivery,
)

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "system.md").read_text()

# ── Standard decompositions per service_type ──────────────────────────────────
# Each entry: type, title, milestone_name (nullable), depends_on (list of ref strings)
# Ref strings: "{type}" or "{type}_{1-based-index}" for same-type ordering

_DECOMPOSITION: dict[str, list[dict]] = {
    "consulting": [
        {"type": "report",         "title": "Report diagnostico iniziale",      "milestone_name": None,                  "depends_on": []},
        {"type": "workshop",       "title": "Workshop operativo #1",             "milestone_name": None,                  "depends_on": ["report"]},
        {"type": "workshop",       "title": "Workshop operativo #2",             "milestone_name": None,                  "depends_on": ["workshop_1"]},
        {"type": "process_schema", "title": "Schema processi AS-IS/TO-BE",       "milestone_name": None,                  "depends_on": ["workshop_2"]},
        {"type": "roadmap",        "title": "Roadmap operativa finale",           "milestone_name": None,                  "depends_on": ["process_schema"]},
        {"type": "presentation",   "title": "Presentazione risultati",            "milestone_name": "consulting_approved", "depends_on": ["roadmap"]},
    ],
    "web_design": [
        {"type": "wireframe",        "title": "Wireframe struttura sito",         "milestone_name": "struttura_approvata", "depends_on": []},
        {"type": "mockup",           "title": "Mockup homepage",                  "milestone_name": None,                  "depends_on": ["wireframe"]},
        {"type": "branding",         "title": "Elementi branding",                "milestone_name": None,                  "depends_on": []},
        {"type": "page",             "title": "Sviluppo pagine",                  "milestone_name": None,                  "depends_on": ["mockup", "branding"]},
        {"type": "responsive_check", "title": "Verifica responsive",              "milestone_name": "mockup_finale",       "depends_on": ["page"]},
    ],
    "digital_maintenance": [
        {"type": "performance_audit", "title": "Audit performance e sicurezza",       "milestone_name": None,          "depends_on": []},
        {"type": "update_cycle",      "title": "Piano aggiornamenti e bonifica",      "milestone_name": None,          "depends_on": ["performance_audit"]},
        {"type": "security_patch",    "title": "Applicazione patch sicurezza",        "milestone_name": "primo_ciclo", "depends_on": ["update_cycle"]},
        {"type": "monitoring_setup",  "title": "Setup monitoraggio continuativo",     "milestone_name": "primo_ciclo", "depends_on": ["security_patch"]},
    ],
}

# Statuses considered "done" for dependency/completion checks
_DONE_STATUSES: frozenset[str] = frozenset({"completed", "approved"})


class DeliveryOrchestratorAgent(BaseAgent):
    """
    Plans and coordinates service delivery.
    Reads: deals, service_deliveries. Writes: service_deliveries, tasks, deals.status.

    Actions:
      plan           — GATE 2 check, create service_deliveries, dispatch first tasks
      check_progress — find ready deliverables, dispatch them; mark deal delivered when all done
    """

    agent_name = "delivery_orchestrator"

    def __init__(self) -> None:
        super().__init__()
        # claude-opus-4-6 per spec (Delivery Orchestrator uses Opus)
        self._client = anthropic.AsyncAnthropic()
        self._model = "claude-opus-4-6"

    # ── execute ───────────────────────────────────────────────────────────────

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload

        # ── Payload validation ────────────────────────────────────────────────
        deal_id_str = payload.get("deal_id")
        client_id_str = payload.get("client_id")
        action = payload.get("action", "plan")
        if not deal_id_str or not client_id_str:
            raise AgentToolError(
                code="validation_missing_payload_field",
                message="Required: deal_id, client_id",
            )
        if action not in ("plan", "check_progress"):
            raise AgentToolError(
                code="validation_missing_payload_field",
                message=f"Unknown action: {action!r}",
            )

        deal_id = UUID(str(deal_id_str))
        client_id = UUID(str(client_id_str))
        dry_run: bool = bool(payload.get("dry_run", False))

        # ── Load deal — ALWAYS from DB (gate flag must be fresh) ──────────────
        deal = await get_deal(deal_id, db)
        if deal is None:
            raise AgentToolError(code="tool_db_deal_not_found", message=f"Deal {deal_id}")

        # ── GATE 2: kickoff must be confirmed before starting delivery ─────────
        if not deal.kickoff_confirmed:
            raise GateNotApprovedError(
                "GATE 2 non confermato — impossibile avviare l'erogazione"
            )

        service_type: str = deal.service_type
        if service_type not in _DECOMPOSITION:
            raise AgentToolError(
                code="validation_invalid_service_type",
                message=f"service_type non riconosciuto: {service_type}",
            )

        if action == "plan":
            return await self._plan(task, payload, deal, deal_id, client_id, service_type, dry_run, db)
        else:
            return await self._check_progress(task, deal, deal_id, client_id, service_type, dry_run, db)

    # ── Action: plan ──────────────────────────────────────────────────────────

    async def _plan(
        self,
        task: AgentTask,
        payload: dict,
        deal: object,
        deal_id: UUID,
        client_id: UUID,
        service_type: str,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        """
        Create service_delivery records and dispatch first ready deliverables.
        Idempotent: if records already exist, falls through to check_progress.
        """
        # ── Idempotency: skip creation if deliveries already exist ────────────
        existing = await get_service_deliveries_for_deal(deal_id, db)
        if existing:
            self.log.info(
                "delivery_orchestrator.plan_already_done",
                task_id=str(task.id),
                deal_id=str(deal_id),
                existing_count=len(existing),
            )
            return await self._check_progress(task, deal, deal_id, client_id, service_type, dry_run, db)

        # ── Idempotency key for plan creation ─────────────────────────────────
        idem_key = f"{task.id}:plan:{deal_id}"
        if not dry_run and await get_task_by_idempotency_key(idem_key, db) is not None:
            return await self._check_progress(task, deal, deal_id, client_id, service_type, dry_run, db)

        # ── Load proposal for timeline_weeks ──────────────────────────────────
        proposal = await get_latest_proposal(deal_id, db)
        timeline_weeks: int = (
            getattr(proposal, "timeline_weeks", None) or _default_timeline(service_type)
        )

        # ── Load lead for LLM context (no PII - public data only) ─────────────
        lead_id = getattr(deal, "lead_id", None)
        lead = await get_lead(lead_id, db) if lead_id else None

        # ── Generate contextual descriptions via LLM ──────────────────────────
        decomposition = _DECOMPOSITION[service_type]
        descriptions = await self._generate_descriptions(
            task=task,
            lead=lead,
            deal=deal,
            service_type=service_type,
            decomposition=decomposition,
        )

        if dry_run:
            return AgentResult(
                task_id=task.id,
                success=True,
                output={
                    "deal_id": str(deal_id),
                    "service_type": service_type,
                    "dry_run": True,
                    "planned_count": len(decomposition),
                    "timeline_weeks": timeline_weeks,
                },
                next_tasks=["doc_generator.generate"],
            )

        # ── Calculate milestone due dates ─────────────────────────────────────
        kickoff_date = date.today()
        due_dates = _compute_due_dates(decomposition, kickoff_date, timeline_weeks)

        # ── Create service_deliveries (first pass: no depends_on) ─────────────
        # Map: (type, positional_index) -> UUID for reference resolution
        type_counters: dict[str, int] = {}
        type_to_ids: dict[str, list[UUID]] = {}  # type -> [id, id, ...]
        created_sds = []  # (spec_index, service_delivery_object)

        for i, spec in enumerate(decomposition):
            stype = spec["type"]
            type_counters[stype] = type_counters.get(stype, 0) + 1
            pos_index = type_counters[stype]  # 1-based

            sd = await create_service_delivery(
                deal_id=deal_id,
                client_id=client_id,
                data={
                    "service_type": service_type,
                    "type": stype,
                    "title": spec["title"],
                    "description": descriptions[i],
                    "milestone_name": spec["milestone_name"],
                    "milestone_due": due_dates[i],
                    "depends_on": [],  # resolved in second pass
                },
                db=db,
            )

            # Register in map: both bare type and positional
            type_to_ids.setdefault(stype, []).append(sd.id)
            created_sds.append((i, sd))

        # ── Second pass: resolve depends_on to UUIDs ──────────────────────────
        for i, sd in created_sds:
            spec = decomposition[i]
            raw_deps = spec.get("depends_on", [])
            if not raw_deps:
                continue
            resolved = _resolve_depends_on(raw_deps, type_to_ids)
            if resolved:
                await update_service_delivery(sd.id, {"depends_on": resolved}, db)

        # ── Update deal status → in_delivery ─────────────────────────────────
        await update_deal(deal_id, {"status": "in_delivery"}, db)

        # ── Register idempotency key ──────────────────────────────────────────
        await create_task(
            type="delivery_orchestrator.plan",
            agent="delivery_orchestrator",
            payload={"deal_id": str(deal_id), "count": len(decomposition)},
            db=db,
            deal_id=deal_id,
            idempotency_key=idem_key,
        )

        # ── Identify first ready deliverables (no dependencies) ───────────────
        ready_ids = [
            str(sd.id)
            for _, sd in created_sds
            if not decomposition[_][1].get("depends_on")
            # Note: we re-check spec deps (empty = ready immediately)
        ]
        # Recompute cleanly
        ready_ids = [
            str(sd.id)
            for i, sd in created_sds
            if not decomposition[i].get("depends_on")
        ]

        self.log.info(
            "delivery_orchestrator.plan_complete",
            task_id=str(task.id),
            deal_id=str(deal_id),
            service_type=service_type,
            deliveries_created=len(created_sds),
            ready_count=len(ready_ids),
        )

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "deal_id": str(deal_id),
                "client_id": str(client_id),
                "service_type": service_type,
                "deliveries_created": len(created_sds),
                "timeline_weeks": timeline_weeks,
                "ready_delivery_ids": ready_ids,
                "status": "in_delivery",
            },
            next_tasks=["doc_generator.generate"] if ready_ids else [],
        )

    # ── Action: check_progress ────────────────────────────────────────────────

    async def _check_progress(
        self,
        task: AgentTask,
        deal: object,
        deal_id: UUID,
        client_id: UUID,
        service_type: str,
        dry_run: bool,
        db: AsyncSession,
    ) -> AgentResult:
        """
        Check completion status of all service deliveries.
        Dispatch next ready ones, or mark deal as delivered if all done.
        """
        deliveries = await get_service_deliveries_for_deal(deal_id, db)

        if not deliveries:
            # Edge case: check_progress called before plan — re-plan
            raise AgentToolError(
                code="validation_deal_wrong_status",
                message=f"Deal {deal_id}: no service_deliveries found — run 'plan' first",
            )

        done_ids: set[str] = {str(sd.id) for sd in deliveries if sd.status in _DONE_STATUSES}
        all_done = len(done_ids) == len(deliveries)

        if all_done:
            self.log.info(
                "delivery_orchestrator.all_done",
                task_id=str(task.id),
                deal_id=str(deal_id),
                total=len(deliveries),
            )
            if not dry_run:
                await update_deal(deal_id, {"status": "delivered"}, db)
            return AgentResult(
                task_id=task.id,
                success=True,
                output={
                    "deal_id": str(deal_id),
                    "status": "delivered",
                    "completed_count": len(deliveries),
                    "total_count": len(deliveries),
                    "ready_delivery_ids": [],
                },
                next_tasks=["account_manager.onboard"],
            )

        # ── Find deliverables that are ready to execute ───────────────────────
        # A deliverable is ready if:
        #   - status == "pending"
        #   - all its depends_on UUIDs are in done_ids
        ready_sds = []
        for sd in deliveries:
            if sd.status != "pending":
                continue
            dep_ids = [str(uid) for uid in (getattr(sd, "depends_on", None) or [])]
            if all(dep in done_ids for dep in dep_ids):
                ready_sds.append(sd)

        in_progress_count = sum(1 for sd in deliveries if sd.status == "in_progress")

        self.log.info(
            "delivery_orchestrator.progress",
            task_id=str(task.id),
            deal_id=str(deal_id),
            completed=len(done_ids),
            total=len(deliveries),
            ready=len(ready_sds),
            in_progress=in_progress_count,
        )

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "deal_id": str(deal_id),
                "status": "in_delivery",
                "completed_count": len(done_ids),
                "total_count": len(deliveries),
                "in_progress_count": in_progress_count,
                "ready_delivery_ids": [str(sd.id) for sd in ready_sds],
            },
            next_tasks=["doc_generator.generate"] if ready_sds else [],
        )

    # ── LLM: generate descriptions ────────────────────────────────────────────

    async def _generate_descriptions(
        self,
        task: AgentTask,
        lead: object | None,
        deal: object,
        service_type: str,
        decomposition: list[dict],
    ) -> list[str]:
        """
        Call claude-opus-4-6 to generate contextual descriptions for deliverables.
        Input: no PII — public business context only.
        """
        user_input = {
            "business_name": getattr(lead, "business_name", "") if lead else "",
            "sector": getattr(lead, "sector", "") if lead else "",
            "sector_label": _sector_label(getattr(lead, "sector", "") if lead else ""),
            "service_type": service_type,
            "gap_summary": getattr(lead, "gap_summary", "") if lead else "",
            "deliverables": [
                {"type": spec["type"], "title": spec["title"]}
                for spec in decomposition
            ],
        }

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
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
            result = json.loads(text)
            descriptions = result.get("descriptions", [])
            if len(descriptions) == len(decomposition):
                return descriptions
        except (json.JSONDecodeError, ValueError):
            pass

        self.log.warning(
            "delivery_orchestrator.llm_parse_error",
            task_id=str(task.id),
            service_type=service_type,
        )
        # Fallback: use generic descriptions based on type
        return [_default_description(spec["type"]) for spec in decomposition]


# ── Module-level pure helpers ─────────────────────────────────────────────────

def _resolve_depends_on(
    raw_refs: list[str],
    type_to_ids: dict[str, list[UUID]],
) -> list[UUID]:
    """
    Resolve string dependency references to UUIDs.

    References can be:
      "report"     → first service_delivery with type="report"
      "workshop_1" → first workshop (1-indexed)
      "workshop_2" → second workshop
    """
    resolved: list[UUID] = []
    for ref in raw_refs:
        # Check if positional: ends with _{digit}
        parts = ref.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            type_name = parts[0]
            idx = int(parts[1]) - 1  # 1-based → 0-based
        else:
            type_name = ref
            idx = 0

        ids = type_to_ids.get(type_name, [])
        if idx < len(ids):
            resolved.append(ids[idx])
    return resolved


def _compute_due_dates(
    decomposition: list[dict],
    kickoff: date,
    timeline_weeks: int,
) -> list[date | None]:
    """
    Assign milestone_due dates based on dependency depth.

    Uses topological layers: each layer's due = kickoff + (layer_index / total_layers) * timeline_days.
    Deliverables without milestone_name get None.
    """
    n = len(decomposition)
    if n == 0:
        return []

    total_days = timeline_weeks * 7

    # Build adjacency for topological layers
    # Map type refs to their positional index in decomposition list
    type_counters: dict[str, int] = {}
    ref_to_idx: dict[str, int] = {}
    for i, spec in enumerate(decomposition):
        stype = spec["type"]
        type_counters[stype] = type_counters.get(stype, 0) + 1
        pos = type_counters[stype]
        ref_to_idx[stype] = i  # bare type → last occurrence (overwritten)
        ref_to_idx[f"{stype}_{pos}"] = i

    # Compute depth (layer) for each deliverable
    depth: list[int] = [0] * n
    for i, spec in enumerate(decomposition):
        for dep_ref in spec.get("depends_on", []):
            dep_idx = ref_to_idx.get(dep_ref)
            if dep_idx is not None and dep_idx != i:
                depth[i] = max(depth[i], depth[dep_idx] + 1)

    max_depth = max(depth) if depth else 0
    layers = max_depth + 1

    due_dates: list[date | None] = []
    for i, spec in enumerate(decomposition):
        if not spec.get("milestone_name"):
            # No milestone → still compute a due date for tracking purposes
            layer_fraction = (depth[i] + 1) / layers
            delta = timedelta(days=max(1, int(total_days * layer_fraction)))
            due_dates.append(kickoff + delta)
        else:
            # Milestone deliverable: due at the end of their layer
            layer_fraction = (depth[i] + 1) / layers
            delta = timedelta(days=max(1, int(total_days * layer_fraction)))
            due_dates.append(kickoff + delta)

    return due_dates


def _default_timeline(service_type: str) -> int:
    """Fallback timeline_weeks when proposal doesn't have one."""
    _DEFAULTS = {"consulting": 4, "web_design": 4, "digital_maintenance": 2}
    return _DEFAULTS.get(service_type, 4)


def _default_description(delivery_type: str) -> str:
    """Generic fallback description when LLM parsing fails."""
    _DESC: dict[str, str] = {
        "report": "Analisi approfondita con dati, raccomandazioni prioritizzate e piano di azione.",
        "workshop": "Sessione interattiva con agenda strutturata, esercizi pratici e deliverable concreti.",
        "roadmap": "Piano d'azione con milestone, responsabili e KPI misurabili per ogni fase.",
        "process_schema": "Schema visivo AS-IS/TO-BE con gap identificati e miglioramenti proposti.",
        "presentation": "Slide executive che raccoglie tutti i risultati e le raccomandazioni del progetto.",
        "wireframe": "Struttura e navigazione del sito con layout di ciascuna pagina.",
        "mockup": "Design visivo ad alta fedeltà con palette colori, tipografia e contenuti.",
        "branding": "Identità visiva completa: logo, palette colori, tipografia e linee guida brand.",
        "page": "Realizzazione di tutte le pagine del sito, ottimizzate per desktop e mobile.",
        "responsive_check": "Verifica di compatibilità e leggibilità su tutti i dispositivi principali.",
        "performance_audit": "Analisi tecnica approfondita: performance, sicurezza, vulnerabilità e metriche.",
        "update_cycle": "Esecuzione aggiornamenti software con backup preventivo e documentazione interventi.",
        "security_patch": "Applicazione patch di sicurezza critiche con report dettagliato degli interventi.",
        "monitoring_setup": "Configurazione sistema di monitoraggio continuo con alert automatici e dashboard.",
    }
    return _DESC.get(delivery_type, f"Esecuzione del deliverable: {delivery_type}.")


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
