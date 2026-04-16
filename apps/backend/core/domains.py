"""Domain context per Pepe — separa identità (invariante) da obiettivo/comportamento (per dominio).

Domini disponibili:
- DOMAIN_ETSY    — Gestione store Etsy (pipeline research → design → publisher)
- DOMAIN_PERSONAL — Assistente personale Andrea (Ollama locale, privacy totale, costo zero)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainContext:
    """
    Descrive un dominio operativo per Pepe.
    Separa: identità (invariante) / obiettivo (per dominio) / comportamento (per dominio).
    Per aggiungere un nuovo dominio: crea una nuova istanza DomainContext.
    Pepe non richiede modifiche.
    """
    # --- Prompt ---
    name: str
    objective: str                          # frase obiettivo nel system prompt
    business_rules: list[str]               # regole NON negoziabili
    agents: dict[str, str]                  # nome → descrizione schema input
    extra_sections: dict[str, str] = field(default_factory=dict)  # sezioni opzionali

    # --- Comportamento ---
    confidence_threshold: float = 0.85      # soglia per autonomia (_apply_confidence_gate)
    confidence_disclaimer: float = 0.60     # soglia per disclaimer (sotto = block)
    pipeline_steps: list[str] = field(default_factory=list)  # sequenza agenti
    learning_triggers: dict[str, str] = field(default_factory=dict)  # segnale → azione
    clarification_questions: list[str] = field(default_factory=list)  # domande contesto


# ---------------------------------------------------------------------------
# Dominio Etsy — unico dominio attivo al momento
# ---------------------------------------------------------------------------

DOMAIN_ETSY = DomainContext(
    name="etsy_store",

    objective=(
        "Vendere prodotti digitali su Etsy generando revenue reale per Andrea. "
        "Ogni decisione deve essere orientata a massimizzare le vendite effettive, "
        "non solo la qualità tecnica del prodotto."
    ),

    business_rules=[
        "Prezzo minimo accettabile: $2.99 (sotto questa soglia il margine è negativo)",
        "Prezzo sweet spot entry: $3.99–$7.99 | bundle: $9.99–$14.99",
        "Non pubblicare mai un listing senza thumbnail verificati",
        "Non pubblicare mai una niche con viable=False da Research Agent",
        "Confidence < 0.60 blocca l'avanzamento della pipeline — non bypassare mai",
        "I tag Etsy provengono SEMPRE da etsy_tags_13 di Research — mai generati da zero",
        "Massimo 5 task paralleli in pipeline (Semaphore(5))",
    ],

    agents={
        "research": (
            'input: {"niches": ["niche1", "niche2"], "product_type": "pdf|png|svg"} '
            '— analizza domanda, competizione, pricing, produce etsy_tags_13 e selling_signals'
        ),
        "design": (
            'input: {"product_type": "...", "niche": "...", "research_context": {...}} '
            '— genera PDF/PNG/SVG con template e preset ottimali'
        ),
        "publisher": (
            'input: {"product_type": "...", "niche": "...", "research_context": {...}, '
            '"design_output": {...}} '
            '— pubblica su Etsy con SEO, pricing e thumbnail verificati'
        ),
        "analytics": (
            'input: {} '
            '— sync stats Etsy, failure analysis, bestseller proposals'
        ),
    },

    extra_sections={
        "Stagionalità Etsy": (
            "Gennaio–Febbraio: planner annuali, habit tracker, goal setting. "
            "Marzo–Maggio: spring cleaning, organizzatori, wedding planning. "
            "Giugno–Agosto: summer tracker, vacation planner, back to school (agosto). "
            "Settembre–Ottobre: fall planner, budget autunnale, Q4 prep. "
            "Novembre–Dicembre: gift guide, holiday planner, year in review. "
            "Considera sempre il mese corrente nel valutare la priorità delle niche."
        ),
    },

    confidence_threshold=0.85,
    confidence_disclaimer=0.60,

    pipeline_steps=["research", "design", "publisher"],

    learning_triggers={
        "no_views":      "fix_tags",        # Research Agent: ottimizza etsy_tags_13
        "no_conversion": "fix_pricing",     # Publisher Agent: aggiorna prezzo
        "bestseller":    "propose_variant", # Pepe propone variante a Andrea
    },

    clarification_questions=[
        "Per quale nicchia o categoria di prodotto vuoi procedere?",
        "Che tipo di file preferisci? (PDF stampabile, PNG, SVG bundle)",
        "Hai già un'idea di prezzo target o lasci decidere al sistema?",
        "Vuoi privilegiare volume (più listing a prezzo basso) "
        "o margine (meno listing a prezzo premium)?",
    ],
)


# ---------------------------------------------------------------------------
# Dominio Personal — assistente personale Andrea
# LLM: Ollama locale (qwen3:4b) — nessuna API key, privacy totale, costo zero
# Attivazione: /personal su Telegram | Disattivazione: /etsy
# ---------------------------------------------------------------------------

DOMAIN_PERSONAL = DomainContext(
    name="personal",

    objective=(
        "Supporto quotidiano ad Andrea: memoria contestuale dello schermo, "
        "gestione reminder, ricerca personale, accesso a Notion/Gmail/Calendar/File. "
        "Priorità: risposta rapida, privacy totale, costo zero."
    ),

    business_rules=[
        "I dati schermo non escono mai dal Mac — solo Ollama locale li processa",
        "Operazioni irreversibili (invia email, cancella file, modifica evento) "
        "richiedono conferma esplicita su Telegram prima dell'esecuzione",
        "FileAgent opera solo dentro le directory in PERSONAL_ALLOWED_DIRS — mai fuori",
        "Confidence < 0.60 blocca l'esecuzione — stessa soglia di DOMAIN_ETSY",
        "API key locale obbligatoria per tutti gli endpoint /api/personal/* e /api/screen/*",
    ],

    agents={
        "recall": (
            'input: {"query": "...", "time_from": "ISO8601|null", "time_to": "ISO8601|null"} '
            '— similarity search ChromaDB screen_memory con filtro temporale'
        ),
        "remind": (
            'input: {"message": "...", "when": "stringa data naturale"} '
            '— crea job APScheduler DateTrigger, notifica Telegram alla scadenza'
        ),
        "summarize": (
            'input: {"content": "...", "style": "bullet|prose|short"} '
            '— sintetizza testo lungo con Ollama'
        ),
        "research_personal": (
            'input: {"query": "...", "depth": "quick|deep"} '
            '— Tavily search + sintesi Ollama, nessun contesto business'
        ),
        "file": (
            'input: {"operation": "read|write|move|search|list", "path": "...", "content": "..."} '
            '— operazioni filesystem dentro PERSONAL_ALLOWED_DIRS'
        ),
        "notion": (
            'input: {"operation": "read|create|update|search", "page_id": "...", "content": "..."} '
            '— Notion API diretta, OAuth token da .env'
        ),
        "gmail": (
            'input: {"operation": "read|search|draft", "query": "...", "draft": {...}} '
            '— Gmail API diretta. Invio: solo con conferma esplicita Telegram'
        ),
        "calendar": (
            'input: {"operation": "read|create", "event": {...}, "range": "..."} '
            '— Calendar API diretta. Modifica/cancellazione: solo con conferma esplicita'
        ),
    },

    confidence_threshold=0.90,
    confidence_disclaimer=0.60,

    pipeline_steps=[],   # nessuna pipeline automatica — tutto su richiesta

    learning_triggers={},

    clarification_questions=[
        "Vuoi cercare qualcosa che hai visto di recente?",
        "Hai bisogno di un reminder o una nota?",
        "Stai cercando qualcosa su Notion, Gmail o Calendar?",
    ],
)
