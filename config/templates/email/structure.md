# Struttura template email

Tutti i template sono file Markdown con frontmatter YAML.
Il Sales Agent e l'Account Manager li leggono a runtime e li passano a Claude
per personalizzare il contenuto prima dell'invio via Gmail API.

---

## Formato file

> **Nota sintassi:** Le `{{variabili}}` nei template email sono **placeholder Claude**,
> non template Jinja2. Claude legge il Markdown del template e le sostituisce con i valori
> reali prima di generare il testo finale da inviare. Al momento dell'invio il testo è già
> completamente espanso — `tools/gmail.py` non esegue nessun rendering Jinja2.

```
---
template: proposal_send
subject: "Proposta personalizzata per {{business_name}}"
from_name: "{{operator_name}}"
language: it
tone: professional_warm
---

Corpo del template con {{variabili}} tra doppie graffe.
```

---

## Variabili disponibili in tutti i template

| Variabile | Fonte | Esempio |
|-----------|-------|---------|
| `{{business_name}}` | `leads.business_name` | "Bar Centrale" |
| `{{contact_name}}` | `clients.contact_name` | "Mario Rossi" |
| `{{operator_name}}` | env `OPERATOR_NAME` | "Andrea Bianchi" |
| `{{operator_email}}` | env `OPERATOR_EMAIL` | "andrea@example.com" |
| `{{deal_id}}` | `deals.id` | UUID |
| `{{sector_label}}` | `config/sectors.yaml` | "Ristorazione" |

---

## Template: `proposal_send`

**Quando:** primo invio proposta (Sales Agent, action: `send_proposal`)
**Variabili aggiuntive:** `{{portal_url}}`, `{{proposal_summary}}`, `{{estimated_value_eur}}`, `{{timeline_weeks}}`

```markdown
Oggetto: Una proposta su misura per {{business_name}}

Gentile {{contact_name}},

Le abbiamo preparato una proposta personalizzata per {{business_name}},
basata su un'analisi del vostro settore e delle opportunità digitali
che abbiamo identificato.

{{proposal_summary}}

Può visualizzare la proposta completa al link qui sotto:
{{portal_url}}

Il link è valido per 72 ore.

Siamo disponibili per qualsiasi domanda.

Cordiali saluti,
{{operator_name}}
```

---

## Template: `follow_up_1`

**Quando:** 3 giorni lavorativi senza risposta
**Variabili aggiuntive:** `{{portal_url}}`

```markdown
Oggetto: Re: Proposta per {{business_name}} — in attesa di riscontro

Gentile {{contact_name}},

Le scrivo in merito alla proposta inviata qualche giorno fa.
Volevamo assicurarci che avesse ricevuto tutto correttamente.

Il link per visualizzarla è ancora attivo:
{{portal_url}}

Siamo a disposizione per qualsiasi chiarimento.

Cordiali saluti,
{{operator_name}}
```

---

## Template: `follow_up_2`

**Quando:** 5 giorni lavorativi dopo follow_up_1
**Tono:** leggermente più diretto, offre alternativa

```markdown
Oggetto: Proposta {{business_name}} — disponibili per una chiamata

Gentile {{contact_name}},

Capisco che i tempi possano essere stretti.
Se preferisce, possiamo anche sentirci telefonicamente per
illustrare la proposta in pochi minuti.

{{portal_url}}

Rispondo anche via email se ha domande specifiche.

Cordiali saluti,
{{operator_name}}
```

---

## Template: `follow_up_3`

**Quando:** 7 giorni lavorativi dopo follow_up_2
**Tono:** definitivo, lascia porta aperta

```markdown
Oggetto: Ultimo contatto — Proposta per {{business_name}}

Gentile {{contact_name}},

Questo sarà il mio ultimo messaggio in merito alla proposta.
Capisco che i tempi possano non essere favorevoli.

Se in futuro vorrà approfondire, la proposta è ancora
disponibile al link:
{{portal_url}}

Le auguro buon lavoro.

Cordiali saluti,
{{operator_name}}
```

---

## Template: `post_sale/onboarding`

**Quando:** consegna progetto (Account Manager, trigger: `delivery`)
**Variabili aggiuntive:** `{{product_name}}`, `{{docs_url}}`, `{{support_email}}`

```markdown
Oggetto: {{product_name}} è pronto — ecco come iniziare

Gentile {{contact_name}},

{{product_name}} è operativo e pronto all'uso.

Per iniziare:
- Documentazione: {{docs_url}}
- Supporto: {{support_email}}

Nelle prossime settimane la contatteremo per verificare
che tutto funzioni al meglio.

Cordiali saluti,
{{operator_name}}
```

---

## Template: `post_sale/nps_survey`

**Quando:** 30 giorni dalla consegna
**Variabili aggiuntive:** `{{nps_url}}`, `{{product_name}}`

```markdown
Oggetto: Come sta andando con {{product_name}}?

Gentile {{contact_name}},

Sono passate alcune settimane dalla consegna di {{product_name}}.
Le chiedo un minuto per dirci come sta andando:

{{nps_url}}

Il suo feedback è prezioso per migliorare il nostro servizio.

Grazie,
{{operator_name}}
```

---

## Template: `post_sale/checkin`

**Quando:** 7 giorni dalla consegna

```markdown
Oggetto: Tutto ok con {{product_name}}?

Gentile {{contact_name}},

Volevamo assicurarci che {{product_name}} stia funzionando
correttamente e che non ci siano domande o necessità.

Per qualsiasi cosa, rispondo a questa email
oppure scriva a {{support_email}}.

Cordiali saluti,
{{operator_name}}
```

---

## Personalizzazione da parte degli agenti

Il Sales Agent e Account Manager passano il template a Claude con questo prompt:

```
Personalizza il seguente template email per il cliente {business_name},
settore {sector_label}. Mantieni la struttura e il tono.
Sostituisci le variabili con i valori forniti.
Non aggiungere informazioni non presenti nel template.
Rispondi solo con il testo dell'email, niente altro.

Template:
{template_content}

Valori:
{variables_json}
```
