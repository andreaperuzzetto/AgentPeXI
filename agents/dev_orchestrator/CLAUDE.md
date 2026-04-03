# Dev Orchestrator Agent

> ⚠️ **DEPRECATO** — Questo agente è stato sostituito dal **Delivery Orchestrator Agent**
> (`agents/delivery_orchestrator/CLAUDE.md`).
>
> Il Dev Orchestrator faceva parte della vecchia pipeline di sviluppo software.
> Con il pivot verso servizi (consulenza, web design, manutenzione digitale),
> la pianificazione e il coordinamento dell'erogazione sono gestiti dal Delivery Orchestrator.
>
> Questa directory è mantenuta come riferimento storico. Non creare nuovi task per questo agente.
> Vedi `docs/overview.md` → "Mappa agenti: vecchi vs nuovi" per il mapping completo.

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Decompone le specifiche approvate in task di sviluppo atomici, li assegna
ai Code Agent specializzati e monitora l'avanzamento. È il "tech lead"
del progetto cliente. Non scrive codice direttamente.

## GATE 2 — controllo obbligatorio prima dell'avvio

```python
deal = await db.get(Deal, deal_id)
if not deal.kickoff_confirmed:
    raise GateNotApprovedError("GATE 2 non confermato — sviluppo bloccato")
```

## Tool disponibili

- `tools/db_tools.py` — lettura specs, scrittura dev_tasks
- `tools/file_store.py` — lettura specs da MinIO (`clients/{client_id}/specs/`)
- Accesso al workspace cliente: `/workspace/clients/{client_id}/`

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "client_id": str,
    "specs_paths": list[str],   # path MinIO dei file di spec approvati
    "tech_stack": dict,         # stack concordato nel CLAUDE.md del progetto
    "priority_features": list[str]
}
```

## Output atteso (AgentResult.output)

```python
{
    "deal_id": str,
    "dev_tasks_created": int,
    "estimated_sprints": int,
    "task_breakdown": list[dict]  # {id, type, feature, assignee, depends_on}
}
```

## Logica di decomposizione

Per ogni feature nelle specs:
1. Legge il file di spec corrispondente
2. Identifica i layer coinvolti (DB schema, API, frontend, infra)
3. Crea `dev_tasks` con dipendenze esplicite
4. Assegna tipo: `"db"` | `"api"` | `"frontend"` | `"infra"` | `"test"`

Ordine di esecuzione: db → api → frontend → infra → test.
Task senza dipendenze vengono eseguiti in parallelo dai Code Agent.

## Monitoraggio avanzamento

Il Dev Orchestrator viene richiamato dall'Orchestrator ogni volta che
un Code Agent completa un task. Valuta se:
- Procedere con il task successivo nella catena
- Sbloccare task paralleli ora che le dipendenze sono soddisfatte
- Segnalare blocchi all'Orchestrator principale

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `deals`, `specs`, MinIO `clients/{client_id}/specs/` |
| Scrive | `dev_tasks`, `tasks` |
| Legge/scrive | `/workspace/clients/{client_id}/` |

## Test

```bash
pytest tests/agents/test_dev_orchestrator.py -v
python -m agents.dev_orchestrator.run --deal-id <uuid> --dry-run
```
