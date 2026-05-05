from __future__ import annotations

import re
from typing import Any

# ── Limiti token per tipo file ────────────────────────────────────────────────

COMPACTION_LIMITS: dict[str, int] = {
    "seasonal":  3000,
    "pricing":   3000,
    "learnings": 1500,
}
NICHE_HARD_LIMIT    = 2000
PERSONAL_HARD_LIMIT = 1800
DEFAULT_HARD_LIMIT  = 2000

# ── Regex ─────────────────────────────────────────────────────────────────────

_FM_RE       = re.compile(r"^---\n(.*?)\n---\s*\n?", re.DOTALL)
_FM_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# ── System prompts ────────────────────────────────────────────────────────────

_COMPILE_NICHE_SYSTEM = """\
Sei WikiManager di AgentPeXI. Aggiorna il file wiki di una nicchia Etsy.
Restituisci SOLO il file markdown completo con frontmatter YAML, nessun commento, \
nessun wrapper ```markdown.

STRUTTURA OBBLIGATORIA:
---
summary: "<1-2 frasi, max 30 parole, descrivi nicchia e stato attuale>"
last_updated: "<ISO8601>"
confidence: <float 0-1>
agents: [<lista agenti che hanno contribuito, es: research, analytics>]
---

# <Nome Nicchia>

## Domanda e Competizione
## Pricing Osservato
## Tag Etsy Validati
## Performance Storica
| Data | Views | Vendite | Revenue | Note |
|---|---|---|---|---|
## Learnings
## Connessioni

REGOLE (non negoziabili):
- Aggiorna sezioni esistenti — non duplicare informazioni già presenti
- Performance: max 12 righe. Oltre: consolida le più vecchie in "[YYYY-MM — YYYY-MM] media views X, vendite Y"
- Learnings: max 7 bullet. Sostituisci i meno distinti, non aggiungerne di nuovi
- Connessioni: usa [[slug-nicchia]] syntax, max 5 link
- Tag Validati: max 13 tag
- File sotto {token_limit} token — compatta se necessario
- summary: aggiornalo sempre per riflettere lo stato corrente
"""

_COMPILE_WIKI_FILE_SYSTEM = """\
Sei WikiManager di AgentPeXI. Aggiorna un file wiki markdown con nuove informazioni.
Restituisci SOLO il file markdown completo, nessun commento.

REGOLE:
- Aggiorna sezioni esistenti — non aggiungere se il contenuto è già rappresentato
- Se il file ha frontmatter YAML, aggiorna last_updated e summary
- File sotto {token_limit} token — compatta se necessario
"""

_QUERY_PASS1_SYSTEM = """\
Sei WikiManager di AgentPeXI. Hai un indice wiki con i summary di ogni file.
Identifica quali file sono rilevanti per la query.

Rispondi con JSON puro (nessun wrapper), questo schema esatto:
{
  "relevant_files": ["dominio/sottocartella/file.md"],
  "sufficient_from_summaries": true,
  "quick_answer": "risposta breve se sufficient=true, altrimenti null"
}
"""

_QUERY_PASS2_SYSTEM = """\
Sei WikiManager di AgentPeXI. Sintetizza le informazioni dai file wiki rilevanti.
Produci testo strutturato da iniettare nel system prompt dell'orchestratore.
Max 600 token. Evidenzia dati pratici: prezzi, tag, performance, learnings.
NON inventare dati non presenti nei file.
"""

_LINT_SYSTEM = """\
Sei WikiManager di AgentPeXI. Analizza la wiki e identifica:
1. File con sezioni mancanti o vuote (<!-- --> o tabelle senza righe)
2. [[wikilink]] rotti (puntano a file non esistenti)
3. File non aggiornati da >30 giorni con dati potenzialmente stale
4. Raw non compilati nel manifest (backlog)
5. Suggerimenti per nuovi articoli basati su pattern nei dati esistenti

Rispondi in testo strutturato conciso — priorità ai problemi bloccanti.
"""

_DISTILL_SYSTEM = """\
Sei WikiManager di AgentPeXI. Il file wiki allegato ha superato il limite dimensione.
Distilla il contenuto mantenendo solo le informazioni più rilevanti e actionable.
Restituisci SOLO il file markdown distillato con frontmatter aggiornato, nessun commento.

REGOLE:
- Mantieni tutto il frontmatter, aggiorna last_updated
- Performance: consolida righe >12 mesi fa in riga summary
- Learnings: tieni i 5-7 più actionable, elimina i ridondanti
- Non perdere prezzi reali o tag validati da dati Analytics
- Target: circa {target_tokens} token
"""

# ── Helpers ───────────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Estrae campi scalari dal frontmatter YAML senza PyYAML."""
    m = _FM_RE.match(text)
    if not m:
        return {}
    fields: dict[str, Any] = {}
    for key, val in _FM_FIELD_RE.findall(m.group(1)):
        fields[key] = val.strip().strip('"').strip("'")
    return fields


def _estimate_tokens(text: str) -> int:
    """Stima token senza tiktoken: len(parole) * 1.33."""
    return int(len(text.split()) * 1.33)
