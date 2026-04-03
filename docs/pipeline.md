# Pipeline operativa

Il sistema gestisce tre tipologie di servizio: **Consulenza**, **Web Design** e **Manutenzione Digitale**.
La pipeline è comune fino alla Fase 02. Dalla Fase 03 il flusso si differenzia per `service_type`.

---

## Fase 01 — Discovery (autonoma)

Scout → Market Analyst → Lead Profiler. Nessun gate umano.

**Criteri di qualifica lead (tutti obbligatori):**
- `lead_score >= 65` (soglia universale per tutti i servizi)
- Settore in `config/sectors.yaml`
- Gap rilevato (`service_gap_detected = true`): varia in base al servizio potenziale
- Nessun record `deals` con stesso `google_place_id`

**Gap per tipologia di servizio:** vedi [`docs/service-types.md`](service-types.md) — colonna "Segnali di gap" per ciascun servizio.

**Fallback no-leads:** espandere raggio `+5km` × 3 iterazioni max.
Se ancora nessun lead: `status = "blocked"`, `blocked_reason = "no_qualified_leads_in_zone"`.

---

## Fase 02 — Proposal (semi-autonoma)

Design Agent → Proposal Agent → **GATE 1** → Sales Agent.

Il Design Agent produce **artefatti contestuali** in base al `service_type`:
- **Consulenza:** presentazioni visive, strutture workshop, schemi di processi, roadmap operative
- **Web Design:** mockup UI (landing page, pagine interne, responsive)
- **Manutenzione Digitale:** schemi architetturali, piani di aggiornamento, dashboard di monitoraggio

La proposta commerciale cambia leggermente a seconda del contesto del servizio.
Pricing: **per progetto**.

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

## Fase 03 — Delivery (semi-autonoma, differenziata per servizio)

**GATE 2 — Kickoff erogazione**
Verificare `deal.kickoff_confirmed = true` prima di avviare l'erogazione.
(Necessario anche dopo approvazione cliente — pianificazione risorse dell'operatore.)

Il Delivery Orchestrator coordina gli agenti di erogazione in base al `service_type`.
I Code Agent, QA Agent e Dev Orchestrator della vecchia pipeline sono **disattivati** e sostituiti da:

| Vecchio agente | Nuovo agente | Funzione |
|---------------|-------------|----------|
| Dev Orchestrator | **Delivery Orchestrator** | Pianifica e traccia l'erogazione del servizio |
| Code Team | **Document Generator** | Genera report, presentazioni, documenti di progetto |
| QA Agent | **Delivery Tracker** | Traccia avanzamento, milestone, qualità deliverable |

### Milestone specifiche per servizio

| Servizio | Milestone di kickoff | Milestone chiave |
|----------|---------------------|-----------------|
| Consulenza | Firma contratto o inizio primo workshop | `consulting_approved` |
| Web Design | Inizio progettazione | Approvazione mockup finale |
| Manutenzione Digitale | Avvio servizio | Primo ciclo di aggiornamento pianificato |

### GATE 3 — Approvazione consegna finale
Verificare `deal.delivery_approved = true` prima di chiudere il deal come consegnato.
(Per consulenza il gate si chiama `consulting_approved` ed è verificato allo stesso modo.)

---

## Fase 04 — Post-Sale (autonoma con escalation)

Dopo la consegna delle credenziali di onboarding al cliente, l'Account Manager
imposta `deal.status = "active"` (transizione da `"delivered"`). Da questo momento
il deal è in gestione attiva: NPS survey, upsell, supporto continuativo.

Il supporto post-vendita è orientato al servizio offerto:
- Richieste di assistenza e aggiornamenti
- Richieste di servizio aggiuntivo
- Supporto post vendita specifico per il servizio erogato

**Escalation automatica** all'operatore se:
- Ticket support aperto da > 48h senza risposta
- `billing_dispute = true` su una fattura
- NPS < 6 nell'ultimo survey automatico
- Soddisfazione cliente in calo
