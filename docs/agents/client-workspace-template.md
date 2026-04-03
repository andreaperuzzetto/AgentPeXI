# Progetto {CLIENT_NAME} — CLAUDE.md

> Generato automaticamente dal Proposal Agent al kickoff confermato.
> Le regole di sicurezza in `../../../../CLAUDE.md` hanno sempre precedenza su questo file.

## Cliente

- **ID:** `{CLIENT_ID}`
- **Settore:** `{SECTOR}`
- **Tipo servizio:** `{SERVICE_TYPE}` (consulenza | web_design | digital_maintenance)
- **SLA risposta:** `{SLA_HOURS}h lavorative`
- **Referente:** consultare CRM (non loggare dati di contatto)

## Servizio concordato

Tipo: `{SERVICE_TYPE}`

### Deliverable approvati

<!-- Scope definito nella proposta approvata. Non aggiungere deliverable non elencati. -->

- [ ] {DELIVERABLE_1} — descrizione in `deliverables/{deliverable_1_slug}.md`
- [ ] {DELIVERABLE_2} — descrizione in `deliverables/{deliverable_2_slug}.md`

### Milestone

| # | Milestone | Deliverable associati | Gate |
|---|-----------|----------------------|------|
| 1 | {MILESTONE_1} | {DELIVERABLE_LIST_1} | — |
| 2 | {MILESTONE_2} | {DELIVERABLE_LIST_2} | `delivery_approved` |

## Criteri di accettazione

{ACCEPTANCE_CRITERIA}

## Struttura workspace

```
/workspace/clients/{CLIENT_ID}/
├── CLAUDE.md              ← questo file
├── deliverables/          ← artefatti prodotti dal Document Generator
│   ├── {deliverable_1_slug}/
│   └── {deliverable_2_slug}/
├── proposals/             ← proposta commerciale approvata
└── reports/               ← report Delivery Tracker
```

## Consegna

- Approvazione finale: richiede `deal.delivery_approved = true` nel DB principale (GATE 3)
- Artefatti finali in MinIO: `clients/{CLIENT_ID}/deliverables/`

## Note specifiche del progetto

{PROJECT_NOTES}

---

*Generato da Proposal Agent · Deal ID: `{DEAL_ID}` · Data: `{GENERATED_AT}`*
