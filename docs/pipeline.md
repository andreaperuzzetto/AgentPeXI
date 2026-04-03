# Pipeline operativa

## Fase 01 — Discovery (autonoma)

Scout → Market Analyst → Lead Profiler. Nessun gate umano.

**Criteri di qualifica lead (tutti obbligatori):**
- `lead_score >= 65`
- Settore in `config/sectors.yaml`
- `website_exists = true` AND `digitalization_gap_detected = true`
- Nessun record `deals` con stesso `google_place_id`

**Fallback no-leads:** espandere raggio `+5km` × 3 iterazioni max.
Se ancora nessun lead: `status = "blocked"`, `blocked_reason = "no_qualified_leads_in_zone"`.

---

## Fase 02 — Proposal (semi-autonoma)

Design Agent → Proposal Agent → **GATE 1** → Sales Agent.

### GATE 1 — Revisione proposta
```python
# In orchestrator/nodes/checkpoint.py
deal = await db.get(Deal, deal_id)
assert deal.proposal_human_approved is True  # letto da DB, non da cache
```

**Rifiuto da operatore:** `proposal_rejection_notes` nel payload → rilancia Design + Proposal.
Max `proposal_rejection_count = 5`, poi escalation manuale.

**Negoziazione dal cliente:** `deal.status = "negotiating"`.
Sales Agent gestisce autonomamente fino a 2 round (modifiche minor).
Oltre: `status = "blocked"`, notifica operatore.

---

## Fase 03 — Development (semi-autonoma)

**GATE 2 — Kickoff sviluppo**
Verificare `deal.kickoff_confirmed = true` prima di avviare i Code Agent.
(Necessario anche dopo approvazione cliente — pianificazione risorse.)

**Lavoro autonomo Code Agent:**
- Branch: `client/{client_id}/feat/{slug}` — mai su `main`
- PR solo quando tutti i test passano
- QA Agent review obbligatoria prima del merge

**GATE 3 — Deploy in produzione**
Verificare `deal.deploy_approved = true` prima di qualsiasi push su hosting cliente.

---

## Fase 04 — Post-Sale (autonoma con escalation)

**Escalation automatica** all'operatore se:
- Ticket support aperto da > 48h senza risposta
- `billing_dispute = true` su una fattura
- NPS < 6 nell'ultimo survey automatico
- Uptime produzione < 99%
