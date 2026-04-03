# Scout Agent

> Letto automaticamente da Claude Code quando lavori in questa directory.
> Le regole globali in `../../CLAUDE.md` si applicano sempre.

## Responsabilità

Interroga Google Maps per trovare business nelle zone target e restituisce una lista di lead grezzi all'Orchestrator. Non valuta, non sceglie, non contatta. Solo scoperta.

## Tool disponibili

- `tools/google_maps.py` — wrapper rate-limited (100 req/s). **Non** usare `googlemaps.Client` direttamente.
- `tools/db_tools.py` — per la deduplication (check `google_place_id` su `leads`)
- `tools/file_store.py` — per eventuale cache risultati Maps

## Input atteso (task.payload)

```python
{
    "zone": str,          # es. "Treviso, Italia"
    "sector": str,        # chiave da config/sectors.yaml
    "radius_km": int,     # default 10, max 50
    "max_results": int,   # default 20
    "dry_run": bool       # default False — se True non scrive su DB
}
```

## Output atteso (AgentResult.output)

```python
{
    "leads_found": int,
    "leads_written": int,   # 0 se dry_run
    "skipped_duplicates": int,
    "zone_searched": str,
    "radius_used_km": int
}
```

## Logica di fallback

Se `leads_found < 3`, espandere `radius_km + 5` e riprovare.
Max 3 espansioni. Se ancora insufficiente: `status = "blocked"`.

## Tabelle accessibili

| Operazione | Tabella |
|-----------|---------|
| Legge | `config/sectors.yaml` |
| Scrive | `leads`, `tasks` |

Non toccare `deals`, `clients`, `proposals` o qualsiasi altra tabella.

## System prompt

`agents/scout/prompts/system.md`

## Test

```bash
pytest tests/agents/test_scout.py -v
python -m agents.scout.run --zone "Treviso" --sector "horeca" --dry-run
```
