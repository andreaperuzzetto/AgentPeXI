# Agenti — panoramica

## Interfaccia standard

Ogni agente estende `BaseAgent` da `agents/base.py`:

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

Il system prompt vive in `agents/{nome}/prompts/system.md` — letto a runtime, non hardcoded.

## Scope dati

Ogni agente accede **solo** alle tabelle elencate. Non leggere né scrivere altro.

| Agente | Legge | Scrive |
|--------|-------|--------|
| Scout | `config/sectors.yaml` | `leads`, `tasks` |
| Market Analyst | `leads` | `leads` (score + analysis), `tasks` |
| Lead Profiler | `leads` | `leads` (enriched), `tasks` |
| Design Agent | `deals`, `leads` | `tasks`, MinIO mockups |
| Proposal Agent | `deals`, `leads`, `clients` | `proposals`, `tasks`, MinIO PDF |
| Sales Agent | `deals`, `proposals`, `clients` | `deals.status`, `email_log`, `tasks` |
| Dev Orchestrator | `deals`, `specs` | `dev_tasks`, `tasks` |
| Code Agent Team | workspace cliente | workspace cliente, `dev_tasks` |
| QA Agent | workspace cliente, `dev_tasks` | `qa_reports`, `dev_tasks` |
| Account Manager | `clients`, `deals` | `nps_records`, `tasks` |
| Billing Agent | `deals`, `invoices` | `invoices`, `tasks` |
| Support Agent | `tickets`, `clients` | `tickets`, `tasks` |

## Dettaglio per agente

Ogni agente ha la propria pagina in `docs/agents/{nome}.md`
e il proprio `agents/{nome}/CLAUDE.md` con contesto operativo specifico.
