# Agenti — panoramica

---

## Interfaccia standard

Ogni agente estende `BaseAgent` da `agents/base.py`.
Il system prompt vive in `agents/{nome}/prompts/system.md` — letto a runtime, non hardcoded.

```python
class BaseAgent(ABC):
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 8192

    @abstractmethod
    async def run(self, task: AgentTask) -> AgentResult: ...

    async def validate_input(self, task: AgentTask) -> AgentResult | None:
        """None = ok. AgentResult(success=False) = input non valido."""
        return None

    def get_tools(self) -> list[Tool]:
        return []
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
| Market Analyst | `leads` | `leads` (score, analysis, business_model), `tasks` |
| Lead Profiler | `leads` | `leads` (campi enriched), `tasks` |
| Design Agent | `deals`, `leads` | `tasks`, MinIO mockups |
| Proposal Agent | `deals`, `leads`, `clients` | `proposals`, `tasks`, MinIO PDF |
| Sales Agent | `deals`, `proposals`, `clients` | `deals.status`, `email_log`, `tasks` |
| Dev Orchestrator | `deals` | `dev_tasks`, `tasks` |
| Code Agent Team | workspace cliente, `dev_tasks` | workspace cliente, `dev_tasks.status`, `tasks` |
| QA Agent | workspace cliente, `dev_tasks` | `qa_reports`, `dev_tasks.status`, `tasks` |
| Account Manager | `clients`, `deals`, `nps_records` | `nps_records`, `tasks`, `leads` (upsell) |
| Billing Agent | `deals`, `invoices`, `clients` | `invoices`, `tasks` |
| Support Agent | `tickets`, `clients`, workspace cliente/docs | `tickets`, `dev_tasks` (solo fix), `tasks` |

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
├── dev_orchestrator/CLAUDE.md
├── code_team/CLAUDE.md
├── qa/CLAUDE.md
├── account_manager/CLAUDE.md
├── billing/CLAUDE.md
└── support/CLAUDE.md
```
