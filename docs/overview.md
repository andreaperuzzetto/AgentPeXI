# Agenti — panoramica

---

## Interfaccia standard

Ogni agente estende `BaseAgent` da `agents/base.py`.
Il system prompt vive in `agents/{nome}/prompts/system.md` — letto a runtime, non hardcoded.

```python
class BaseAgent(ABC):
    # Attributo di classe obbligatorio in ogni sottoclasse
    agent_name: str

    def __init__(self) -> None:
        self.log = structlog.get_logger().bind(agent=self.agent_name)

    async def run(self, task: AgentTask) -> AgentResult:
        """Entry point Celery. Non fare override — gestisce lifecycle, DB, try/except."""
        ...  # implementazione in agents/base.py

    @abstractmethod
    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        """Logica specifica dell'agente. Implementare in ogni sottoclasse."""
        ...
```

## Esecuzione via Celery

Ogni agente è registrato in `agents/worker.py` con un wrapper sync → async.
Non chiamare mai `agent.run()` direttamente dall'Orchestrator — usare sempre Celery.
Vedi pattern completo in `docs/inter-agent.md`.

---

## Scope dati

Ogni agente accede **solo** alle tabelle elencate. Schema completo in `docs/db-schema.md`.

| Agente | Legge | Scrive |
|--------|-------|--------|
| Scout | `config/sectors.yaml` | `leads`, `tasks` |
| Market Analyst | `leads` | `leads` (score, analysis, suggested_service_type), `tasks` |
| Lead Profiler | `leads` | `leads` (campi enriched), `tasks` |
| Design Agent | `deals`, `leads` | `tasks`, MinIO artefatti (mockup/presentazioni/schemi) |
| Proposal Agent | `deals`, `leads`, `clients` | `proposals`, `tasks`, MinIO PDF |
| Sales Agent | `deals`, `proposals`, `clients` | `deals.status`, `email_log`, `tasks` |
| Delivery Orchestrator | `deals`, `service_deliveries` | `service_deliveries`, `tasks` |
| Document Generator | workspace cliente, `service_deliveries` | workspace cliente, `service_deliveries.status`, `tasks` |
| Delivery Tracker | `service_deliveries`, workspace cliente | `delivery_reports`, `service_deliveries.status`, `tasks` |
| Account Manager | `clients`, `deals`, `nps_records` | `nps_records`, `tasks`, `leads` (upsell) |
| Billing Agent | `deals`, `invoices`, `clients` | `invoices`, `tasks` |
| Support Agent | `tickets`, `clients`, workspace cliente/docs | `tickets`, `service_deliveries` (solo nuovi task), `tasks` |

---

## Dettaglio per agente

Il contesto operativo specifico di ogni agente è in `agents/{nome}/CLAUDE.md`:
responsabilità, payload input/output, gate da verificare, tabelle accessibili, comandi test.

```
agents/
├── scout/CLAUDE.md
├── analyst/CLAUDE.md
├── lead_profiler/CLAUDE.md
├── design/CLAUDE.md
├── proposal/CLAUDE.md
├── sales/CLAUDE.md
├── delivery_orchestrator/CLAUDE.md
├── doc_generator/CLAUDE.md
├── delivery_tracker/CLAUDE.md
├── account_manager/CLAUDE.md
├── billing/CLAUDE.md
└── support/CLAUDE.md
```
