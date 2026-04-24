"""Pepe — Orchestratore principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal

import anthropic
import openai

from apps.backend.core.config import MODEL_SONNET, MODEL_HAIKU, settings
from apps.backend.core.domains import DomainContext, DOMAIN_ETSY, PersonalLayer, PERSONAL_LAYER
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentStatus, AgentTask, TaskStatus
from apps.backend.agents.base import AgentBase

logger = logging.getLogger("agentpexi.pepe")

# ------------------------------------------------------------------
# Tool definition per delega agenti (Anthropic tool_use)
# Descrizione base — il tool completo viene costruito on-demand da
# Pepe._build_delegation_tool() usando le AgentCard registrate.
# ------------------------------------------------------------------

DELEGATION_BASE_DESCRIPTION = (
    "Delega un task a un agente specializzato. "
    "Usalo SEMPRE quando l'utente chiede di creare prodotti, fare ricerca di mercato, "
    "pubblicare listing, analizzare performance o generare report finanziari. "
    "NON rispondere in prosa descrivendo cosa faresti — delega direttamente. "
    "REGOLA PIPELINE: per avviare una pipeline, creare un prodotto o analizzare una nicchia, "
    "delega SEMPRE a 'research' come primo step — mai ad analytics o altri agenti. "
    "'analytics' si usa SOLO quando l'utente chiede esplicitamente statistiche "
    "o performance di listing già pubblicati."
)


# ------------------------------------------------------------------
# Urgency system — costanti
# ------------------------------------------------------------------

_NOISE_APPS: frozenset[str] = frozenset({
    "Spotify", "Music", "Apple Music", "Netflix", "YouTube", "Prime Video",
    "Steam", "Minecraft", "IINA", "VLC", "Podcasts", "Audible",
    "Disney+", "Twitch", "Discord",
})

_NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*[\d\s\W]{0,15}\s*$"),   # solo numeri/simboli
    re.compile(r"^.{0,9}$"),                   # meno di 10 caratteri
]

# Pattern che fanno auto-invoke Recall senza passare dal confidence gate
_RECALL_PATTERN = re.compile(
    r"(cosa|quando|dove).{0,20}(stav[oa]|ho\s+(visto|letto|aperto|cercato)|"
    r"guardav[oa]|leggev[oa]|facev[oa]|usav[oa]|era\s+aperto)",
    re.IGNORECASE,
)

# Pattern per rilevare intent personal in messaggi misti (§4.P5)
PERSONAL_INTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(ricord[ai]mi|reminder|promemoria|avvisami|mettimi\s+un)\b", re.I), "remind"),
    (re.compile(r"\b(cosa\s+stav[oa]|ho\s+(visto|letto|aperto|cercato|usato))\b", re.I), "recall"),
    (re.compile(r"\b(riassumi|summarize|sintetizza|fammi\s+un\s+riassunto)\b", re.I), "summarize"),
    (re.compile(r"\b(gmail|mail\b|manda\s+un[a']?\s+mail|scrivi\s+(a|ad)\s+\w+)\b", re.I), "gmail"),
    (re.compile(r"\b(notion|appunta|salva\s+(su|in)\s+notion)\b", re.I), "notion"),
    (re.compile(r"\b(calendario|agenda|appuntamento|crea\s+un\s+evento)\b", re.I), "calendar"),
    (re.compile(r"\b(cerca|ricerca|dimmi).{0,20}\b(personale|per\s+me|mio)\b", re.I), "research_personal"),
]

# Prompt Ollama caveman per classificazione urgenza
_URGENCY_SYSTEM = (
    "Rate urgency. Output ONLY this format:\n"
    "LEVEL: HIGH|MEDIUM|LOW\n"
    "REASON: max 8 words, italian\n"
    "---\n"
    "HIGH = azione richiesta oggi, scadenza, finanziario, medico\n"
    "MEDIUM = info utile, nessuna azione immediata\n"
    "LOW = intrattenimento, navigazione generica, rumore"
)


def _format_analytics_summary(output: dict) -> str:
    """Formatta il report analytics in formato compatto (identico al messaggio Telegram)."""
    from datetime import date as _date
    date_str = output.get("date", _date.today().isoformat())
    total_views = output.get("total_views", 0)
    total_fav   = output.get("total_favorites", 0)
    total_sales = output.get("total_sales", 0)
    total_rev   = output.get("total_revenue_eur", 0.0)
    delta       = output.get("delta_views_vs_yesterday", 0)
    active      = output.get("total_listings_active", 0)
    drafts      = output.get("drafts", 0)
    failures    = output.get("failures", {})

    delta_sign = f"+{delta}" if delta >= 0 else str(delta)

    bestsellers = output.get("bestsellers", [])
    if bestsellers:
        bs = bestsellers[0]
        bs_line = f"{bs.get('title', '')[:40]} ({bs.get('sales', 0)} vendite)"
    else:
        bs_line = "nessuno"

    ab = output.get("ab_performance", {})
    ab_winner = ab.get("winner")
    if ab_winner and ab_winner != "inconclusive":
        ab_line = f"A/B: variante {ab_winner} vince ({ab.get('winner_confidence', '')} confidence)\n"
    elif ab_winner == "inconclusive":
        ab_line = "A/B: dati insufficienti\n"
    else:
        ab_line = ""

    tot_failures = sum(v for v in failures.values() if isinstance(v, int))
    failure_detail = ""
    if tot_failures:
        parts = []
        if failures.get("no_views"):
            parts.append(f"{failures['no_views']} senza views >7gg")
        if failures.get("no_conversion"):
            parts.append(f"{failures['no_conversion']} senza conversioni >45gg")
        if parts:
            failure_detail = f"Da ottimizzare: {', '.join(parts)}\n"

    return (
        f"Etsy — {date_str}\n"
        f"{'─' * 14}\n"
        f"Views: {total_views} ({delta_sign} vs ieri)  |  Favorites: {total_fav}\n"
        f"Vendite: {total_sales}  |  Revenue: €{total_rev:.2f}\n"
        f"Listing attivi: {active}  |  Bozze: {drafts}\n"
        f"{ab_line}"
        f"Bestseller: {bs_line}\n"
        f"{failure_detail}"
    ).rstrip()



class Pepe:
    """Orchestratore centrale: gestisce queue, agenti, e interazione utente."""

    def __init__(
        self,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        active_domain: DomainContext | None = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self.domain = active_domain
        self._business_domain: DomainContext | None = None
        self._personal_layer: PersonalLayer = PERSONAL_LAYER
        self._agent_cards: dict[str, AgentCard] = {}

        # Anthropic client (Etsy domain — Sonnet)
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        # Ollama client (Personal domain — local, zero cost)
        self._local_client = openai.AsyncOpenAI(
            base_url=settings.OLLAMA_BASE_URL,
            api_key="ollama",  # placeholder — Ollama non richiede auth
        )

        # Agent registry: {name: AgentBase instance}
        self._agents: dict[str, AgentBase] = {}
        self._agent_status: dict[str, AgentStatus] = {}

        # Task queue + semaforo parallelismo
        self._queue: asyncio.Queue[AgentTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(settings.MAX_PARALLEL_TASKS)

        # Futures per attendere risultati dei task
        self._pending_futures: dict[str, asyncio.Future[AgentResult]] = {}

        # Worker tasks
        self._workers: list[asyncio.Task] = []

        # Callback notifiche Telegram (impostato dal bot module)
        self._telegram_notifier: Callable[[str, bool], Coroutine] | None = None
        self._reminder_notifier: Callable[[str], Coroutine] | None = None

        # Mock mode — attivabile via /mock Telegram
        self.mock_mode: bool = False

        # Urgency system — stato runtime
        self._last_watcher_app: str = ""
        self._urgency_medium_buffer: list[dict] = []
        self._medium_buffer_lock = asyncio.Lock()

    def _has_business_domain(self) -> bool:
        return self._business_domain is not None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self, num_workers: int = 3) -> None:
        """Avvia i worker della queue."""
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"pepe-worker-{i}")
            self._workers.append(task)
        logger.info("Pepe avviato con %d worker", num_workers)

    async def stop(self) -> None:
        """Ferma i worker gracefully."""
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Pepe fermato")

    def _fire(self, coro: "Coroutine[Any, Any, Any]", name: str = "") -> asyncio.Task:
        """Schedula una coroutine fire-and-forget con logging delle eccezioni."""
        task = asyncio.create_task(coro, name=name or coro.__qualname__)
        task.add_done_callback(
            lambda t: logger.error("Background task '%s' fallito: %s", task.get_name(), t.exception())
            if not t.cancelled() and t.exception() else None
        )
        return task

    # ------------------------------------------------------------------
    # LLM wrapper tracciato — retry + cost logging + WebSocket
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> float:
        """Stima costo USD (mirror di AgentBase._estimate_cost)."""
        if "sonnet" in model:
            cost = (
                input_tokens * settings.LLM_SONNET_INPUT_PRICE
                + output_tokens * settings.LLM_SONNET_OUTPUT_PRICE
            ) / 1_000_000
            cost += (
                cache_read * settings.LLM_SONNET_CACHE_READ_PRICE
                + cache_write * settings.LLM_SONNET_CACHE_WRITE_PRICE
            ) / 1_000_000
        elif "haiku" in model:
            cost = (
                input_tokens * settings.LLM_HAIKU_INPUT_PRICE
                + output_tokens * settings.LLM_HAIKU_OUTPUT_PRICE
            ) / 1_000_000
            cost += (
                cache_read * settings.LLM_HAIKU_CACHE_READ_PRICE
                + cache_write * settings.LLM_HAIKU_CACHE_WRITE_PRICE
            ) / 1_000_000
        else:
            cost = (
                input_tokens * settings.LLM_SONNET_INPUT_PRICE
                + output_tokens * settings.LLM_SONNET_OUTPUT_PRICE
            ) / 1_000_000
        return round(cost, 6)

    async def _pepe_llm_call(
        self,
        model: str,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        label: str = "pepe.routing",
    ) -> Any:
        """Wrapper Anthropic tracciato per le chiamate interne di Pepe.

        Garantisce: retry su 429/529, log in llm_calls, cost tracking,
        evento WebSocket — identico a AgentBase._call_llm.

        Usare questo invece di self.client.messages.create() direttamente.
        """
        import time as _time

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        t0 = _time.monotonic()
        last_exc: Exception | None = None
        response = None
        for attempt in range(3):
            try:
                response = await self.client.messages.create(**kwargs)
                break
            except anthropic.RateLimitError as exc:
                last_exc = exc
                await asyncio.sleep(2 ** attempt)
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529:
                    last_exc = exc
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        if response is None:
            raise last_exc  # type: ignore[misc]

        duration_ms = int((_time.monotonic() - t0) * 1000)
        usage = response.usage
        input_tokens  = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read    = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write   = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost_usd      = self._estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)

        # Log in llm_calls (stessa tabella degli agenti → cost dashboard completo)
        try:
            await self.memory.log_llm_call(
                task_id=None,
                step_id=None,
                agent_name=label,
                model=model,
                system_prompt=system,
                messages=messages,
                response="<structured>",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                provider="anthropic",
            )
        except Exception as exc:
            logger.warning("_pepe_llm_call: log_llm_call fallito: %s", exc)

        # Evento WebSocket → cost dashboard live
        if self._ws_broadcast:
            try:
                await self._ws_broadcast({
                    "type": "llm_call",
                    "agent": label,
                    "task_id": None,
                    "model": model,
                    "provider": "anthropic",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                    "duration_ms": duration_ms,
                })
            except Exception:
                pass

        logger.debug(
            "_pepe_llm_call [%s]: model=%s in=%d out=%d cost=$%.5f dur=%dms",
            label, model, input_tokens, output_tokens, cost_usd, duration_ms,
        )
        return response

    # ------------------------------------------------------------------
    # Registrazione agenti
    # ------------------------------------------------------------------

    def register_agent(self, name: str, agent: AgentBase) -> None:
        self._agents[name] = agent
        self._agent_status[name] = AgentStatus.IDLE
        # Indicizza la card per lookup rapido
        if hasattr(agent, 'card'):
            self._agent_cards[name] = agent.card
        logger.info("Agente registrato: %s (layer=%s, llm=%s)",
                    name,
                    getattr(agent, 'card', {}).layer if hasattr(agent, 'card') else 'unknown',
                    getattr(agent, 'card', {}).llm if hasattr(agent, 'card') else 'unknown')

    def get_agent_statuses(self) -> dict[str, str]:
        return {name: status.value for name, status in self._agent_status.items()}

    def resume_agent(self, name: str) -> bool:
        """Riattiva un agente sospeso per troppi errori."""
        if name in self._agent_status and self._agent_status[name] == AgentStatus.ERROR:
            self._agent_status[name] = AgentStatus.IDLE
            logger.info("Agente %s riattivato", name)
            return True
        return False

    def _get_agent_llm(self, agent_name: str) -> Literal['ollama', 'sonnet', 'haiku']:
        card = self._agent_cards.get(agent_name)
        return card.llm if card else 'sonnet'   # fallback sicuro

    def _agent_requires_clarification(self, agent_name: str, input_data: dict) -> list[str]:
        """Ritorna lista di campi mancanti richiesti dall'agente."""
        card = self._agent_cards.get(agent_name)
        if not card or not card.requires_clarification:
            return []
        return [f for f in card.requires_clarification if not input_data.get(f)]

    def _agent_requires_confirmation(self, agent_name: str) -> bool:
        card = self._agent_cards.get(agent_name)
        return card.requires_confirmation if card else False

    def _build_delegation_tool(self) -> tuple[dict, dict]:
        """Costruisce DELEGATION_TOOL e DELEGATION_TOOL_OAI dalle AgentCard registrate.
        Versione finale — supera l'implementazione in §4.P4 che usava _personal_layer.agents.
        """
        personal_pairs = [
            (name, card) for name, card in self._agent_cards.items()
            if card.layer == 'personal'
        ]
        business_pairs = [
            (name, card) for name, card in self._agent_cards.items()
            if card.layer == 'business'
            and (self._has_business_domain() and name in (self._business_domain.agents or self._agent_cards))
        ] if self._has_business_domain() else []

        # FALLBACK per agenti registrati senza card (transizione Step 3-7)
        # Agenti senza card ma in _business_domain.agents vengono inclusi senza descrizione
        if self._has_business_domain():
            card_names = {name for name, _ in business_pairs}
            for name in (self._business_domain.agents or {}):
                if name not in card_names and name in self._agents:
                    business_pairs.append((name, None))  # None = no card, agente legacy

        all_names = [name for name, _ in personal_pairs] + [name for name, _ in business_pairs]

        personal_desc = ", ".join(name for name, _ in personal_pairs)
        enum_desc = f"Utilità personal (sempre disponibili): {personal_desc}."
        if business_pairs:
            business_desc = ", ".join(name for name, _ in business_pairs)
            pipeline = " → ".join(self._business_domain.pipeline_steps or [])
            enum_desc += f" Agenti business (solo contesto Etsy): {business_desc}."
            if pipeline:
                enum_desc += f" Pipeline obbligatoria: {pipeline}."

        properties = {
            "delegate": {"type": "string", "enum": all_names, "description": enum_desc},
            "input": {"type": "object", "description": "Parametri per l'agente."},
            "task_type": {"type": "string"},
        }
        required = ["delegate", "input"]

        # Formato Anthropic
        tool = {
            "name": "delegate_to_agent",
            "description": DELEGATION_BASE_DESCRIPTION,
            "input_schema": {"type": "object", "properties": properties, "required": required},
        }
        # Formato OpenAI-compat (Ollama)
        tool_oai = {
            "type": "function",
            "function": {
                "name": "delegate_to_agent",
                "description": DELEGATION_BASE_DESCRIPTION,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }
        return tool, tool_oai

    # ------------------------------------------------------------------
    # System prompt — prompt misto personal + business (§10.3)
    # ------------------------------------------------------------------

    def _is_personal_intent(self, message: str) -> bool:
        return any(p.search(message) for p, _ in PERSONAL_INTENT_PATTERNS)

    def _build_system_prompt(self, last_message: str = "") -> str:
        """
        Costruisce il system prompt con personal layer sempre presente
        e business layer condizionale. Ordine sezioni adattivo per intent.
        """
        is_personal = self._is_personal_intent(last_message) if last_message else False
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # ─── IDENTITÀ ────────────────────────────────────────────────────────────
        identity = (
            "Sei Pepe, orchestratore di AgentPeXI. Coordini agenti specializzati per "
            "supportare Andrea nelle sue attività. Hai accesso a due livelli di capacità: "
            "utilità personali (sempre disponibili) e agenti di dominio business "
            "(attivi se un dominio è selezionato)."
        )

        # ─── OBIETTIVO ───────────────────────────────────────────────────────────
        if self._has_business_domain():
            objective = (
                f"## Obiettivo attuale — {self._business_domain.name}\n"
                f"{self._business_domain.objective}\n\n"
                "Le utilità personali rimangono disponibili per qualsiasi richiesta "
                "di supporto personale, indipendentemente dal contesto business."
            )
        else:
            objective = (
                "## Obiettivo\n"
                "Supporto personale ad Andrea. Usa le utilità disponibili su sua richiesta "
                "esplicita. Non avviare pipeline automatiche."
            )

        # ─── LAYER PERSONAL ──────────────────────────────────────────────────────
        personal_agents_list = ""
        for name, card in self._agent_cards.items():
            if card.layer == "personal":
                personal_agents_list += f"- **{name}**: {card.description}\n  input: {card.input_schema}\n"

        personal_names = ", ".join(
            name for name, card in self._agent_cards.items() if card.layer == "personal"
        )
        personal_section = (
            "## LIVELLO PERSONALE — sempre attivo\n"
            f"Agenti: {personal_names}.\n"
            "SEMPRE disponibili. Anche se dominio Etsy è attivo.\n"
            "NON sono pipeline. NON hanno regole Etsy.\n"
            "Chiamali subito. Non aspettare altri step.\n\n"
            f"{personal_agents_list}"
        )

        # ─── LAYER BUSINESS ──────────────────────────────────────────────────────
        business_section = ""
        pipeline_section = ""
        rules_section = ""
        wiki_section = ""
        seasonality_section = ""

        if self._has_business_domain():
            d = self._business_domain
            business_agents_list = ""
            for name in (d.agents.keys() if d.agents else []):
                card = self._agent_cards.get(name)
                if card:
                    business_agents_list += f"- **{name}**: {card.description}\n  input: {card.input_schema}\n"
                elif name in d.agents:
                    business_agents_list += f"- **{name}**: {d.agents[name]}\n"

            business_names = ", ".join(
                name for name, card in self._agent_cards.items() if card.layer == "business"
            )
            business_section = (
                f"## LIVELLO BUSINESS — {d.name}\n"
                f"Agenti: {business_names}.\n"
                "Solo per task Etsy. Seguono pipeline. Seguono regole business.\n\n"
                f"{business_agents_list}"
            )

            if d.pipeline_steps:
                steps_str = " → ".join(d.pipeline_steps)
                pipeline_section = (
                    f"## PIPELINE OBBLIGATORIA — {d.name}\n"
                    f"Ordine: {steps_str}\n"
                    f"PRIMO step è SEMPRE: {d.pipeline_steps[0]}.\n"
                    "NON saltare step.\n"
                    "NON chiamare design senza output di research.\n"
                    "NON chiamare publisher senza output di design.\n"
                    f"PIPELINE = solo agenti business. NON vale per {personal_names}."
                )

            if d.business_rules:
                rules_list = "\n".join(f"- {r}" for r in d.business_rules)
                rules_section = (
                    "## REGOLE BUSINESS\n"
                    f"{rules_list}\n\n"
                    f"ATTENZIONE: queste regole valgono SOLO per {business_names}.\n"
                    f"NON valgono per {personal_names}."
                )

            if d.extra_sections:
                seasonality_section = "\n\n".join(
                    f"## {title}\n{body}"
                    for title, body in d.extra_sections.items()
                )

            if hasattr(self, "_wiki") and self._wiki:
                wiki_section = f"## Contesto wiki — {d.name}\n{self._wiki}"

        # ─── DISAMBIGUAZIONE — caveman-style ─────────────────────────────────────
        disambiguation = """## REGOLA SCELTA AGENTE

Parola chiave nel messaggio → agente corretto:

"ricordami" / "reminder" / "avvisami" → remind (action='create')
"leggimi i reminder" / "mostrami i promemoria" / "reminder più recente" / "cosa ho in agenda" / "dimmi i promemoria" / "quali reminder" / "lista reminder" → remind (action='list')
"cosa ho visto" / "cosa ho cercato" / "cosa ho fatto" / "ho aperto" → recall
"riassumi" / "sintetizza" / "fammi un riassunto" → summarize
"gmail" / "mail" / "manda una mail" / "scrivi a" → gmail
"notion" / "appunta" / "salva su notion" → notion
"calendario" / "appuntamento" / "crea evento" → calendar
"nicchia" / "niche" / "listing" / "pubblica" / "bestseller" / "tag Etsy" → research (poi pipeline)
"analisi vendite" / "stats" / "quante views" → analytics
"costi" / "revenue" / "margine" / "ROI" → finance

REGOLA: "cerca" / "ricerca" DA SOLO non basta.
  "cerca nicchie" → research (ha "nicchie")
  "cerca info su X" / "cerca come funziona Y" → research_personal
  "ricordami di fare ricerca" → remind (è un promemoria)

REGOLA DEFAULT: dubbio tra personal e business → scegli personal.
Business si attiva SOLO se messaggio menziona: nicchie, listing, Etsy store, vendite, prodotti digitali, pipeline.

ESEMPI:
"ricordami di fare ricerca su botanical art" → remind action='create' (NON research)
"leggimi il reminder più recente" → remind action='list'
"ricordami quella cosa di prima" → remind action='list' (vuole LEGGERE, non creare)
"quali sono i miei reminder?" → remind action='list'
"cerca nicchie botanical art su Etsy" → research
"cosa ho guardato ieri?" → recall
"analisi vendite settimana" → analytics
"riassumi questo articolo" → summarize
"aggiungi nota Notion: pipeline ok" → notion (NON pipeline business)
"cerca come funziona algoritmo Etsy" → research_personal (NON research)"""

        # ─── STATO SISTEMA ───────────────────────────────────────────────────────
        status_lines = "\n".join(
            f"- {name}: {status.value}"
            for name, status in self._agent_status.items()
        )
        system_state = f"## Stato sistema — {now_str}\n{status_lines}"

        # ─── ASSEMBLAGGIO — ordine adattivo ──────────────────────────────────────
        blocks = [identity, objective]

        if is_personal:
            # Intent personal rilevato: personal prima, business dopo (se presente)
            blocks.append(personal_section)
            if business_section:
                blocks += [business_section, pipeline_section, rules_section]
        else:
            # Intent business o neutro: business prima (se presente), personal dopo
            if business_section:
                blocks += [business_section, pipeline_section, rules_section]
            blocks.append(personal_section)

        blocks += [disambiguation, system_state]

        # Sezioni extra solo se business attivo
        if seasonality_section:
            blocks.append(seasonality_section)
        if wiki_section:
            blocks.append(wiki_section)

        # Rimuovere blocchi vuoti
        return "\n\n".join(b for b in blocks if b.strip())

    # ------------------------------------------------------------------
    # Entry point — messaggio utente
    # ------------------------------------------------------------------

    async def _llm_simple_call(
        self,
        system: str,
        user_content: str,
        max_tokens: int = 512,
        use_haiku: bool = False,
        agent_name: str | None = None,
    ) -> str:
        """Chiamata LLM single-turn senza tools, routed per agente e dominio.

        Ollama se:  nessun business domain attivo
                 OR agent_name è un agente personal (layer=personal da AgentCard)
        Anthropic altrimenti (Haiku se use_haiku=True, Sonnet altrimenti).
        """
        # Routing: sempre Anthropic (Ollama rimosso — inaffidabile su hardware corrente).
        # Haiku per agenti personal e chiamate leggere, Sonnet per business.
        _personal_names = {
            name for name, card in self._agent_cards.items()
            if card.layer == "personal"
        }
        use_haiku = use_haiku or (
            not self._has_business_domain()
            or (agent_name is not None and agent_name in _personal_names)
        )
        if True:  # sempre Anthropic
            model = MODEL_HAIKU if use_haiku else MODEL_SONNET
            try:
                resp = await self._pepe_llm_call(
                    model=model,
                    system=system,
                    messages=[{"role": "user", "content": user_content}],
                    max_tokens=max_tokens,
                    label=f"pepe.simple/{agent_name or 'unknown'}",
                )
                return resp.content[0].text if resp.content else ""
            except Exception:
                return ""

    async def _llm_decide(
        self,
        history: list[dict],
        system: str,
        message: str = "",
    ) -> tuple[dict | None, str]:
        """Chiama il LLM corretto in base al dominio attivo e all'intent del messaggio.

        Routing a 3 vie (§4.P5 — aggiornato: Ollama rimosso dalla rotta routing):
        - nessun business domain → Haiku (affidabile per tool calling, economico)
        - business attivo + intent personal → Haiku (fallback Sonnet)
        - business attivo + intent business/neutro → Sonnet

        Motivazione: qwen3:8b locale produceva risposte vuote al tool calling,
        bloccando ogni delega ad agenti. Haiku è 100% affidabile, ~0.001€/call.

        Returns:
            (delegation, reply_text) — delegation è None se risposta diretta.
        """
        _tool, _tool_oai = self._build_delegation_tool()

        if not self._has_business_domain():
            # Nessun business domain → Haiku (personal assistant, tool calling affidabile)
            return await self._llm_decide_anthropic(history, system, _tool, model=MODEL_HAIKU)
        elif self._is_personal_intent(message):
            # Business attivo ma intento chiaramente personal → Haiku, fallback Sonnet
            try:
                return await self._llm_decide_anthropic(history, system, _tool, model=MODEL_HAIKU)
            except Exception:
                return await self._llm_decide_anthropic(history, system, _tool)
        else:
            # Business attivo, intento business → Claude Sonnet
            return await self._llm_decide_anthropic(history, system, _tool)

    async def _llm_decide_ollama(
        self,
        history: list[dict],
        system: str,
        tool_oai: dict,
    ) -> tuple[dict | None, str]:
        """Chiamata Ollama con delegation tool OpenAI-compat."""
        oai_messages = [{"role": "system", "content": system}] + history
        oai_resp = await self._local_client.chat.completions.create(
            model=settings.OLLAMA_MODEL,
            messages=oai_messages,
            tools=[tool_oai],
            tool_choice="auto",
        )
        msg = oai_resp.choices[0].message
        delegation: dict | None = None
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            try:
                delegation = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                logger.warning("_llm_decide_ollama: JSON delegation non parsabile: %s", tc.function.arguments[:200])
        reply_text = msg.content or ""
        logger.debug(
            "_llm_decide_ollama: delegation=%s agent=%s reply_text='%s'",
            bool(delegation),
            delegation.get("delegate") if delegation else None,
            reply_text[:80],
        )
        if not delegation and not reply_text:
            logger.warning("_llm_decide_ollama: Ollama ha prodotto né delegation né reply_text (risposta vuota)")
        return delegation, reply_text

    async def _llm_decide_anthropic(
        self,
        history: list[dict],
        system: str,
        tool: dict | None = None,
        model: str | None = None,
    ) -> tuple[dict | None, str]:
        """Chiamata Anthropic con delegation tool dinamico.

        Args:
            model: modello da usare (default MODEL_SONNET). Passare MODEL_HAIKU
                   per routing economico in contesti personal/no-business.
        """
        _tool = tool if tool is not None else self._build_delegation_tool()[0]
        _model = model if model is not None else MODEL_SONNET
        logger.debug("_llm_decide_anthropic: model=%s", _model)
        response = await self._pepe_llm_call(
            model=_model,
            system=system,
            messages=history,
            max_tokens=2048,
            tools=[_tool],
            label="pepe.routing",
        )
        delegation: dict | None = None
        reply_text = ""
        for block in response.content:
            if block.type == "tool_use" and block.name == "delegate_to_agent":
                delegation = block.input
            elif hasattr(block, "text"):
                reply_text += block.text
        return delegation, reply_text

    async def handle_user_message(
        self, message: str, source: str = "web", session_id: str = "default"
    ) -> str:
        """Gestisce un messaggio utente: risposta diretta o delega ad agente."""
        # Salva messaggio utente nella sessione
        await self.memory.save_message(session_id, "user", message, source)

        # --- Handler "sì/no" per pending_actions (incluso urgency_proposal) ---
        quick_reply = await self._check_pending_action(message, source)
        if quick_reply is not None:
            await self.memory.save_message(session_id, "assistant", quick_reply, source)
            return quick_reply

        # --- RECALL_PATTERN auto-invoke ---
        # Se il messaggio corrisponde a "cosa stavo guardando / cosa ho aperto..."
        # bypassa il gate LLM e delega direttamente a Recall.
        if _RECALL_PATTERN.search(message) and "recall" in self._agents:
            context_hint = f"last_app={self._last_watcher_app}" if self._last_watcher_app else ""
            recall_task = AgentTask(
                agent_name="recall",
                input_data={"query": message, "context": context_hint},
                source=source,
            )
            try:
                result = await self._enqueue_and_wait(recall_task)
                final_reply = await self._apply_confidence_gate(
                    message, "recall", result, session_id, source
                )
            except Exception as exc:
                if source == "orb_voice":
                    final_reply = self._voice_error_phrase(str(exc))
                else:
                    final_reply = await self._synthesize_error("recall", str(exc), {})
                await self.memory.save_message(session_id, "assistant", final_reply, source)
            return final_reply

        # AGGIUNTA 1 — Pipeline context check
        pipeline_summary = await self._get_pipeline_summary()
        analytics_summary = await self._get_recent_analytics_summary()

        # Recupera contesto da ChromaDB
        context_docs = await self.memory.query_insights(message, n_results=3)
        context_text = ""
        if context_docs:
            context_text = "\n\nContesto dalla memoria:\n" + "\n".join(
                f"- {d['document']}" for d in context_docs
            )

        # Conversazione sessione per continuità
        recent = await self.memory.get_conversation_history(session_id, limit=20)
        history = []
        for m in recent:
            if m["role"] == "user":
                history.append({"role": "user", "content": m["content"]})
            elif m["role"] in ("assistant", "pepe"):
                history.append({"role": "assistant", "content": m["content"]})
        # Rimuovi l'ultimo (è il messaggio corrente appena salvato)
        if history and history[-1]["role"] == "user":
            history.pop()

        # Aggiungi messaggio corrente + contesto
        user_content = message
        if context_text:
            user_content += context_text
        history.append({"role": "user", "content": user_content})

        # Wiki context — iniettato su self._wiki per _build_system_prompt (Step 5.2.3)
        # Non blocca: se query fallisce, self._wiki rimane "".
        self._wiki = ""
        if self._has_business_domain() and hasattr(self, "wiki") and self.wiki is not None:
            try:
                self._wiki = await self.wiki.query(
                    self._business_domain.name.lower(), message, self.client
                )
            except Exception as exc:
                logger.warning("wiki.query fallita in handle_user_message: %s", exc)

        # System prompt dinamico — prompt misto personal + business
        system = self._build_system_prompt(last_message=message)

        # Modalità vocale: istruzioni per risposta parlata naturale
        if source == "orb_voice":
            system += (
                "\n\n## MODALITÀ VOCALE — obbligatorio\n"
                "La tua risposta verrà letta ad alta voce da un TTS. "
                "Deve essere ascoltabile senza sembrare troncata.\n"
                "REGOLE ASSOLUTE:\n"
                "- Niente markdown: niente **, ##, *, liste con numeri o trattini\n"
                "- Niente emoji\n"
                "- Italiano parlato naturale, come se stessi rispondendo a voce\n"
                "- Se devi elencare cose, fallo in prosa: 'posso fare X, Y e Z'\n"
                "- Lunghezza: massimo 2-3 frasi COMPLETE. "
                "Non iniziare un elenco che non riesci a finire entro 3 frasi. "
                "Se l'argomento è ampio, dai i punti principali (2-3) e aggiungi "
                "'per i dettagli chiedimi su Telegram' — poi fermati.\n"
                "- Ogni risposta deve terminare con una frase grammaticalmente completa, mai a metà\n"
                "- Non iniziare con 'Certo!', 'Perfetto!', 'Ottima domanda!' — vai dritto al punto"
            )

        # Prima chiamata LLM — decide se delegare o rispondere in testo.
        # Routing: no business domain → Haiku; personal intent → Haiku; else → Sonnet.
        delegation, reply_text = await self._llm_decide(history, system, message=message)

        if delegation:
            agent_name = delegation["delegate"]

            # ── Pre-crea AgentTask — consente clarification formale con INPUT_REQUIRED (§5.2) ──
            # Il task_id viene allocato qui: se serve clarification viene salvato come
            # pending_action correlato; se no, viene subito messo in coda.
            task = AgentTask(
                agent_name=agent_name,
                input_data={
                    **delegation.get("input", {}),
                    "task_type": delegation.get("task_type", "generic"),
                    "_user_message": message,   # testo originale completo — usato da remind per dateparser
                },
                source=source,
            )

            # Clarification loop — derivato da AgentCard.requires_clarification
            _needs_clarify = bool(self._agent_requires_clarification(agent_name, delegation.get("input", {})))
            if not _needs_clarify:
                # Fallback transitorio per agenti senza card ancora registrata
                _needs_clarify = (
                    agent_name in {"remind", "summarize"}
                    or (self._has_business_domain() and agent_name == "research")
                )
            if _needs_clarify:
                clarification = await self._clarify_if_needed(
                    message, delegation, history, system, session_id, source,
                    task=task,  # ← task formale: abilita INPUT_REQUIRED + pending_action correlata
                )
                if clarification is not None:
                    return clarification

            # Verifica duplicati in pipeline (solo business domain)
            if self._has_business_domain() and agent_name == "research":
                duplicate_warning = await self._check_pipeline_duplicate(delegation)
                if duplicate_warning:
                    await self.memory.save_message(session_id, "assistant", duplicate_warning, source)
                    return duplicate_warning

            # AGGIUNTA 3 — Context enrichment (aggiorna task.input_data in-place)
            enriched_input = await self._enrich_task_context(
                agent_name=agent_name,
                base_input=delegation.get("input", {}),
                session_id=session_id,
            )
            task.input_data.update(enriched_input)
            delegation["input"] = enriched_input  # mantieni per coerenza downstream

            # Mette in coda e attende risultato
            try:
                result = await self._enqueue_and_wait(task)
            except Exception as exc:
                if source == "orb_voice":
                    error_reply = self._voice_error_phrase(str(exc))
                else:
                    error_reply = await self._synthesize_error(
                        agent_name, str(exc), task.input_data
                    )
                await self.memory.save_message(session_id, "assistant", error_reply, source)
                return error_reply

            # --- Confidence gate ---
            final_reply = await self._apply_confidence_gate(
                message, agent_name, result, session_id, source
            )
            return final_reply

        # Risposta diretta
        if source == "orb_voice" and reply_text:
            # Canale vocale: strip markdown, tronca a max 1-2 frasi corte
            import re as _re
            _v = _re.sub(r'\*{1,2}|#{1,6}\s*|`{1,3}|\[.*?\]\(.*?\)', '', reply_text)
            _v = _re.sub(r'\s+', ' ', _v).strip()
            # Prendi la prima frase significativa (split su . ! ?)
            _sentences = _re.split(r'(?<=[.!?])\s+', _v)
            _short = ' '.join(_sentences[:2])[:180].strip()
            reply_text = _short or "Non ho capito, puoi ripetere?"
        await self.memory.save_message(session_id, "assistant", reply_text, source)
        return reply_text

    # ------------------------------------------------------------------
    # Task queue
    # ------------------------------------------------------------------

    # Timeout per agente (secondi). Usato da _enqueue_and_wait per evitare
    # worker bloccati a tempo indeterminato. Agenti lenti (research, finance)
    # hanno più margine; agenti rapidi (remind, recall) molto meno.
    _AGENT_TIMEOUTS: dict[str, float] = {
        "remind":            30.0,
        "recall":            30.0,
        "summarize":         90.0,
        "research":         180.0,
        "research_personal": 90.0,
        "analytics":        180.0,
        "finance":          180.0,
        "design":           180.0,
        "publisher":        240.0,
    }
    _AGENT_TIMEOUT_DEFAULT: float = 120.0  # fallback per agenti non in lista

    async def _enqueue_and_wait(self, task: AgentTask) -> AgentResult:
        """Mette il task in coda, crea un Future e attende il risultato.

        Applica un timeout per-agente: se l'agente non risponde entro il limite
        il Future viene pulito e viene sollevato asyncio.TimeoutError (gestito
        dai caller in handle_user_message come errore agente).
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentResult] = loop.create_future()
        self._pending_futures[task.task_id] = future
        await self._queue.put(task)
        timeout = self._AGENT_TIMEOUTS.get(task.agent_name, self._AGENT_TIMEOUT_DEFAULT)
        logger.info(
            "Task %s in coda per agente %s (timeout=%.0fs)",
            task.task_id, task.agent_name, timeout,
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                "Task %s agente '%s' timeout dopo %.0fs — Future cancellato",
                task.task_id, task.agent_name, timeout,
            )
            # Pulizia: rimuovi il future per evitare leak; il worker continuerà
            # ma il risultato verrà scartato (future già rimosso da _pending_futures).
            self._pending_futures.pop(task.task_id, None)
            raise
        except BaseException:
            self._pending_futures.pop(task.task_id, None)
            raise

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker loop: prende task dalla queue e li esegue."""
        logger.info("Worker %d avviato", worker_id)
        while True:
            task = await self._queue.get()
            try:
                async with self._semaphore:
                    result = await self.dispatch_task(task)
                # Risolvi il Future
                future = self._pending_futures.pop(task.task_id, None)
                if future and not future.done():
                    future.set_result(result)
            except Exception as exc:
                logger.error("Worker %d errore task %s: %s", worker_id, task.task_id, exc)
                future = self._pending_futures.pop(task.task_id, None)
                if future and not future.done():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Dispatch — routing + error threshold
    # ------------------------------------------------------------------

    async def dispatch_task(self, task: AgentTask) -> AgentResult:
        """Route task all'agente giusto. Blocca se >3 errori/ora."""
        agent_name = task.agent_name

        if agent_name not in self._agents:
            raise ValueError(f"Agente sconosciuto: {agent_name}")

        # Check soglia errori
        error_count = await self.memory.get_agent_error_count(agent_name, hours=1)
        if error_count > 3:
            self._agent_status[agent_name] = AgentStatus.ERROR
            msg = f"⚠️ Agente {agent_name} sospeso: {error_count} errori nell'ultima ora. Usa /resume_agent {agent_name} per riattivarlo."
            logger.warning(msg)
            await self.notify_telegram(msg, priority=True)
            raise RuntimeError(msg)

        if self._agent_status.get(agent_name) == AgentStatus.ERROR:
            raise RuntimeError(
                f"Agente {agent_name} sospeso. Usa /resume_agent {agent_name} per riattivarlo."
            )

        agent = self._agents[agent_name]
        self._agent_status[agent_name] = AgentStatus.RUNNING

        # Notifica frontend che un agente è partito
        await self._broadcast_context_update(
            next_action=f"await_{agent_name}_output",
            trigger="dispatch",
        )

        try:
            result = await agent.execute(task)
        except Exception:
            self._agent_status[agent_name] = AgentStatus.IDLE
            raise

        self._agent_status[agent_name] = AgentStatus.IDLE
        return result

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    async def retry_task(self, task_id: str | None = None) -> AgentResult:
        """Riprova un task fallito. Se task_id=None, usa l'ultimo fallito."""
        if task_id:
            task_data = await self.memory.get_task_by_id(task_id)
        else:
            task_data = await self.memory.get_last_failed_task()

        if not task_data:
            raise ValueError("Nessun task fallito trovato da riprovare.")

        # Ricostruisci AgentTask dai dati salvati
        new_task = AgentTask(
            agent_name=task_data["agent_name"],
            input_data=task_data.get("input_data") or {},
        )

        logger.info(
            "Retry task %s → nuovo task %s per agente %s",
            task_data["task_id"],
            new_task.task_id,
            new_task.agent_name,
        )

        return await self._enqueue_and_wait(new_task)

    # ------------------------------------------------------------------
    # Notifiche Telegram
    # ------------------------------------------------------------------

    async def has_pending_voice_clarification(self) -> bool:
        """Controlla se la sessione voice_orb ha una clarification in attesa.

        Usato dal WebSocket vocale per decidere se restare in ascolto
        (fase utterance) invece di tornare al wake word.
        """
        action = await self.memory.get_pending_action("clarification")
        return action is not None

    async def notify_telegram(self, message: str, priority: bool = False) -> None:
        """Invia notifica via Telegram se il notifier è configurato."""
        if self._telegram_notifier:
            try:
                await self._telegram_notifier(message, priority)
            except Exception as exc:
                logger.error("Errore notifica Telegram: %s", exc)

    def set_telegram_notifier(self, fn: Callable[[str, bool], Coroutine]) -> None:
        """Registra il callback per notifiche Telegram (chiamato dal bot module)."""
        self._telegram_notifier = fn

    def set_reminder_notifier(self, fn: Callable[[str], Coroutine]) -> None:
        """Registra il callback per reminder — ritorna il message_id Telegram."""
        self._reminder_notifier = fn

    async def send_reminder_notification(self, message: str) -> int:
        """Invia reminder via Telegram e restituisce message_id (per ACK via reply).
        Ritorna 0 se il notifier non è configurato o fallisce."""
        if self._reminder_notifier:
            try:
                return await self._reminder_notifier(message)
            except Exception as exc:
                logger.error("send_reminder_notification fallito: %s", exc)
        return 0

    # ------------------------------------------------------------------
    # Mock mode
    # ------------------------------------------------------------------

    def set_mock_mode(self, value: bool) -> None:
        """Attiva/disattiva mock mode a runtime. Thread-safe (GIL)."""
        self.mock_mode = value
        logger.info("Mock mode: %s", "ON" if value else "OFF")

    def get_mock_mode(self) -> bool:
        return self.mock_mode

    # ------------------------------------------------------------------
    # Domain routing
    # ------------------------------------------------------------------

    def set_active_domain(self, domain: DomainContext | None) -> None:
        """Cambia dominio attivo a runtime. Sticky fino al riavvio o al prossimo switch.

        None → disattiva il business domain (solo personal layer attivo).
        DomainContext → attiva il dominio business specificato.
        """
        if domain is None:
            prev = self._business_domain.name if self._business_domain else "none"
            self._business_domain = None
            logger.info("Business domain disattivato (era: %s)", prev)
        else:
            prev = self._business_domain.name if self._business_domain else "none"
            self._business_domain = domain
            logger.info("Business domain: %s → %s", prev, domain.name)

    def get_active_domain(self) -> DomainContext | None:
        """Restituisce il dominio business attivo, o None se solo personal layer è attivo."""
        return self._business_domain

    # ------------------------------------------------------------------
    # Urgency system — metodi
    # ------------------------------------------------------------------

    def _is_obvious_noise(self, text: str, source: str) -> bool:
        """Pre-filter rapido: True se sicuramente rumore, senza chiamate LLM.

        Quando source == 'watcher' controlla _last_watcher_app (l'app attiva al
        momento della cattura) invece di source stesso, perché source indica
        l'origine della cattura, non l'applicazione.
        """
        app_to_check = self._last_watcher_app if source == "watcher" else source
        if app_to_check in _NOISE_APPS:
            return True
        for pattern in _NOISE_PATTERNS:
            if pattern.match(text):
                return True
        return False

    async def _ollama_urgency_classify(
        self, text: str, source: str = "", context: str = ""
    ) -> tuple[str, str]:
        """Classifica urgenza via Ollama qwen3:8b con caveman prompt.

        Ritorna (level, reason) — level in {"HIGH", "MEDIUM", "LOW"}.
        Timeout 8 s, fallback LOW su qualsiasi errore.
        """
        import aiohttp
        from datetime import datetime as _dt

        now = _dt.now()
        _WEEKDAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        weekday_it = _WEEKDAYS_IT[now.weekday()]

        parts = [f"TEXT: {text[:500]}"]
        if source:
            parts.append(f"Source: {source}")
        parts.append(f"Context: {now.hour}:00 {weekday_it}")
        if context:
            parts.append(f"Hint: {context[:100]}")
        user_msg = "\n".join(parts)

        try:
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(settings.OLLAMA_BASE_URL)
            _ollama_chat_url = f"{_parsed.scheme}://{_parsed.netloc}/api/chat"

            timeout = aiohttp.ClientTimeout(total=settings.URGENCY_OLLAMA_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    _ollama_chat_url,
                    json={
                        "model": settings.OLLAMA_MODEL,
                        "messages": [
                            {"role": "system", "content": _URGENCY_SYSTEM},
                            {"role": "user",   "content": user_msg},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 40},
                    },
                ) as resp:
                    data = await resp.json()

            raw = data.get("message", {}).get("content", "").strip()
            level = "LOW"
            reason = "non classificato"
            for line in raw.splitlines():
                if line.startswith("LEVEL:"):
                    val = line.split(":", 1)[1].strip().upper()
                    if val in {"HIGH", "MEDIUM", "LOW"}:
                        level = val
                elif line.startswith("REASON:"):
                    reason = line.split(":", 1)[1].strip()
            return level, reason

        except Exception as exc:
            logger.warning("Ollama urgency classify fallito: %s", exc)
            return "LOW", "timeout o errore classificatore"

    async def _apply_user_rules(self, level: str, text: str) -> str:
        """Sovrascrive il livello in base alle regole apprese dall'utente.

        Legge personal_learning per agent="urgency", pattern_type="keyword".
        weight > 0.7 → promuove a HIGH; weight < 0.3 → degrada a MEDIUM.
        """
        try:
            patterns = await self.memory.get_learning_patterns(
                agent="urgency", pattern_type="keyword"
            )
            text_lower = text.lower()
            for p in patterns:
                kw = p.get("pattern_value", "").lower()
                if not kw or kw not in text_lower:
                    continue
                w = p.get("weight", 0.5)
                # Verifica acceptance rate: applica solo pattern con signal history utile
                acceptance_rate = await self.memory.get_pattern_acceptance_rate(kw, last_n=20)
                if acceptance_rate < 0.5:
                    logger.debug(
                        "Pattern signal '%s' ha acceptance rate bassa (%.2f), skip", kw, acceptance_rate
                    )
                    continue
                if w > 0.7 and level in ("MEDIUM", "LOW"):
                    return "HIGH"
                if w < 0.3 and level == "HIGH":
                    return "MEDIUM"
        except Exception as exc:
            logger.debug("_apply_user_rules fallito: %s", exc)
        return level

    async def score_urgency(
        self, text: str, source: str = "", context: str = ""
    ) -> tuple[str, str]:
        """Pipeline completa: pre-filter → Ollama classify → user rules.

        Ritorna (level, reason).
        """
        if self._is_obvious_noise(text, source):
            return "LOW", "filtro rumore"
        level, reason = await self._ollama_urgency_classify(text, source=source, context=context)
        level = await self._apply_user_rules(level, text)
        return level, reason

    async def _propose_action(self, text: str, reason: str, source: str) -> None:
        """Invia a Telegram una proposta di azione su cattura HIGH.

        Salva come pending_action per l'handler sì/no in handle_user_message.
        """
        msg = (
            f"⚠️ Rilevato qualcosa da gestire:\n"
            f"«{text[:200]}»\n"
            f"Motivo: {reason}\n\n"
            f"Vuoi che lo gestisca? (rispondi sì/no)"
        )
        await self.notify_telegram(msg, priority=True)
        await self.memory.save_pending_action(
            action_type="urgency_proposal",
            payload={"text": text, "source": source, "reason": reason},
        )

    @staticmethod
    def _sanitize_ocr_input(text: str, max_len: int = 500) -> str:
        """Sanifica testo OCR prima dell'inserimento in un prompt LLM.

        Tronca alla lunghezza massima e rimuove sequenze tipiche di prompt injection.
        """
        text = text.strip()[:max_len]
        text = re.sub(
            r"(?i)(ignore\s+(previous|all|above|prior)\s+instructions?"
            r"|system\s*:|<\s*/?system\s*>|\[\s*system\s*\]"
            r"|assistant\s*:|<\s*/?assistant\s*>"
            r"|\\n---\\n|---END---|<\|im_end\|>|<\|im_start\|>)",
            "",
            text,
        )
        return text.strip()

    async def process_watcher_capture(self, text: str, app_name: str) -> None:
        """Punto di ingresso per ogni cattura dello ScreenWatcher.

        Aggiorna _last_watcher_app, valuta urgenza, propone azione se HIGH.
        Buffering MEDIUM: accumula fino a 5 catture poi invia riepilogo.
        """
        # Sanitizza il testo OCR prima di qualsiasi uso nei prompt LLM
        text = self._sanitize_ocr_input(text)
        self._last_watcher_app = app_name

        # source="watcher" → _is_obvious_noise userà _last_watcher_app per il check NOISE_APPS
        level, reason = await self.score_urgency(text, source="watcher")
        logger.info(
            "Watcher capture — app=%s level=%s reason=%s",
            app_name, level, reason[:60],
        )

        if level == "HIGH":
            await self._propose_action(text, reason, source=app_name)

        elif level == "MEDIUM":
            # Accumula nel buffer — il flush avviene via CronTrigger alle 18:00
            async with self._medium_buffer_lock:
                self._urgency_medium_buffer.append(
                    {"text": text, "app": app_name, "reason": reason}
                )
        # LOW: silenzio — nessuna azione

    async def flush_medium_digest(self) -> None:
        """Invia il digest giornaliero dei MEDIUM e svuota il buffer.

        Chiamato dal job CronTrigger alle URGENCY_MEDIUM_DIGEST_HOUR.
        Se il buffer è vuoto non invia nulla.
        """
        async with self._medium_buffer_lock:
            if not self._urgency_medium_buffer:
                return
            snapshot = list(self._urgency_medium_buffer)
            self._urgency_medium_buffer.clear()
        lines = [
            f"• [{e['app']}] {e['text'][:80]} — {e['reason']}"
            for e in snapshot
        ]
        summary = "\n".join(lines)
        count = len(snapshot)
        await self.notify_telegram(
            f"📋 Riepilogo giornaliero ({count} eventi):\n{summary}"
        )

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    async def _synthesize_reply(
        self, user_message: str, agent_name: str, result: AgentResult, autonomous: bool = False
    ) -> str:
        """Sintetizza risposta dettagliata per Andrea.

        Stesso formato su Telegram e web — sempre completo.
        Ogni risposta include: risultato, raccomandazione, passo successivo.
        """
        output_str = json.dumps(result.output_data, ensure_ascii=False, default=str)
        if len(output_str) > 8000:
            output_str = output_str[:8000] + "... [troncato]"

        # Formato compatto — stesso su chat e Telegram (max 500 token)
        # Struttura identica al formato Telegram publisher/analytics:
        # "Agente — Nicchia\n──────────────\nRiga 1\nRiga 2\nProssimo: ..."
        agent_synthesis_prompts = {
            "research": (
                "Rispondi in max 10 righe. Formato OBBLIGATORIO (niente prose, niente elenchi):\n"
                "Research — {niche}\n"
                "──────────────\n"
                "Verdetto: viable/skip — {ragione 1 riga}\n"
                "Difficoltà: {level}  |  Gap: {top gap in 5 parole}\n"
                "Prezzo: €{launch} → €{regime}  |  Tag: {tag1}, {tag2}, {tag3}\n"
                "Prossimo: Design in avvio."
            ),
            "design": (
                "Rispondi in max 8 righe. Formato OBBLIGATORIO:\n"
                "Design — {niche}\n"
                "──────────────\n"
                "Varianti: {n}  |  Template: {nome}\n"
                "Confidence: {pct}%  |  Thumbnail: {n}/3\n"
                "Prossimo: Publisher in avvio."
            ),
            "publisher": (
                "Rispondi in max 8 righe. Formato OBBLIGATORIO:\n"
                "Publisher — {niche}\n"
                "──────────────\n"
                "Draft: {n}  |  Prezzo A/B: €{a} / €{b}\n"
                "SEO: {chars} car.  |  Tag: 13 applicati\n"
                "Prossimo: Analytics in avvio."
            ),
            "analytics": (
                "Rispondi in max 8 righe. Formato OBBLIGATORIO:\n"
                "Analytics — {data}\n"
                "──────────────\n"
                "Views: {n} ({delta})  |  Vendite: {n}  |  Revenue: €{n}\n"
                "Top: {title} ({n} vendite)\n"
                "Alert: {issues o 'nessuno'}"
            ),
            "finance": (
                "Rispondi in max 8 righe. Formato OBBLIGATORIO:\n"
                "Finance — {periodo}\n"
                "──────────────\n"
                "Ricavi: €{n}  |  Fee Etsy: €{n}  |  Margine: €{n} ({pct}%)\n"
                "Trend: {delta vs periodo prec}\n"
                "Alert: {issues o 'nessuno'}"
            ),
            "customer_service": (
                "Rispondi in max 6 righe. Formato OBBLIGATORIO:\n"
                "Customer Service — {data}\n"
                "──────────────\n"
                "Messaggi: {n}  |  Escalation: {n}\n"
                "Pattern: {issue principale o 'nessuno'}"
            ),
        }

        synthesis_instruction = agent_synthesis_prompts.get(
            agent_name,
            "Riporta il risultato in max 6 righe: cosa è stato fatto, numeri chiave, azione immediata.",
        )

        auto_note = (
            " Il sistema procede automaticamente — non chiedere conferma, non fare domande."
        ) if autonomous else ""

        domain_label = "sistema di automazione Etsy" if self._has_business_domain() else "assistente personale di Andrea"
        synth_system = (
            f"Sei Pepe, {domain_label}. "
            "Rispondi SEMPRE nel formato compatto indicato. "
            "Max 10 righe. Niente prose. Niente elenchi numerati. "
            "Niente emoji decorative. Niente titoli in grassetto. "
            "Solo dati e fatti.\n"
            f"{synthesis_instruction}{auto_note}"
        )
        user_content = (
            f"Agente '{agent_name}' completato — status: {result.status.value}\n"
            f"Confidence: {result.output_data.get('confidence', 'N/A') if isinstance(result.output_data, dict) else 'N/A'}\n\n"
            f"Output:\n{output_str}"
        )
        text = await self._llm_simple_call(synth_system, user_content, max_tokens=500, agent_name=agent_name)
        return text or f"Agente {agent_name} completato. Controlla la dashboard per i dettagli."

    async def _broadcast(self, event: dict) -> None:
        """Invia evento WebSocket se broadcaster disponibile."""
        if self._ws_broadcast is not None:
            try:
                if "timestamp" not in event:
                    event["timestamp"] = datetime.now(timezone.utc).isoformat()
                await self._ws_broadcast(event)
            except Exception:
                pass

    async def _broadcast_context_update(
        self,
        confidence: float | None = None,
        next_action: str | None = None,
        trigger: str = "periodic",
    ) -> None:
        """Emette un evento context_update con lo stato decisionale corrente.

        Campi:
          confidence_threshold — soglia dominio (config)
          confidence_current   — valore rilevato nell'ultimo gate (None se non applicato)
          strategy             — nome strategia attiva ("research_first")
          domain               — nome dominio attivo
          next_action          — azione in corso / prossima
          retry_policy         — stringa descrittiva policy retry
          failure_count        — errori recenti negli ultimi 60 min
          trigger              — causa dell'evento (periodic/dispatch/confidence_gate)
        """
        # Conta errori recenti da tutti gli agenti noti
        failure_count = 0
        try:
            for agent_name in self._agents:
                failure_count += await self.memory.get_agent_error_count(agent_name, hours=1)
        except Exception:
            pass

        # Determina next_action dal registro stato agenti
        if next_action is None:
            running_agents = [
                name for name, status in self._agent_status.items()
                if status.value == "running"
            ]
            if running_agents:
                next_action = f"await_{running_agents[0]}_output"
            else:
                next_action = "idle"

        await self._broadcast({
            "type": "context_update",
            "confidence_threshold": getattr(self.domain, "confidence_threshold", 0.85),
            "confidence_current": confidence,
            "strategy": "research_first",
            "domain": self._business_domain.name if self._business_domain else None,
            "personal_layer_active": True,
            "next_action": next_action,
            "retry_policy": "max_3 · backoff_2s",
            "failure_count": failure_count,
            "trigger": trigger,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_context_state(self) -> dict:
        """Snapshot sincrono dello stato contestuale — usato dallo scheduler per _sync_agent_status."""
        running_agents = [
            name for name, status in self._agent_status.items()
            if status.value == "running"
        ]
        next_action = f"await_{running_agents[0]}_output" if running_agents else "idle"
        return {
            "type": "context_update",
            "confidence_threshold": getattr(self.domain, "confidence_threshold", 0.85),
            "confidence_current": None,
            "strategy": "research_first",
            "domain": getattr(self.domain, "name", "etsy_store"),
            "next_action": next_action,
            "retry_policy": "max_3 · backoff_2s",
            "failure_count": 0,
            "trigger": "sync",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Clarification loop (Intervento 3)
    # ------------------------------------------------------------------

    async def _clarify_if_needed(
        self,
        user_message: str,
        delegation: dict,
        history: list[dict],
        system: str,
        session_id: str,
        source: str,
        task: AgentTask | None = None,    # ← nuovo: task già creato, per correlazione
    ) -> str | None:
        """Verifica se il contesto è sufficiente prima di eseguire l'agente.

        Ritorna una domanda di chiarimento (str) se manca qualcosa,
        None se il contesto è sufficiente e si può procedere.

        Se task è fornito: imposta task.status = INPUT_REQUIRED e salva pending_action
        con task_id correlato + broadcast WS.

        Routing LLM: Ollama in Personal, Haiku in Etsy.
        """
        agent_input  = delegation.get("input", {})
        agent_name   = delegation.get("delegate", "")
        missing: list[str] = []

        if not self._has_business_domain():
            # ── Personal: check per remind e summarize ──
            if agent_name == "remind":
                # action='list' non richiede when — salta il check
                action = agent_input.get("action", "create")
                if action != "list":
                    has_when = bool(
                        agent_input.get("when")
                        or any(
                            w in user_message.lower()
                            for w in ["domani", "stasera", "stanotte", "tra", "alle", "lunedì",
                                      "martedì", "mercoledì", "giovedì", "venerdì", "sabato",
                                      "domenica", "oggi", "settimana", "mese", "ora", "minuti"]
                        )
                    )
                    if not has_when:
                        missing.append("quando vuoi essere ricordato")

            elif agent_name == "summarize":
                # "content" obbligatorio — URL o testo da sintetizzare
                has_content = bool(
                    agent_input.get("content")
                    or agent_input.get("url")
                    or "http" in user_message.lower()
                )
                if not has_content:
                    missing.append("cosa vuoi che sintetizzi (URL o testo)")

            elif agent_name == "research_personal":
                # "query" obbligatoria — cosa cercare sul web
                has_query = bool(agent_input.get("query") and str(agent_input["query"]).strip())
                if not has_query:
                    missing.append("cosa vuoi che cerchi")

        else:
            # ── Etsy: check per research (niche + product_type) ──
            has_niche = bool(
                agent_input.get("niches")
                or agent_input.get("query")
                or any(
                    w in user_message.lower()
                    for w in ["nicchia", "niche", "planner", "tracker", "art", "bundle"]
                )
            )
            has_product_type = bool(agent_input.get("product_type"))
            if not has_niche:
                missing.append("nicchia")
            if not has_product_type:
                missing.append("product_type")

        if not missing:
            return None  # Contesto sufficiente, procedi

        # ── Genera UNA domanda tramite LLM (domain-routed) ──
        _domain = getattr(self, "_business_domain", None) or getattr(self, "domain", None)
        questions_pool = _domain.clarification_questions if _domain else []
        questions_hint = "\n".join(f"- {q}" for q in questions_pool) if questions_pool else ""
        _domain_name = _domain.name if _domain else "personal"

        clarify_system = (
            f"Sei Pepe, assistente di Andrea per il dominio {_domain_name}. "
            "Devi fare UNA domanda specifica per ottenere le informazioni mancanti. "
            "La domanda deve essere diretta, concisa, in italiano. "
            "Rispondi solo con la domanda, niente altro."
        )
        clarify_user = (
            f"L'utente ha detto: '{user_message}'\n"
            f"Manca: {', '.join(missing)}.\n"
            f"Genera UNA domanda breve per ottenerlo.\n"
            f"Esempi utili:\n{questions_hint}"
        )

        question = await self._llm_simple_call(
            clarify_system, clarify_user, max_tokens=150, use_haiku=True, agent_name=agent_name
        )
        if not question:
            return None  # Fallback: procedi senza chiarimento

        # ── Se task fornito: aggiorna stato e persisti pending_action correlata ──
        if task is not None:
            task.status = TaskStatus.INPUT_REQUIRED
            task.pending_input = {
                "required_fields": missing,
                "question": question,
                "context": agent_input,
            }
            await self.memory.save_pending_action(
                action_type="clarification",
                payload={
                    "task_id": task.task_id,
                    "agent_name": agent_name,
                    "question": question,
                    "partial_input": agent_input,
                },
                task_id=task.task_id,
            )
            if self._ws_broadcast:
                await self._ws_broadcast({
                    "type": "task_input_required",
                    "task_id": task.task_id,
                    "agent_name": agent_name,
                    "question": question,
                })

        await self.memory.save_message(session_id, "assistant", question, source)
        return question

    # ------------------------------------------------------------------
    # Context enrichment (Intervento 4)
    # ------------------------------------------------------------------

    async def _enrich_task_context(
        self,
        agent_name: str,
        base_input: dict,
        session_id: str,
    ) -> dict:
        """Arricchisce l'input di ogni AgentTask con contesto completo.

        - Stato production queue per la nicchia
        - Analytics recenti per nicchie simili
        - Failure history da ChromaDB
        - Contesto stagionale
        """
        enriched = dict(base_input)

        # Contesto stagionale sempre presente
        enriched["seasonal_context"] = {
            "current_month": datetime.now(timezone.utc).month,
            "current_year": datetime.now(timezone.utc).year,
        }

        # Niche-specific context
        niche = (
            base_input.get("niche")
            or (base_input.get("niches", [None])[0])
            or base_input.get("query", "")
        )

        if niche and agent_name in ("research", "design", "publisher"):
            # Failure history da ChromaDB (con decadimento temporale)
            try:
                failure_docs = await self.memory.query_chromadb_recent(
                    query=f"FAILURE niche {niche}",
                    n_results=3,
                    where={"type": "failure_analysis"},
                    primary_days=90,
                    fallback_days=180,
                )
                if failure_docs:
                    enriched["failure_history"] = [
                        {
                            "document": d.get("document", ""),
                            "metadata": d.get("metadata", {}),
                        }
                        for d in failure_docs
                    ]
            except Exception:
                pass

            # Success pattern recenti da ChromaDB
            try:
                successes = await self.memory.query_chromadb_recent(
                    query=f"SUCCESS niche {niche}",
                    n_results=2,
                    where={"type": "success_pattern"},
                    primary_days=90,
                    fallback_days=180,
                )
                if successes:
                    enriched["success_patterns"] = [
                        {
                            "document": d.get("document", ""),
                            "metadata": d.get("metadata", {}),
                        }
                        for d in successes
                    ]
            except Exception:
                pass

            # Design outcome recenti da ChromaDB
            try:
                design_wins = await self.memory.query_chromadb_recent(
                    query=f"DESIGN_OUTCOME niche {niche} performance high",
                    n_results=2,
                    where={"type": "design_outcome"},
                    primary_days=90,
                    fallback_days=180,
                )
                if design_wins:
                    enriched["design_wins"] = [
                        {
                            "document": d.get("document", ""),
                            "metadata": d.get("metadata", {}),
                        }
                        for d in design_wins
                    ]
            except Exception:
                pass

            # Performance storica nicchie simili da etsy_listings
            try:
                if hasattr(self.memory, "get_listings_by_niche"):
                    existing = await self.memory.get_listings_by_niche(niche)
                    if existing:
                        enriched["existing_listings_performance"] = [
                            {
                                "listing_id": l.get("listing_id"),
                                "title": l.get("title"),
                                "views": l.get("views", 0),
                                "sales": l.get("sales", 0),
                                "status": l.get("status"),
                            }
                            for l in existing[:5]
                        ]
            except Exception:
                pass

        # Per Design Agent: inietta sempre research_context se presente in sessione
        if agent_name == "design" and not enriched.get("research_context"):
            try:
                cached = await self.memory.query_chromadb_recent(
                    query=f"Research report per nicchia '{niche}'",
                    n_results=1,
                    where={"type": "research_report"},
                    primary_days=90,
                    fallback_days=180,
                )
                if cached:
                    enriched["research_context"] = {
                        "cached_summary": cached[0].get("document", "")
                    }
            except Exception:
                pass

        return enriched

    # ------------------------------------------------------------------
    # Pipeline duplicate check (Intervento 5)
    # ------------------------------------------------------------------

    async def _check_pipeline_duplicate(self, delegation: dict) -> str | None:
        """Verifica se la nicchia è già in produzione o in coda.

        Ritorna messaggio di warning oppure None se si può procedere.
        """
        agent_input = delegation.get("input", {})
        niche = (
            agent_input.get("niche")
            or (agent_input.get("niches", [None])[0])
            or agent_input.get("query", "")
        )

        if not niche:
            return None

        try:
            product_type = agent_input.get("product_type", "printable_pdf")
            is_duplicate = await self.memory.is_duplicate_product(
                niche=niche,
                product_type=product_type,
            )
            if is_duplicate:
                return (
                    f"⚠️ La nicchia **{niche}** è già presente in production queue o "
                    f"tra i listing pubblicati.\n\n"
                    f"Vuoi:\n"
                    f"• Procedere comunque con una variante diversa\n"
                    f"• Vedere le performance del listing esistente\n"
                    f"• Scegliere una nicchia diversa"
                )
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Pipeline & analytics summary (Intervento 6)
    # ------------------------------------------------------------------

    async def _get_pipeline_summary(self) -> str:
        """Ritorna un riassunto dello stato della production queue per il system prompt."""
        try:
            if not hasattr(self.memory, "get_production_queue_stats"):
                return ""
            stats = await self.memory.get_production_queue_stats()
            if not stats:
                return ""
            pending = stats.get("planned", 0)
            in_progress = stats.get("in_progress", 0)
            completed_today = stats.get("completed_today", 0)
            return (
                f"In coda: {pending} prodotti pianificati, "
                f"{in_progress} in lavorazione, "
                f"{completed_today} completati oggi"
            )
        except Exception:
            return ""

    async def _get_recent_analytics_summary(self) -> str:
        """Ritorna un riassunto delle performance recenti per il system prompt."""
        try:
            if not hasattr(self.memory, "get_analytics_summary"):
                return ""
            summary = await self.memory.get_analytics_summary(days=7)
            if not summary:
                return ""
            return (
                f"Ultimi 7 giorni: "
                f"{summary.get('total_views', 0)} views, "
                f"{summary.get('total_sales', 0)} vendite, "
                f"€{summary.get('revenue', 0):.2f} revenue"
            )
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Handler pending_actions (sì/no per proposte varianti)
    # ------------------------------------------------------------------

    async def _check_pending_action(self, message: str, source: str) -> str | None:
        """Controlla se esiste un pending_action e il messaggio è sì/no.

        Gestisce:
        - urgency_proposal (sì/no) → feedback learning loop
        - production_queue_proposal (sì/no) → aggiunge alla queue

        Ritorna la risposta da inviare, oppure None se non applicabile.
        """
        normalized = message.strip().lower()
        yes_words = {"sì", "si", "yes", "s"}
        no_words = {"no", "n", "nope"}

        # --- urgency_proposal ---
        urgency_pending = await self.memory.get_pending_action("urgency_proposal")
        if urgency_pending and normalized in yes_words | no_words:
            payload = urgency_pending.get("payload", {})
            text = payload.get("text", "")
            signal = "positive" if normalized in yes_words else "negative"
            weight_delta = 0.1 if signal == "positive" else -0.1
            # Estrai prime 2 parole chiave dal testo come pattern keyword
            words = [w.lower() for w in text.split() if len(w) > 4][:2]
            for kw in words:
                try:
                    await self.memory.upsert_learning(
                        agent="urgency",
                        pattern_type="keyword",
                        pattern_value=kw,
                        signal_type=signal,
                        weight_delta=weight_delta,
                    )
                except Exception:
                    pass
            await self.memory.delete_pending_action("urgency_proposal")
            if normalized in yes_words:
                # Segnala a Pepe di gestire — per ora risposta testuale
                return "✅ Gestisco. Ti aggiorno a breve."
            else:
                return "👍 Ok, non lo gestisco. Ho preso nota per il futuro."

        # --- clarification (task_id correlato) ---
        clarification_pending = await self.memory.get_pending_action("clarification")
        if clarification_pending:
            payload = clarification_pending.get("payload", {})
            task_id = payload.get("task_id")
            agent_name = payload.get("agent_name")
            partial_input = payload.get("partial_input", {})

            # Merge risposta utente con input parziale
            enriched_input = {**partial_input, "user_clarification": message}

            # Ricreare il task e rimetterlo in coda (stesso task_id per tracciabilità)
            new_task = AgentTask(
                task_id=task_id,
                agent_name=agent_name,
                input_data=enriched_input,
                source=source,
                status=TaskStatus.PENDING,
            )
            await self.memory.resolve_pending_input(task_id)
            return await self._enqueue_and_wait(new_task)

        pending = await self.memory.get_pending_action("production_queue_proposal")

        if not pending:
            return None

        if normalized in yes_words:
            from uuid import uuid4

            payload = pending["payload"]
            niche_variant = f"{payload.get('niche', '')} variante {payload.get('color_scheme', '')} alternativa"
            brief = {
                "niche": niche_variant,
                "product_type": payload.get("product_type", "printable_pdf"),
                "template": payload.get("template", "weekly_planner"),
                "num_variants": 3,
                "color_schemes": [],
                "keywords": [],
            }
            await self.memory.add_to_production_queue(
                task_id=str(uuid4()),
                product_type=payload.get("product_type", "printable_pdf"),
                niche=niche_variant,
                brief=brief,
            )
            await self.memory.delete_pending_action("production_queue_proposal")
            return "✅ Aggiunto in coda! Sarà prodotto nel prossimo ciclo pipeline (domani alle 09:00)."

        if normalized in no_words:
            await self.memory.delete_pending_action("production_queue_proposal")
            return "👍 Ok, proposta ignorata."

        # Messaggio non è sì/no → ignora pending_action, processa normalmente
        return None

    # ------------------------------------------------------------------
    # Confidence gate
    # ------------------------------------------------------------------

    async def _apply_confidence_gate(
        self,
        user_message: str,
        agent_name: str,
        result: AgentResult,
        session_id: str,
        source: str,
    ) -> str:
        """Applica confidence gate sul risultato di un agente.

        >= 0.85: procedi autonomamente + advance pipeline
        0.60-0.84: procedi con disclaimer e proposta
        < 0.60: blocca, spiega cosa manca con opzioni
        None: agente non supporta confidence → procedi normalmente
        """
        output = result.output_data or {}
        confidence = output.get("confidence") if isinstance(output, dict) else None
        missing_data = output.get("missing_data", []) if isinstance(output, dict) else []

        # Task FAILED
        if result.status == TaskStatus.FAILED:
            from datetime import timezone as _tz
            error_msg = (
                output.get("error", "Errore sconosciuto")
                if isinstance(output, dict)
                else str(output)
            )
            if source == "orb_voice":
                # Voce: frase corta umana. Il dettaglio tecnico arriva via WebSocket
                # come campo "detail" nel messaggio "error" → green card sul frontend.
                reply = result.reply_voice or "Non sono riuscito, puoi ripetere?"
                # Broadcast green card con dettaglio tecnico
                if self._ws_broadcast:
                    try:
                        from datetime import datetime as _dt
                        await self._ws_broadcast({
                            "type": "error",
                            "message": reply,
                            "detail": error_msg,
                            "agent": agent_name,
                            "ts": _dt.now(_tz.utc).isoformat(),
                        })
                    except Exception:
                        pass
            else:
                reply = await self._synthesize_error(agent_name, error_msg, {}, missing_data)
            await self.memory.save_message(session_id, "assistant", reply, source)
            await self._broadcast_context_update(
                confidence=confidence,
                next_action="error_recovery",
                trigger="confidence_gate",
            )
            return reply

        # Source of truth: AgentCard.confidence_threshold
        # Fallback: PersonalLayer / DomainContext per retrocompatibilità transizione
        card = self._agent_cards.get(agent_name)
        if card:
            threshold = card.confidence_threshold
            # confidence_disclaimer non è in AgentCard — usa il layer di appartenenza
            if card.layer == "personal":
                disclaimer = self._personal_layer.confidence_disclaimer
            else:
                disclaimer = self._business_domain.confidence_disclaimer if self._business_domain else 0.60
        else:
            # Fallback legacy: agente senza card ancora
            _personal_names = {n for n, c in self._agent_cards.items() if c.layer == "personal"}
            if agent_name in _personal_names or not self._has_business_domain():
                threshold = self._personal_layer.confidence_threshold
                disclaimer = self._personal_layer.confidence_disclaimer
            else:
                d = self._business_domain
                threshold = d.confidence_threshold if d else 0.85
                disclaimer = d.confidence_disclaimer if d else 0.60

        # confidence None o >= threshold: procedi autonomamente
        if confidence is None or confidence >= threshold:
            # Canale vocale con reply_voice dedicata → non serve _synthesize_reply
            if source == "orb_voice" and result.reply_voice:
                final_reply = result.reply_voice
            else:
                final_reply = await self._synthesize_reply(user_message, agent_name, result, autonomous=True)

            # Wiki hook — Branch 2 (prima di _advance_pipeline, vedi Step 5.2.2a)
            if hasattr(self, "wiki") and self.wiki is not None:
                self._fire(
                    self._compile_wiki_entry(agent_name, result, session_id),
                    name="wiki_compile",
                )

            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast_context_update(
                confidence=confidence,
                trigger="confidence_gate",
            )

            # Triggera passo successivo pipeline DOPO il broadcast —
            # garantisce che il report arrivi prima di "🎨 Design Agent avviato"
            await self._advance_pipeline_if_autonomous(agent_name, result, session_id)

            return final_reply

        # confidence >= disclaimer threshold: procedi con disclaimer e proposta
        if confidence >= disclaimer:
            # Canale vocale: il disclaimer confidence è inutile ad alta voce → usa reply_voice
            if source == "orb_voice" and result.reply_voice:
                final_reply = result.reply_voice
            else:
                final_reply = await self._synthesize_reply(user_message, agent_name, result)
                disclaimer_text = (
                    f"\n\n⚠️ **Nota**: analisi basata su dati parziali "
                    f"(confidence {confidence:.0%}). "
                    f"Dati mancanti: {', '.join(missing_data[:3])}.\n"
                    f"Vuoi che proceda comunque o preferisci attendere dati migliori?"
                )
                final_reply += disclaimer_text

            # Wiki hook — Branch 3
            if hasattr(self, "wiki") and self.wiki is not None:
                self._fire(
                    self._compile_wiki_entry(agent_name, result, session_id),
                    name="wiki_compile",
                )

            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast_context_update(
                confidence=confidence,
                next_action="await_user_confirmation",
                trigger="confidence_gate",
            )
            return final_reply

        # confidence < 0.60: NON procedere, rilancia automaticamente
        missing_str = ", ".join(missing_data[:5]) if missing_data else "dati insufficienti"
        reply = (
            f"❌ Dati insufficienti per procedere con sicurezza "
            f"(confidence: {confidence:.0%}).\n\n"
            f"**Mancano**: {missing_str}\n\n"
            f"**Causa principale**: i dati di pricing e keyword "
            f"provengono da inferenza LLM invece che da fonti dirette.\n\n"
            f"**Cosa puoi fare**:\n"
            f"• Attendere l'attivazione delle API di dominio per dati reali\n"
            f"• Specificare una nicchia più narrow per migliorare la ricerca\n"
            f"• Procedere lo stesso accettando il rischio di dati parziali"
        )
        await self.memory.save_message(session_id, "assistant", reply, source)
        await self._broadcast_context_update(
            confidence=confidence,
            next_action="blocked_low_confidence",
            trigger="confidence_gate",
        )
        return reply

    # ------------------------------------------------------------------
    # Error synthesis
    # ------------------------------------------------------------------

    async def _compile_wiki_entry(
        self, agent_name: str, result: AgentResult, session_id: str  # noqa: ARG002
    ) -> None:
        """Alimenta la wiki in background dopo ogni agent completion (Branch 2 e 3).

        Chiamata sempre tramite asyncio.create_task — non bloccante.
        Guard hasattr(self, "wiki") già applicato nel chiamante.
        """
        # Early return per agenti che non producono dati wiki
        if agent_name in {"recall", "remind"}:
            return

        # Copia difensiva — result.output_data potrebbe essere None o oggetto condiviso
        output = dict(result.output_data or {})

        # LLM client per-agente — source of truth: AgentCard.layer (fallback per agenti senza card)
        card = self._agent_cards.get(agent_name)
        if card:
            llm = self._local_client if card.layer == "personal" else self.client
        else:
            # Fallback legacy
            llm = self._local_client if not self._has_business_domain() else self.client

        try:
            if agent_name == "research":
                niches = output.get("niches") or []
                # Supporta sia output singola nicchia che autonomous (winner.niche)
                winner_niche = (output.get("winner") or {}).get("niche", "")
                niche = output.get("niche") or winner_niche or (niches[0].get("name", "") if niches and isinstance(niches[0], dict) else niches[0] if niches else "")
                if niche:
                    await self.wiki.compile_niche(niche, "research", output, llm)
                await self.wiki.store_raw("etsy", "research", output)

            elif agent_name == "analytics":
                niche = output.get("niche", "")
                if niche:
                    await self.wiki.compile_niche(niche, "analytics", output, llm)

            elif agent_name == "publisher":
                # Publisher restituisce N risultati (uno per file) — iteriamo su publish_details
                for detail in output.get("publish_details", []):
                    niche = detail.get("niche", "")
                    if not niche:
                        continue

                    status = detail.get("status", "")
                    listing_id = detail.get("listing_id")

                    # Raw sempre — successo o fallimento
                    await self.wiki.store_raw("etsy", "publisher", detail)

                    # Wiki compile solo se listing creato (dati significativi)
                    if listing_id:
                        await self.wiki.compile_niche(niche, "publisher", detail, llm)

                    # ChromaDB — successo
                    if listing_id:
                        text = (
                            f"Publisher: listing creato per niche '{niche}'. "
                            f"Template: {detail.get('file_type', '')} | "
                            f"Schema: {detail.get('color_scheme', '') or 'N/A'} | "
                            f"Prezzo: {detail.get('price_source', '')} | "
                            f"Variante A/B: {detail.get('ab_variant', '')} | "
                            f"SEO validata: {detail.get('seo_validated', False)} | "
                            f"Immagini: {detail.get('images_uploaded', 0)}/3."
                        )
                        await self.memory.store_insight(text, {
                            "type": "publish_success",
                            "niche": niche,
                            "template": detail.get("file_type", ""),
                            "color_scheme": detail.get("color_scheme", ""),
                            "ab_variant": detail.get("ab_variant", ""),
                            "seo_validated": str(detail.get("seo_validated", False)),
                            "images_uploaded": str(detail.get("images_uploaded", 0)),
                            "price_source": detail.get("price_source", ""),
                            "date": datetime.now(timezone.utc).date().isoformat(),
                        })

                    # ChromaDB — fallimento (skipped o error)
                    elif status in ("skipped_file_too_large", "skipped_no_thumbnails", "error"):
                        error_msg = detail.get("error", "")
                        text = (
                            f"Publisher: listing NON creato per niche '{niche}'. "
                            f"Motivo: {status}. "
                            f"Template: {detail.get('file_type', '')} | "
                            f"Schema: {detail.get('color_scheme', '') or 'N/A'}. "
                            f"Errore: {error_msg[:200] if error_msg else 'nessuno'}."
                        )
                        await self.memory.store_insight(text, {
                            "type": "publish_failure",
                            "niche": niche,
                            "failure_type": status,
                            "template": detail.get("file_type", ""),
                            "color_scheme": detail.get("color_scheme", ""),
                            "date": datetime.now(timezone.utc).date().isoformat(),
                        })

            elif agent_name == "design":
                niche = output.get("niche", "")
                preset = output.get("preset", "")
                template = output.get("template", "")
                variants = output.get("variants", [])

                # Raw sempre — una entry per variante generata
                for variant in variants:
                    await self.wiki.store_raw("etsy", "design", {
                        "niche": niche,
                        "preset": preset,
                        "template": template,
                        "color_scheme": variant.get("color_scheme", ""),
                        "colors": variant.get("colors", {}),
                        "validation": variant.get("validation", {}),
                        "pages": variant.get("pages", 0),
                        "include_dates": output.get("include_dates", False),
                    })

                # ChromaDB — design_outcome per variante (letto da _lookup_failure_patterns)
                for variant in variants:
                    if not niche or not preset or not template:
                        continue
                    validation = variant.get("validation", {})
                    color_scheme = variant.get("color_scheme", "")
                    text = (
                        f"Design: variante generata per niche '{niche}'. "
                        f"Preset: {preset} | Template: {template} | "
                        f"Schema colore: {color_scheme or 'N/A'} | "
                        f"PDF valido: {validation.get('valid', False)} | "
                        f"Pagine: {variant.get('pages', 0)} | "
                        f"Dimensione: {validation.get('file_size_kb', 0):.0f}KB."
                    )
                    await self.memory.store_insight(text, {
                        "type": "design_outcome",
                        "niche": niche,
                        "preset": preset,
                        "template": template,
                        "color_scheme": color_scheme,
                        "pdf_valid": str(validation.get("valid", False)),
                        "pages": str(variant.get("pages", 0)),
                        "file_size_kb": str(round(validation.get("file_size_kb", 0))),
                        "date": datetime.now(timezone.utc).date().isoformat(),
                    })

            elif agent_name == "finance":
                content = json.dumps(output, ensure_ascii=False)
                await self.wiki.compile_wiki_file("etsy", "patterns/pricing", content, llm)

            elif agent_name == "research_personal":
                await self.wiki.store_raw("personal", "research", output)

            elif agent_name == "summarize":
                content = output.get("summary") or output.get("text") or str(output)
                await self.wiki.store_raw("personal", "summarize", output)
                if content:
                    await self.wiki.compile_wiki_file("personal", "preferences", content, llm)

        except Exception as exc:
            logger.warning("_compile_wiki_entry (%s): %s", agent_name, exc)

    # ------------------------------------------------------------------

    @staticmethod
    def _voice_error_phrase(error_msg: str) -> str:
        """Mappa un messaggio di errore tecnico in una frase vocale breve e umana.

        Nessuna chiamata LLM — lookup sincrono per mantenere latenza minima
        sul canale vocale. Il dettaglio completo viene inviato via WebSocket
        come campo 'detail' per la green card sul frontend.
        """
        msg = error_msg.lower()
        if any(k in msg for k in ("quando", "when")):
            return "Non ho capito quando, puoi ripetere?"
        if any(k in msg for k in ("testo mancante", "missing", "manca", "campo")):
            return "Non ho capito bene, puoi ripetere?"
        if any(k in msg for k in ("timeout", "timed out", "ci ha messo")):
            return "Ci ho messo troppo, riprova."
        if any(k in msg for k in ("connect", "network", "unreachable", "connessione")):
            return "C'è un problema di connessione, riprova tra un momento."
        if any(k in msg for k in ("duplicat", "già un reminder", "già present")):
            return "Hai già qualcosa di simile, vuoi aggiungerlo lo stesso?"
        if any(k in msg for k in ("notion", "calendar", "sincronizzazione")):
            return "Fatto, anche se la sincronizzazione esterna non è riuscita."
        if any(k in msg for k in ("auth", "api key", "unauthorized", "credenziali")):
            return "C'è un problema con le credenziali, controlla la configurazione."
        return "Non sono riuscito, puoi ripetere?"

    async def _synthesize_error(
        self,
        agent_name: str,
        error_message: str,
        context_data: dict | None = None,
        missing_data: list[str] | None = None,
    ) -> str:
        """Sintetizza errore in linguaggio naturale per l'utente."""
        domain_label = self._business_domain.name if self._business_domain else "personal"
        error_system = (
            f"Sei Pepe, orchestratore di AgentPeXI per il dominio {domain_label}. "
            "Un agente ha fallito. Riferisci onestamente cosa è successo: "
            "descrivi l'errore reale (anche tecnico se necessario), spiega la causa probabile "
            "solo se deducibile dall'errore stesso — non speculare. "
            "Proponi solo azioni concretamente applicabili nel sistema "
            "(es. riprovare, riformulare la richiesta, verificare una configurazione specifica). "
            "NON inventare workaround generici. NON attribuire il problema a server esterni "
            "se non è nell'errore. NON dare consigli su volumi di dati o tempi di attesa "
            "a meno che non siano nell'errore stesso. Sii diretto. Max 100 parole."
        )
        context_str = json.dumps(context_data, ensure_ascii=False, default=str) if context_data else "{}"
        missing_str = ", ".join(missing_data) if missing_data else "nessuno"
        user_content = (
            f"Agente: {agent_name}\n"
            f"Errore: {error_message}\n"
            f"Contesto: {context_str}\n"
            f"Missing data: {missing_str}"
        )
        text = await self._llm_simple_call(
            error_system, user_content, max_tokens=512, use_haiku=True, agent_name=agent_name
        )
        return text or f"L'agente {agent_name} ha fallito: {error_message}"

    # ------------------------------------------------------------------
    # Pipeline automation (Intervento 8)
    # ------------------------------------------------------------------

    async def _advance_pipeline_if_autonomous(
        self,
        agent_name: str,
        result: AgentResult,
        session_id: str,
    ) -> None:
        """Dopo un risultato con confidence >= 0.85, avanza la pipeline autonomamente.

        Research completato → nessuna azione (Pepe propone Design nella risposta).
        Design completato → auto-trigger Publisher se file_paths disponibili.
        Analytics completato → triggera learning loop.
        """
        output = result.output_data or {}

        if agent_name == "analytics":
            # Learning loop: processa risultati analytics
            await self._handle_learning_loop(output)
            return

        if agent_name == "publisher":
            # Publisher completato → auto-trigger Analytics per sincronizzare stats
            listings_created = output.get("listings_created", 0)
            if listings_created > 0:
                analytics_task = AgentTask(
                    agent_name="analytics",
                    input_data={
                        "trigger": "post_publish",
                        "listings_created": listings_created,
                        "_run_cost_usd": output.get("_run_cost_usd", 0.0),  # cumulativo research+design+publisher
                    },
                    source="pipeline_auto",
                )
                logger.info(
                    "Publisher completato (%d listing) → auto-trigger Analytics",
                    listings_created,
                )
                self._fire(
                    self._run_analytics_auto(analytics_task, session_id),
                    name="analytics_auto",
                )
            return

        if agent_name == "design":
            file_paths = output.get("file_paths", [])
            # Il Design Agent restituisce i file dentro "variants" (lista di dict),
            # non come "file_paths" flat. Estrai i path da ogni variante.
            variants = output.get("variants", [])
            if not file_paths:
                for v in variants:
                    path = v.get("pdf_path") or v.get("file_path") or v.get("svg_path") or v.get("path")
                    if path:
                        file_paths.append(path)
            if not file_paths:
                logger.info("Design completato senza file_paths né variants, publisher non triggerato")
                return

            # Estrai thumbnail path dai variants (generati da Playwright)
            # I publisher li usa come immagini Etsy — passali esplicitamente.
            thumbnail_paths: list[str] = []
            for v in variants:
                thumbs = v.get("thumbnails", {})
                for key in ("mockup", "cover", "interior"):
                    p = thumbs.get(key)
                    if p:
                        thumbnail_paths.append(str(p))

            # Recupera contesto necessario per Publisher dall'input del task originale
            publisher_input = {
                "file_paths": file_paths,
                "thumbnail_paths": thumbnail_paths,  # path espliciti da Design Agent
                "product_type": output.get("product_type", "printable_pdf"),
                "template": output.get("template", ""),
                "niche": output.get("niche", ""),
                "color_schemes": output.get("color_schemes", []),
                "keywords": output.get("keywords", []),
                "size": output.get("size", "A4"),
                "production_queue_task_id": output.get("production_queue_task_id"),
                "pricing": output.get("pricing", {}),  # da research_context, per prezzo research-driven
                "_run_cost_usd": output.get("_run_cost_usd", 0.0),  # costo cumulativo research+design
            }

            publish_task = AgentTask(
                agent_name="publisher",
                input_data=publisher_input,
                source="pipeline_auto",
            )
            logger.info(
                "Design completato (%d file) → auto-trigger Publisher",
                len(file_paths),
            )
            # Fire-and-forget: non blocca la risposta a Andrea
            self._fire(self._run_publisher_auto(publish_task, session_id), name="publisher_auto")
            return

        if agent_name == "research" and result.status == TaskStatus.COMPLETED:
            # Research → Design: auto-trigger se ci sono dati di ricerca
            research_output = output
            niches = research_output.get("niches", [])
            if not niches:
                # Prova a usare l'output come contesto diretto
                niche = research_output.get("niche", research_output.get("query", ""))
                if niche:
                    niches = [{"niche": niche, "product_type": research_output.get("product_type", "printable_pdf")}]

            if niches:
                # Prendi la prima nicchia viable per il design
                # Il research schema usa "name" e "recommended_product_type" (non "niche"/"product_type")
                first = niches[0] if isinstance(niches[0], dict) else {"name": niches[0]}
                niche_name = first.get("name") or first.get("niche", "")
                _VALID_PRODUCT_TYPES = {"printable_pdf", "digital_art_png", "svg_bundle"}
                product_type = (
                    first.get("recommended_product_type")
                    or first.get("product_type", "printable_pdf")
                )
                if product_type not in _VALID_PRODUCT_TYPES:
                    product_type = "printable_pdf"
                design_input = {
                    "niche": niche_name,
                    "product_type": product_type,
                    "research_context": research_output,
                    "keywords": first.get("keywords", []),
                    "color_schemes": first.get("color_schemes", []),
                    "_run_cost_usd": result.cost_usd,  # costo research, accumulato lungo la pipeline
                }
                design_task = AgentTask(
                    agent_name="design",
                    input_data=design_input,
                    source="pipeline_auto",
                )
                logger.info(
                    "Research completato → auto-trigger Design per nicchia '%s'",
                    niche_name or "?",
                )
                self._fire(
                    self._run_design_auto(design_task, session_id),
                    name="design_auto",
                )
            return

    async def _run_design_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue il design in background dopo research, notifica via WS e Telegram."""
        try:
            niche = task.input_data.get('niche', '?')
            msg = f"Design avviato — {niche}"
            # Piccolo delay: garantisce che il reply del research (già in volo su Telegram)
            # arrivi prima del "Design avviato" — evita inversione di ordine dei messaggi
            await asyncio.sleep(2.0)
            await self.notify_telegram(msg)
            result = await self._enqueue_and_wait(task)
            if result.status == TaskStatus.COMPLETED:
                output = result.output_data or {}

                # Inietta nel result il contesto research che il Design Agent non propaga
                # (pricing, keywords, color_schemes) — il Publisher ne ha bisogno
                research_ctx = task.input_data.get("research_context", {})
                if research_ctx:
                    first_niche = next(
                        iter(research_ctx.get("niches", [{}])), {}
                    ) if research_ctx.get("niches") else research_ctx
                    if not output.get("pricing") and first_niche.get("pricing"):
                        output["pricing"] = first_niche["pricing"]
                    if not output.get("keywords") and first_niche.get("keywords"):
                        output["keywords"] = first_niche.get("keywords", [])
                    # color_schemes: prendi dai variants se non già presenti
                    if not output.get("color_schemes"):
                        variants = output.get("variants", [])
                        cs = [v.get("color_scheme", "") for v in variants if v.get("color_scheme")]
                        if cs:
                            output["color_schemes"] = cs
                    # In alternativa usa quelli dall'input del task
                    if not output.get("color_schemes") and task.input_data.get("color_schemes"):
                        output["color_schemes"] = task.input_data["color_schemes"]
                    result.output_data = output

                n_files = len(output.get("variants", [])) or output.get("variants_generated", 0)
                design_cost = result.cost_usd
                cost_so_far = task.input_data.get("_run_cost_usd", 0.0) + design_cost
                output["_run_cost_usd"] = cost_so_far  # propaga al publisher via output_data
                result.output_data = output
                msg = f"Design completato — {n_files} varianti in pending. Costo step: ${design_cost:.4f}. Pubblicazione in avvio."
                await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
                await self.notify_telegram(msg)
                # _advance_pipeline_if_autonomous gestirà Design → Publisher
                await self._advance_pipeline_if_autonomous("design", result, session_id)
            else:
                error = (result.output_data or {}).get("error", "Errore sconosciuto")
                msg = f"Design fallito — {error}"
                await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
                await self.notify_telegram(msg, priority=True)
        except Exception as exc:
            logger.error("Design auto fallito: %s", exc)
            msg = f"Design interrotto — {exc}"
            await self.notify_telegram(msg, priority=True)

    async def _run_publisher_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue il publisher in background dopo il design, notifica via WS e Telegram."""
        try:
            niche = task.input_data.get("niche", "?")
            start_msg = f"Pubblicazione avviata — {niche}"
            await self.notify_telegram(start_msg)
            result = await self._enqueue_and_wait(task)
            output = result.output_data or {}
            n = output.get("listings_created", 0)
            publisher_cost = result.cost_usd
            cost_so_far = task.input_data.get("_run_cost_usd", 0.0) + publisher_cost
            output["_run_cost_usd"] = cost_so_far  # propaga all'analytics
            result.output_data = output
            msg = (
                f"Pubblicazione completata — {n} draft su Etsy. Costo step: ${publisher_cost:.4f}. Analisi in avvio."
                if n > 0
                else f"Pubblicazione completata — nessun draft creato. Costo step: ${publisher_cost:.4f}. Verifica log publisher."
            )
            await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
            # Publisher → Analytics: sincronizza stats dopo ogni pubblicazione
            await self._advance_pipeline_if_autonomous("publisher", result, session_id)
        except Exception as exc:
            logger.error("Publisher auto fallito: %s", exc)
            msg = f"Pubblicazione interrotta — {exc}"
            await self.notify_telegram(msg, priority=True)

    async def _run_analytics_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue analytics in background dopo il publisher, notifica via WS e Telegram."""
        try:
            msg = "Analytics post-pubblicazione avviato."
            await self.notify_telegram(msg)
            result = await self._enqueue_and_wait(task)
            output = result.output_data or {}

            # Costruisci il report formattato (stesso formato di Telegram)
            # per mostrarlo anche nella chat web — i due canali restano identici
            summary_msg = _format_analytics_summary(output)
            await self.memory.save_message(session_id, "assistant", summary_msg, "pipeline_auto")
            # Telegram riceve il report già da analytics.py._send_daily_summary;
            # mandiamo solo il breve "completato" per non duplicare il report
            # Conta totale: attivi + bozze (evita "0" quando tutti i listing sono draft)
            listings_analyzed = (
                (output.get("total_listings_active") or 0)
                + (output.get("drafts") or 0)
                or output.get("listings_analyzed_count")
                or len(output.get("listings_analyzed", []))
            )
            analytics_cost = result.cost_usd
            total_run_cost = task.input_data.get("_run_cost_usd", 0.0) + analytics_cost
            total_run_eur = total_run_cost * settings.USD_EUR_RATE
            done_msg = (
                f"Analytics completato — {listings_analyzed} listing analizzati.\n"
                f"Costo run: ${total_run_cost:.4f} (≈ €{total_run_eur:.4f})"
            )
            await self.memory.save_message(session_id, "assistant", done_msg, "pipeline_auto")
            await self.notify_telegram(done_msg)
            # Learning loop
            await self._advance_pipeline_if_autonomous("analytics", result, session_id)
        except Exception as exc:
            logger.error("Analytics auto fallito: %s", exc)
            msg = f"Analytics interrotto — {exc}"
            await self.notify_telegram(msg, priority=True)

    # ------------------------------------------------------------------
    # Learning loop (Intervento 9)
    # ------------------------------------------------------------------

    async def _evaluate_and_gate_pattern(
        self,
        signal: str,
        pattern_value: str,
        metric_type: str,
        current_metric: float,
    ) -> bool:
        """Applica acceptance gate prima di salvare un pattern appreso.

        Recupera il baseline della metrica dalle ultime LEARNING_EVAL_WINDOW occorrenze.
        Se il delta >= LEARNING_ACCEPTANCE_THRESHOLD: salva il pattern, ritorna True.
        Se il delta < threshold: non salva, logga il rifiuto, ritorna False.
        Se dati insufficienti (prima volta): salva comunque (cold start), ritorna True.
        """
        baseline = await self.memory.get_baseline_metric(
            metric_type, window=settings.LEARNING_EVAL_WINDOW
        )

        if baseline is None:
            # Cold start: nessun dato storico, accetta comunque
            logger.info(
                "Learning gate: cold start per signal=%s, pattern=%s — accettato",
                signal, pattern_value
            )
            return True

        delta = current_metric - baseline

        if delta >= settings.LEARNING_ACCEPTANCE_THRESHOLD:
            await self.memory.save_learning_evaluation(
                pattern_id=f"{signal}:{pattern_value}",
                signal_type=signal,
                metric_type=metric_type,
                baseline_value=baseline,
                post_value=current_metric,
                accepted=True,
            )
            logger.info(
                "Learning gate: ACCETTATO signal=%s delta=%.3f (baseline=%.3f post=%.3f)",
                signal, delta, baseline, current_metric
            )
            return True
        else:
            await self.memory.save_learning_evaluation(
                pattern_id=f"{signal}:{pattern_value}",
                signal_type=signal,
                metric_type=metric_type,
                baseline_value=baseline,
                post_value=current_metric,
                accepted=False,
            )
            logger.info(
                "Learning gate: RIFIUTATO signal=%s delta=%.3f < threshold=%.3f",
                signal, delta, settings.LEARNING_ACCEPTANCE_THRESHOLD
            )
            return False

    async def _store_design_winner(
        self, niche: str, template: str, color_scheme: str, views: int, sales: int
    ) -> None:
        """Scrive design_winner su ChromaDB quando un listing converte bene.

        Letto da DesignAgent._lookup_failure_patterns per guidare la scelta
        di preset e template nelle run successive sulla stessa niche.
        """
        try:
            text = (
                f"Design winner per niche '{niche}': "
                f"template '{template}', schema colore '{color_scheme or 'N/A'}'. "
                f"Performance: {sales} vendite, {views} views."
            )
            await self.memory.store_insight(text, {
                "type": "design_winner",
                "niche": niche,
                "template": template,
                "color_scheme": color_scheme,
                "views": str(views),
                "sales": str(sales),
                "date": datetime.now(timezone.utc).date().isoformat(),
            })
            logger.info("Design winner salvato: niche=%s template=%s sales=%d", niche, template, sales)
        except Exception as exc:
            logger.warning("Errore salvataggio design_winner niche=%s: %s", niche, exc)

    async def _handle_learning_loop(self, analytics_output: dict) -> None:
        """Processa i risultati dell'Analytics Agent e triggera azioni autonome.

        - Bestseller → aggiunge varianti alla production queue + notifica
        - 0 views a 7gg → triggera Research per fix tag
        - 0 conversioni a 45gg → triggera Research per revisione prezzo
        """
        listings = analytics_output.get("listings_analyzed", [])

        for listing in listings:
            listing_id = listing.get("listing_id")
            niche = listing.get("niche", "")
            views = listing.get("views", 0)
            sales = listing.get("sales", 0)
            days_live = listing.get("days_live", 0)
            failure_type = listing.get("failure_type")
            template = listing.get("template", "")
            color_scheme = listing.get("color_scheme", "")

            # --- Design winner — indipendente dal segnale primario ---
            # Criteri: almeno 1 vendita, almeno 10 views, metadati design presenti.
            # Viene scritto su ChromaDB e letto da DesignAgent al prossimo run sulla niche.
            if sales >= 1 and views >= 10 and template and niche:
                await self._store_design_winner(niche, template, color_scheme, views, sales)

            # Determina segnale dal dato
            signal = None
            if sales >= 10:
                signal = "bestseller"
            elif failure_type == "no_views" and days_live >= 7:
                signal = "no_views"
            elif failure_type == "no_conversion" and days_live >= 45 and views > 0:
                signal = "no_conversion"

            if signal is None:
                continue

            action = self.domain.learning_triggers.get(signal)

            if action == "propose_variant":
                # Gate: accetta pattern bestseller solo se le vendite segnano un delta positivo
                accepted = await self._evaluate_and_gate_pattern(
                    signal="bestseller",
                    pattern_value=niche,
                    metric_type="sales_delta",
                    current_metric=float(sales),
                )
                if not accepted:
                    continue
                proposal_msg = (
                    f"🌟 **Bestseller rilevato**: {listing.get('title', listing_id)}\n"
                    f"📊 {sales} vendite, {views} views\n\n"
                    f"Vuoi che creo varianti di questo prodotto? "
                    f"Rispondi 'sì' per aggiungerle in coda automaticamente."
                )
                await self.notify_telegram(proposal_msg, priority=True)

                # Salva come pending_action per handler sì/no
                await self.memory.save_pending_action(
                    action_type="bestseller_variant_proposal",
                    payload={
                        "listing_id": listing_id,
                        "niche": niche,
                        "product_type": listing.get("product_type", "printable_pdf"),
                        "original_sales": sales,
                    },
                )

            elif action == "fix_tags":
                # Gate: accetta fix_tags solo se il delta views giustifica l'intervento
                views_delta = listing.get("delta_views_vs_yesterday", 0)
                accepted = await self._evaluate_and_gate_pattern(
                    signal="no_views",
                    pattern_value=niche,
                    metric_type="views_delta",
                    current_metric=float(views_delta),
                )
                if not accepted:
                    continue
                fix_task = AgentTask(
                    agent_name="research",
                    input_data={
                        "niches": [niche],
                        "task_type": "fix_tags",
                        "target_listing_id": listing_id,
                        "problem": "0 views dopo 7 giorni — tag strategy da rivedere",
                        "current_tags": listing.get("tags", []),
                    },
                    source="learning_loop",
                )
                await self._queue.put(fix_task)
                await self.notify_telegram(
                    f"🔍 Avviata ricerca automatica per fix tag: {listing.get('title', listing_id)}\n"
                    f"0 views dopo {days_live} giorni."
                )

            elif action == "fix_pricing":
                # Gate: accetta fix_pricing solo se il delta conversioni giustifica l'intervento
                accepted = await self._evaluate_and_gate_pattern(
                    signal="no_conversion",
                    pattern_value=niche,
                    metric_type="task_success_rate",
                    current_metric=0.0,  # 0 conversioni = task_success_rate = 0
                )
                if not accepted:
                    continue
                fix_task = AgentTask(
                    agent_name="research",
                    input_data={
                        "niches": [niche],
                        "task_type": "fix_pricing",
                        "target_listing_id": listing_id,
                        "problem": f"0 vendite dopo {days_live} giorni con {views} views — prezzo da rivedere",
                        "current_price": listing.get("price_usd"),
                    },
                    source="learning_loop",
                )
                await self._queue.put(fix_task)
                await self.notify_telegram(
                    f"💰 Avviata analisi prezzo automatica: {listing.get('title', listing_id)}\n"
                    f"{views} views ma 0 vendite dopo {days_live} giorni."
                )

            else:
                logger.debug("Segnale '%s' non gestito nel dominio '%s'", signal, self.domain.name)
