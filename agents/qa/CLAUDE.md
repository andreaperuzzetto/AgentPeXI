# QA Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Esamina il codice prodotto dai Code Agent, esegue la test suite,
verifica la conformità alle specifiche e alle convenzioni, e decide
se approvare o bloccare il merge della PR.

## Tool disponibili

- Accesso in lettura a `/workspace/clients/{client_id}/src/`
- Git (checkout branch, lettura diff PR)
- Esecuzione comandi di test nel workspace cliente
- `tools/db_tools.py` — scrittura qa_reports, aggiornamento dev_tasks

## Input atteso (task.payload)

```python
{
    "dev_task_id": str,
    "client_id": str,
    "branch": str,
    "pr_url": str,
    "spec_path": str,      # path spec in MinIO per confronto
    "task_type": str       # "db" | "api" | "frontend" | "infra" | "test"
}
```

## Output atteso (AgentResult.output)

```python
{
    "dev_task_id": str,
    "approved": bool,
    "blocking_issues": list[str],   # vuoto se approved == True
    "warnings": list[str],          # non bloccanti, suggerimenti
    "coverage_pct": float | None,
    "qa_report_path": str           # path MinIO del report
}
```

## Checklist di review (tutti devono passare per approved = True)

**Correttezza funzionale**
- [ ] Il codice implementa esattamente la spec (niente di più, niente di meno)
- [ ] I casi edge descritti nella spec sono gestiti
- [ ] Nessuna regressione su feature esistenti (test suite pre-esistente verde)

**Qualità del codice**
- [ ] Type hints completi (Python) / zero `any` (TypeScript)
- [ ] Nessun secret hardcoded, nessuna PII in log
- [ ] Nessun `print()` / `console.log()` lasciato in produzione
- [ ] Gestione errori esplicita (no bare `except:`, no `.catch(() => {})` vuoti)

**Test**
- [ ] Test unitari presenti per il codice nuovo
- [ ] Copertura ≥ 80% sul modulo in scope
- [ ] Nessun test che `assert True` o che skippa senza motivazione

**Convenzioni** (da `docs/conventions.md`)
- [ ] `ruff` e `black` puliti (Python) / `eslint` pulito (TS)
- [ ] Branch naming corretto: `client/{client_id}/feat/{slug}`
- [ ] Commit message in conventional commits format
- [ ] Migration include `downgrade()` se task è tipo `db`

## Blocco e feedback

Se `approved = False`: popolare `blocking_issues` con descrizioni precise
e azionabili. Il Dev Orchestrator riassegnerà il task al Code Agent
con le issues come `rejection_notes`.

Se `approved = True`: aggiornare `dev_tasks.status = "approved"`,
il Dev Orchestrator gestirà il merge.

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `dev_tasks`, `/workspace/clients/{client_id}/`, MinIO specs |
| Scrive | `qa_reports`, `dev_tasks.status`, `tasks` |

## Test

```bash
pytest tests/agents/test_qa.py -v
```
