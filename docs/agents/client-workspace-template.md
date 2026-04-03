# Progetto {CLIENT_NAME} — CLAUDE.md

> Generato automaticamente dal Proposal Agent al kickoff confermato.
> Le regole di sicurezza in `../../../../CLAUDE.md` hanno sempre precedenza su questo file.

## Cliente

- **ID:** `{CLIENT_ID}`
- **Settore:** `{SECTOR}`
- **SLA risposta:** `{SLA_HOURS}h lavorative`
- **Referente:** consultare CRM (non loggare dati di contatto)

## Stack concordato

```
{AGREED_STACK}
```

## Feature approvate

<!-- Scope definito nella proposta approvata. Non aggiungere feature non elencate. -->

- [ ] {FEATURE_1} — spec in `specs/{feature_1_slug}.md`
- [ ] {FEATURE_2} — spec in `specs/{feature_2_slug}.md`

## Criteri di accettazione

{ACCEPTANCE_CRITERIA}

## Branch e deploy

- Branch produzione: `main`
- Hosting: `{HOSTING_PLATFORM}`
- Env vars: vedi `.env.example` in questa directory (mai valori reali nel file)
- Deploy: richiede `deal.deploy_approved = true` nel DB principale (GATE 3)

## Note specifiche del progetto

{PROJECT_NOTES}

---

*Generato da Proposal Agent · Deal ID: `{DEAL_ID}` · Data: `{GENERATED_AT}`*
