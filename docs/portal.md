# Portale cliente

Pagina Next.js che permette al cliente di visualizzare la proposta e approvarla o rifiutarla.
È l'interfaccia del GATE 1 (approvazione proposta) e del GATE 3 (conferma deploy).

---

## Architettura

Il portale è una route protetta dell'app Next.js principale, non un'app separata.

```
frontend/app/portal/
├── [token]/
│   ├── page.tsx          ← Server Component: verifica JWT, carica dati
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

def generate_portal_token(proposal_id: str, deal_id: str) -> str:
    payload = {
        "proposal_id": proposal_id,
        "deal_id": deal_id,
        "exp": datetime.utcnow() + timedelta(hours=72),
        "iat": datetime.utcnow(),
        "type": "portal_access"
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

- Logo e nome del business operatore
- Nome del business cliente
- PDF inline della proposta (iframe o react-pdf)
- Sezione riassuntiva: soluzione proposta, pricing, timeline
- Due pulsanti: **Approvo** e **Non approvo**

Design: minimal, mobile-first, niente dark mode (è rivolto al cliente finale).
Font: system font stack, niente JetBrains Mono.
Lingua: italiano.

---

### 4 — Approvazione

Il cliente clicca "Approvo". Appare un dialog di conferma.
Alla conferma, il Client Component chiama:

```
POST /webhooks/portal/client-approve
{ "proposal_id": "uuid", "token": "jwt" }
```

Il backend (vedi `docs/api.md`):
1. Verifica JWT con `PORTAL_SECRET_KEY`
2. Aggiorna DB
3. Pubblica su Redis → Orchestrator riprende il run
4. Risponde 200

La pagina mostra messaggio di conferma: _"Perfetto! Verrete contattati entro 24 ore per definire i dettagli del progetto."_

---

### 5 — Rifiuto

Il cliente clicca "Non approvo". Appare un form con campo note opzionale.
Alla conferma, chiama:

```
POST /webhooks/portal/client-reject
{ "proposal_id": "uuid", "token": "jwt", "notes": "..." }
```

La pagina mostra: _"Grazie per il feedback. Potrete ricontattarci in qualsiasi momento."_

---

### 6 — Link scaduto

Se il token JWT è scaduto (> 72h), la verifica lato server fallisce.
Redirect a `/portal/expired` che mostra: _"Questo link è scaduto. Contattaci per ricevere una nuova proposta."_ con email/telefono dell'operatore.

---

## Sicurezza

- Il JWT viene verificato **solo lato server** (Server Component o API route). Mai lato client.
- `PORTAL_SECRET_KEY` è diverso da `SECRET_KEY` — compromettere uno non compromette l'altro.
- Il token contiene solo `proposal_id` e `deal_id` — nessun dato PII.
- Una volta usato (risposta ricevuta), il token non è più accettato (`already_responded`).
- Il portale non richiede login — il link è il meccanismo di autenticazione.
- Nessuna informazione sensibile è esposta nella pagina prima della verifica JWT.

---

## Variabili d'ambiente richieste

```bash
PORTAL_SECRET_KEY=      # firma JWT portale (diverso da SECRET_KEY)
BASE_URL=               # es. http://localhost:3000
```

---

## Estensione futura — GATE 3 (approvazione deploy)

Lo stesso meccanismo si applica al GATE 3: il sistema invia al cliente
un link `/portal/{token}` che mostra il risultato della fase di sviluppo
(demo link, screenshot, changelog) e permette di approvare il deploy in produzione.

Il token GATE 3 usa `type: "deploy_access"` nel payload JWT.
Il webhook corrispondente è `POST /webhooks/portal/deploy-approve`.
