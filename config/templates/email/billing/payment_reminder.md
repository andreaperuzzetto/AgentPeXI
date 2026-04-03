---
template: billing/payment_reminder
subject: "{{subject_line}}"
from_name: "{{operator_name}}"
language: it
tone: professional_formal
---

Gentile {{contact_name}},

{{reminder_body}}

Importo: €{{amount_eur}}
Scadenza: {{due_date}}

Per pagamenti o chiarimenti, risponda a questa email.

Cordiali saluti,
{{operator_name}}

---
<!-- Claude: personalizza subject_line e reminder_body in base al tipo:
  - type=gentle (5 gg prima scadenza): tono amichevole, ricorda scadenza imminente
  - type=due (giorno scadenza): tono neutro, conferma importo dovuto
  - type=overdue (7 gg dopo scadenza): tono formale, sollecito con riferimento a termini contrattuali
-->
