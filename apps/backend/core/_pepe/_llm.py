"""LLM routing mixin for Pepe."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import anthropic

from apps.backend.core.config import MODEL_SONNET, MODEL_HAIKU, settings

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


class LlmMixin:

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
                logger.exception("Unexpected error")
        logger.debug(
            "_pepe_llm_call [%s]: model=%s in=%d out=%d cost=$%.5f dur=%dms",
            label, model, input_tokens, output_tokens, cost_usd, duration_ms,
        )
        return response

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
        from datetime import datetime as _dt, timezone
        is_personal = self._is_personal_intent(last_message) if last_message else False
        now_str = _dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
            except Exception as exc:
                logger.warning("_llm_simple_call [%s]: LLM fallito: %s", agent_name or "unknown", exc)
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
