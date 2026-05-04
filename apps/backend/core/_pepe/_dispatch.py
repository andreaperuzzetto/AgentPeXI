"""Dispatch mixin for Pepe — task queue, worker loop, message handler."""
from __future__ import annotations

import asyncio
import logging
import re

from apps.backend.core.models import AgentResult, AgentStatus, AgentTask

logger = logging.getLogger("agentpexi.pepe")

# Pattern che fanno auto-invoke Recall senza passare dal confidence gate
_RECALL_PATTERN = re.compile(
    r"(cosa|quando|dove).{0,20}(stav[oa]|ho\s+(visto|letto|aperto|cercato)|"
    r"guardav[oa]|leggev[oa]|facev[oa]|usav[oa]|era\s+aperto)",
    re.IGNORECASE,
)


class DispatchMixin:

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

    async def has_pending_voice_clarification(self) -> bool:
        """Controlla se la sessione voice_orb ha una clarification in attesa.

        Usato dal WebSocket vocale per decidere se restare in ascolto
        (fase utterance) invece di tornare al wake word.
        """
        action = await self.memory.get_pending_action("clarification")
        return action is not None
