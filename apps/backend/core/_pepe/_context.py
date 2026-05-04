"""Context, synthesis and clarification mixin for Pepe."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from apps.backend.core.models import AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.pepe")


class ContextMixin:

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
