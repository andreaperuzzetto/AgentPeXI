# Portale cliente

Pagina Next.js che permette al cliente di visualizzare la proposta e approvarla o rifiutarla.
È l'interfaccia del **GATE 1** (approvazione proposta) e del **GATE 3** (conferma consegna).

Il portale mostra proposte contestuali al tipo di servizio:
- **Consulenza:** roadmap operativa, piano workshop, deliverable previsti
- **Web Design:** mockup visivi, struttura sito, timeline
- **Manutenzione Digitale:** piano di manutenzione, SLA, cicli di aggiornamento

---

## Architettura

Il portale è una route protetta dell'app Next.js principale, non un'app separata.

```
frontend/app/portal/
├── [token]/
│   ├── page.tsx          ← Server Component: verifica JWT, carica dati (GATE 1 o GATE 3)
│   ├── ApproveButton.tsx ← Client Component: pulsante con confirm dialog
│   └── RejectForm.tsx    ← Client Component: form rifiuto con note
└── expired/
    └── page.tsx          ← Pagina link scaduto
```

---

## Flusso completo

### 1 — Generazione link (Sales Agent)

Quando il Sales Agent invia la proposta, genera un JWT firmato con `PORTAL_SECRET_KEY`:

```python
import jwt
from datetime import datetime, timedelta

def generate_portal_token(proposal_id: str, deal_id: str, gate: str = "proposal") -> str:
    """
    gate: "proposal" per GATE 1, "delivery" per GATE 3.
    """
    payload = {
        "proposal_id": proposal_id,
        "deal_id": deal_id,
        "exp": datetime.utcnow() + timedelta(hours=72),
        "iat": datetime.utcnow(),
        "type": "portal_access",
        "gate": gate,              # "proposal" | "delivery"
    }
    return jwt.encode(payload, PORTAL_SECRET_KEY, algorithm="HS256")

portal_url = f"{BASE_URL}/portal/{token}"
```

Il token viene salvato su `proposals.portal_link_token` e la scadenza su `proposals.portal_link_expires`.

---

### 2 — Accesso cliente

Il cliente clicca il link nell'email. Next.js carica `app/portal/[token]/page.tsx`.

**Server Component — verifica token:**

```typescript
import { verifyPortalToken } from "@/lib/auth"
import { getProposal } from "@/lib/api"

export default async function PortalPage({
  params,
}: {
  params: { token: string }
}) {
  // 1. Verifica JWT lato server (mai lato client)
  const claims = await verifyPortalToken(params.token)
  if (!claims) redirect("/portal/expired")

  // 2. Carica dati proposta
  const proposal = await getProposal(claims.proposal_id)
  if (proposal.client_response) {
    // Già risposta — mostra stato
    return <AlreadyRespondedPage response={proposal.client_response} />
  }

  // 3. Renderizza portale
  return <ProposalView proposal={proposal} token={params.token} />
}
```

---

### 3 — Cosa vede il cliente

La pagina mostra:

- Logo e nome dell'operatore (Andrea Peruzzetto)
- Nome del business cliente
- **GATE 1 (proposta):** PDF inline della proposta (iframe o react-pdf), riassunto soluzione/pricing/timeline, due pulsanti: **Approvo** e **Non approvo**
- **GATE 3 (consegna):** link ai deliverable prodotti (PDF/HTML), riepilogo lavoro svolto, pulsante **Confermo la consegna**

Il content cambia in base a `claims.gate` nel JWT.

Design: minimal, mobile-first, niente dark mode (rivolto al cliente finale).
Font: system font stack, niente JetBrains Mono.
Lingua: italiano.

---

### 4 — Approvazione

Il cliente clicca "Approvo". Appare un dialog di conferma.
Alla conferma, l'approvazione viene registrata via webhook.
Per il contratto tecnico dell'endpoint: vedi [`docs/api.md`](api.md) — sezione "Portal Webhooks".

La pagina mostra messaggio di conferma: _"Perfetto! Verrete contattati entro 24 ore per definire i dettagli del servizio."_

---

### 5 — Rifiuto

Il cliente clicca "Non approvo". Appare un form con campo note opzionale.
Alla conferma, il rifiuto viene inviato via webhook (vedi [`docs/api.md`](api.md) — sezione "Portal Webhooks").

La pagina mostra: _"Grazie per il feedback. Potrete ricontattarci in qualsiasi momento."_

---

### 6 — Link scaduto

Se il token JWT è scaduto (> 72h), la verifica lato server fallisce.
Redirect a `/portal/expired` che mostra: _"Questo link è scaduto. Contattaci per ricevere una nuova proposta."_ con email/telefono dell'operatore.

---

## GATE 3 — Approvazione consegna

Quando il Delivery Orchestrator completa tutti i deliverable, il sistema
(Delivery Tracker) invia al cliente un link portale GATE 3.

**Generazione token GATE 3 (Delivery Tracker):**
```python
# Recuperare la proposta più recente per ottenere l'ID corretto
proposal = await get_latest_proposal(deal_id, db)   # da tools.db_tools
delivery_token = generate_portal_token(
    proposal_id=str(proposal.id),
    deal_id=str(deal_id),
    gate="delivery",
)
delivery_url = f"{BASE_URL}/portal/{delivery_token}"
```

Per il contratto tecnico dell'endpoint di approvazione consegna: vedi [`docs/api.md`](api.md) — sezione "Portal Webhooks".

---

## Sicurezza

- Il JWT viene verificato **solo lato server** (Server Component o API route). Mai lato client.
- `PORTAL_SECRET_KEY` è diverso da `SECRET_KEY` — compromettere uno non compromette l'altro.
- Il token contiene solo `proposal_id` e `deal_id` — nessun dato PII.
- Una volta usato (risposta ricevuta), il token non è più accettato (`already_responded`).
- Il portale non richiede login — il link è il meccanismo di autenticazione.
- Nessuna informazione sensibile è esposta nella pagina prima della verifica JWT.
- Il campo `gate` nel JWT ("proposal" | "delivery") determina quale contenuto mostrare.

---

## Variabili d'ambiente richieste

```bash
PORTAL_SECRET_KEY=      # firma JWT portale (diverso da SECRET_KEY)
BASE_URL=               # es. http://localhost:3000
```
