"""Pepe — Orchestratore principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.core.config import MODEL_SONNET, MODEL_HAIKU, settings
from apps.backend.core.domains import DomainContext, DOMAIN_ETSY
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentStatus, AgentTask, TaskStatus
from apps.backend.agents.base import AgentBase

logger = logging.getLogger("agentpexi.pepe")

# ------------------------------------------------------------------
# Tool definition per delega agenti (Anthropic tool_use)
# ------------------------------------------------------------------

DELEGATION_TOOL = {
    "name": "delegate_to_agent",
    "description": (
        "Delega un task a un agente specializzato. "
        "Usalo SEMPRE quando l'utente chiede di creare prodotti, fare ricerca di mercato, "
        "pubblicare listing, analizzare performance o generare report finanziari. "
        "NON rispondere in prosa descrivendo cosa faresti — delega direttamente."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "delegate": {
                "type": "string",
                "enum": ["research", "design", "publisher", "analytics", "finance"],
                "description": "Nome dell'agente a cui delegare il task.",
            },
            "input": {
                "type": "object",
                "description": "Parametri per l'agente. Per research: {niches: [...], product_type: '...'}. Per design: {niche, product_type, research_context}. Per analytics/finance: {}.",
            },
            "task_type": {
                "type": "string",
                "description": "Tipo di task (es: niche_research, create_listing, full_pipeline, analytics_report).",
            },
        },
        "required": ["delegate", "input"],
    },
}




class Pepe:
    """Orchestratore centrale: gestisce queue, agenti, e interazione utente."""

    def __init__(
        self,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        active_domain: DomainContext = DOMAIN_ETSY,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self.domain = active_domain

        # Anthropic client
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

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

        # Mock mode — attivabile via /mock Telegram
        self.mock_mode: bool = False

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

    # ------------------------------------------------------------------
    # Registrazione agenti
    # ------------------------------------------------------------------

    def register_agent(self, name: str, agent: AgentBase) -> None:
        self._agents[name] = agent
        self._agent_status[name] = AgentStatus.IDLE
        logger.info("Agente registrato: %s", name)

    def get_agent_statuses(self) -> dict[str, str]:
        return {name: status.value for name, status in self._agent_status.items()}

    def resume_agent(self, name: str) -> bool:
        """Riattiva un agente sospeso per troppi errori."""
        if name in self._agent_status and self._agent_status[name] == AgentStatus.ERROR:
            self._agent_status[name] = AgentStatus.IDLE
            logger.info("Agente %s riattivato", name)
            return True
        return False

    # ------------------------------------------------------------------
    # System prompt — costruito dal DomainContext attivo
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        agent_statuses: dict[str, str],
        production_queue_summary: str = "",
        recent_analytics: str = "",
        current_month: int | None = None,
    ) -> str:
        import calendar
        month_name = calendar.month_name[current_month] if current_month else ""

        # Identità — invariante, non viene dal dominio
        identity = (
            "Sei Pepe, orchestratore di AgentPeXI. "
            "Il tuo proprietario è Andrea. Rispondi sempre in italiano. "
            "Il tuo obiettivo non è gestire agenti — è raggiungere i risultati "
            "di business del dominio attivo."
        )

        # Obiettivo — dal dominio
        objective = f"## Obiettivo attuale ({self.domain.name})\n{self.domain.objective}"

        # Regole di business — dal dominio
        rules = "## Regole di business (NON negoziabili)\n"
        rules += "\n".join(f"- {r}" for r in self.domain.business_rules)

        # Agenti disponibili — dal dominio
        agents_section = "## Agenti disponibili\n"
        for agent_name, schema in self.domain.agents.items():
            agents_section += f"- **{agent_name}**: {schema}\n"

        # Sezioni extra — dal dominio (es. stagionalità)
        extras = ""
        if self.domain.extra_sections:
            extras = "\n\n".join(
                f"## {title}\n{body}"
                for title, body in self.domain.extra_sections.items()
            )
            if month_name:
                extras += f"\n\nMese corrente: {month_name}."

        # Contesto runtime — invariante
        agent_status_str = ", ".join(f"{n}: {s}" for n, s in agent_statuses.items())
        runtime = f"## Stato sistema\nAgenti: {agent_status_str}"
        if production_queue_summary:
            runtime += f"\nPipeline: {production_queue_summary}"
        if recent_analytics:
            runtime += f"\nPerformance recente: {recent_analytics}"

        return "\n\n".join(filter(bool, [
            identity, objective, rules, agents_section, extras, runtime
        ]))

    # ------------------------------------------------------------------
    # Entry point — messaggio utente
    # ------------------------------------------------------------------

    async def handle_user_message(
        self, message: str, source: str = "web", session_id: str = "default"
    ) -> str:
        """Gestisce un messaggio utente: risposta diretta o delega ad agente."""
        # Salva messaggio utente nella sessione
        await self.memory.save_message(session_id, "user", message, source)

        # --- Handler "sì/no" per pending_actions ---
        quick_reply = await self._check_pending_action(message, source)
        if quick_reply is not None:
            await self.memory.save_message(session_id, "assistant", quick_reply, source)
            await self._broadcast({"type": "pepe_message", "content": quick_reply, "source": source})
            return quick_reply

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

        # System prompt dinamico con contesto iniettato
        system = self._build_system_prompt(
            agent_statuses={n: s.value for n, s in self._agent_status.items()},
            production_queue_summary=pipeline_summary,
            recent_analytics=analytics_summary,
        )

        # Prima chiamata LLM — decide se delegare (tool_use) o rispondere in testo
        response = await self.client.messages.create(
            model=MODEL_SONNET,
            system=system,
            messages=history,
            max_tokens=2048,
            tools=[DELEGATION_TOOL],
        )

        # Estrai delega (tool_use) e/o testo dalla risposta
        delegation = None
        reply_text = ""
        for block in response.content:
            if block.type == "tool_use" and block.name == "delegate_to_agent":
                delegation = block.input  # già dict strutturato
            elif hasattr(block, "text"):
                reply_text += block.text

        if delegation:
            agent_name = delegation["delegate"]

            # AGGIUNTA 2 — Clarification loop
            # Se l'agente è research, verifica contesto sufficiente prima di procedere
            if agent_name == "research":
                clarification = await self._clarify_if_needed(
                    message, delegation, history, system, session_id, source
                )
                if clarification is not None:
                    # Pepe ha fatto una domanda — aspetta il prossimo turno
                    return clarification

                # Verifica duplicati in pipeline
                duplicate_warning = await self._check_pipeline_duplicate(delegation)
                if duplicate_warning:
                    await self.memory.save_message(session_id, "assistant", duplicate_warning, source)
                    await self._broadcast({"type": "pepe_message", "content": duplicate_warning, "source": source})
                    return duplicate_warning

            # AGGIUNTA 3 — Context enrichment
            enriched_input = await self._enrich_task_context(
                agent_name=agent_name,
                base_input=delegation.get("input", {}),
                session_id=session_id,
            )
            delegation["input"] = enriched_input

            task = AgentTask(
                agent_name=agent_name,
                input_data=delegation.get("input", {}),
                source=source,
            )
            task.input_data["task_type"] = delegation.get("task_type", "generic")

            # Mette in coda e attende risultato
            try:
                result = await self._enqueue_and_wait(task)
            except Exception as exc:
                error_reply = await self._synthesize_error(
                    agent_name, str(exc), task.input_data
                )
                await self.memory.save_message(session_id, "assistant", error_reply, source)
                await self._broadcast({"type": "pepe_message", "content": error_reply, "source": source})
                return error_reply

            # --- Confidence gate ---
            final_reply = await self._apply_confidence_gate(
                message, agent_name, result, session_id, source
            )
            return final_reply

        # Risposta diretta
        await self.memory.save_message(session_id, "assistant", reply_text, source)
        await self._broadcast({"type": "pepe_message", "content": reply_text, "source": source})
        return reply_text

    # ------------------------------------------------------------------
    # Task queue
    # ------------------------------------------------------------------

    async def _enqueue_and_wait(self, task: AgentTask) -> AgentResult:
        """Mette il task in coda, crea un Future e attende il risultato."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentResult] = loop.create_future()
        self._pending_futures[task.task_id] = future
        await self._queue.put(task)
        logger.info("Task %s in coda per agente %s", task.task_id, task.agent_name)
        return await future

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
    # Helpers privati
    # ------------------------------------------------------------------

    async def _synthesize_reply(
        self, user_message: str, agent_name: str, result: AgentResult
    ) -> str:
        """Sintetizza risposta dettagliata per Andrea.

        Stesso formato su Telegram e web — sempre completo.
        Ogni risposta include: risultato, raccomandazione, passo successivo.
        """
        output_str = json.dumps(result.output_data, ensure_ascii=False, default=str)
        if len(output_str) > 8000:
            output_str = output_str[:8000] + "... [troncato]"

        # System prompt differenziato per agente
        agent_synthesis_prompts = {
            "research": (
                "Sintetizza il report di ricerca Etsy per Andrea. Struttura la risposta così:\n"
                "1. **Raccomandazione** (1 riga): entra o non entra in questa nicchia e perché\n"
                "2. **Nicchie analizzate**: per ognuna viable — nome, prezzo consigliato, "
                "difficoltà, i 3 tag più importanti, cosa fare per differenziarsi\n"
                "3. **Segnali di vendita**: thumbnail style, bundle vs singolo, timing stagionale\n"
                "4. **Passo successivo**: cosa fare adesso (es: 'Posso procedere con il Design Agent')\n"
                "Se ci sono nicchie scartate, spiegale brevemente.\n"
                "Usa markdown con grassetti. Sii specifico, non generico."
            ),
            "design": (
                "Sintetizza i risultati del Design Agent. Struttura:\n"
                "1. **Prodotti generati**: quante varianti, template usato, preset visivo\n"
                "2. **Thumbnail**: conferma se le 3 immagini Etsy sono state generate\n"
                "3. **Confidence**: mostra il valore e cosa manca se < 0.85\n"
                "4. **Passo successivo**: 'Posso procedere con il Publisher Agent' o cosa manca\n"
                "Usa markdown. Sii concreto."
            ),
            "publisher": (
                "Sintetizza i risultati del Publisher Agent. Struttura:\n"
                "1. **Listing creati**: quanti, su quale nicchia\n"
                "2. **Dettagli SEO**: titolo usato, 13 tag impostati, prezzo A/B test\n"
                "3. **Link**: se disponibili, includi link Etsy ai draft\n"
                "4. **Prossimi 7 giorni**: cosa monitorare (views attese, soglia alert)\n"
                "Usa markdown."
            ),
            "analytics": (
                "Sintetizza il report analytics. Struttura:\n"
                "1. **Overview**: views totali, vendite, revenue periodo analizzato\n"
                "2. **Top performer**: listing con più vendite o views\n"
                "3. **Problemi rilevati**: listing con 0 views o 0 conversioni\n"
                "4. **Azioni automatiche avviate**: se il sistema ha triggerato fix automatici\n"
                "5. **Raccomandazione**: cosa fare questa settimana\n"
                "Usa markdown con numeri chiari."
            ),
            "finance": (
                "Sintetizza il report finanziario. Struttura:\n"
                "1. **P&L**: entrate lorde, fee Etsy (6.5% + €0.20/listing), costi API, margine netto\n"
                "2. **Trend**: rispetto al periodo precedente\n"
                "3. **Alert**: se ci sono costi fuori controllo o margine negativo\n"
                "4. **Raccomandazione**: ottimizzazioni possibili\n"
                "Usa markdown con €/$ chiari."
            ),
            "customer_service": (
                "Sintetizza le attività customer service. Struttura:\n"
                "1. **Messaggi gestiti**: quanti, tipologia\n"
                "2. **Escalation**: casi che richiedono intervento di Andrea\n"
                "3. **Pattern**: problemi ricorrenti da risolvere a monte\n"
                "Usa markdown."
            ),
        }

        synthesis_instruction = agent_synthesis_prompts.get(
            agent_name,
            "Sintetizza il risultato dell'agente in modo chiaro per Andrea. "
            "Includi sempre: cosa è stato fatto, raccomandazione, passo successivo.",
        )

        try:
            response = await self.client.messages.create(
                model=MODEL_SONNET,
                system=(
                    f"Sei Pepe, orchestratore di AgentPeXI. Rispondi in italiano.\n"
                    f"{synthesis_instruction}"
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"L'utente ha chiesto: {user_message}\n\n"
                            f"L'agente '{agent_name}' ha completato con status: {result.status.value}\n"
                            f"Confidence: {result.output_data.get('confidence', 'N/A') if isinstance(result.output_data, dict) else 'N/A'}\n\n"
                            f"Output completo:\n{output_str}"
                        ),
                    }
                ],
                max_tokens=2048,
            )
            return response.content[0].text if response.content else "Task completato."
        except Exception:
            return f"✅ Agente {agent_name} completato. Controlla la dashboard per i dettagli."

    async def _broadcast(self, event: dict) -> None:
        """Invia evento WebSocket se broadcaster disponibile."""
        if self._ws_broadcast is not None:
            try:
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
            "domain": getattr(self.domain, "name", "etsy_store"),
            "next_action": next_action,
            "retry_policy": "max_3 · backoff_2s",
            "failure_count": failure_count,
            "trigger": trigger,
            "timestamp": datetime.utcnow().isoformat(),
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
            "timestamp": datetime.utcnow().isoformat(),
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
    ) -> str | None:
        """Verifica se il contesto è sufficiente per una ricerca accurata.

        Se manca qualcosa, genera UNA domanda specifica e la ritorna.
        Ritorna None se il contesto è sufficiente → si può procedere.
        """
        agent_input = delegation.get("input", {})

        # Criteri di sufficienza per Research Agent
        has_niche = bool(
            agent_input.get("niches")
            or agent_input.get("query")
            or any(
                word in user_message.lower()
                for word in ["nicchia", "niche", "planner", "tracker", "art", "bundle"]
            )
        )
        has_product_type = bool(agent_input.get("product_type"))

        missing = []
        if not has_niche:
            missing.append("nicchia")
        if not has_product_type:
            missing.append("product_type")

        if not missing:
            return None  # Contesto sufficiente, procedi

        # Genera domanda specifica tramite LLM, usando pool domande dal dominio
        questions_pool = self.domain.clarification_questions
        questions_hint = "\n".join(f"- {q}" for q in questions_pool) if questions_pool else ""

        clarification_prompt = await self.client.messages.create(
            model=MODEL_HAIKU,
            system=(
                f"Sei Pepe, assistente di Andrea per il dominio {self.domain.name}. "
                "Devi fare UNA domanda specifica per ottenere le informazioni mancanti. "
                "La domanda deve essere diretta, concisa, e aiutare a capire esattamente "
                "cosa vuole. Rispondi solo con la domanda, niente altro."
            ),
            messages=[
                *history,
                {
                    "role": "user",
                    "content": (
                        f"L'utente ha scritto: '{user_message}'\n"
                        f"Per fare una ricerca accurata mancano: {', '.join(missing)}.\n"
                        f"Genera UNA domanda specifica per ottenere queste informazioni.\n"
                        f"Domande di riferimento:\n{questions_hint}"
                    ),
                },
            ],
            max_tokens=150,
        )

        question = clarification_prompt.content[0].text if clarification_prompt.content else ""
        if not question:
            return None  # Fallback: procedi senza chiarimento

        await self.memory.save_message(session_id, "assistant", question, source)
        await self._broadcast({"type": "pepe_message", "content": question, "source": source})
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
            "current_month": datetime.utcnow().month,
            "current_year": datetime.utcnow().year,
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

        Ritorna la risposta da inviare, oppure None se non applicabile.
        """
        normalized = message.strip().lower()
        pending = await self.memory.get_pending_action("production_queue_proposal")

        if not pending:
            return None

        yes_words = {"sì", "si", "yes", "s"}
        no_words = {"no", "n", "nope"}

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
            error_msg = (
                output.get("error", "Errore sconosciuto")
                if isinstance(output, dict)
                else str(output)
            )
            reply = await self._synthesize_error(agent_name, error_msg, {}, missing_data)
            await self.memory.save_message(session_id, "assistant", reply, source)
            await self._broadcast({"type": "pepe_message", "content": reply, "source": source})
            await self._broadcast_context_update(
                confidence=confidence,
                next_action="error_recovery",
                trigger="confidence_gate",
            )
            return reply

        # confidence None o >= threshold: procedi autonomamente
        if confidence is None or confidence >= self.domain.confidence_threshold:
            final_reply = await self._synthesize_reply(user_message, agent_name, result)

            # Triggera passo successivo pipeline se in modalità autonoma
            await self._advance_pipeline_if_autonomous(agent_name, result, session_id)

            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast({"type": "pepe_message", "content": final_reply, "source": source})
            await self._broadcast_context_update(
                confidence=confidence,
                trigger="confidence_gate",
            )
            return final_reply

        # confidence >= disclaimer threshold: procedi con disclaimer e proposta
        if confidence >= self.domain.confidence_disclaimer:
            final_reply = await self._synthesize_reply(user_message, agent_name, result)
            disclaimer = (
                f"\n\n⚠️ **Nota**: analisi basata su dati parziali "
                f"(confidence {confidence:.0%}). "
                f"Dati mancanti: {', '.join(missing_data[:3])}.\n"
                f"Vuoi che proceda comunque o preferisci attendere dati migliori?"
            )
            final_reply += disclaimer
            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast({"type": "pepe_message", "content": final_reply, "source": source})
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
        await self._broadcast({"type": "pepe_message", "content": reply, "source": source})
        await self._broadcast_context_update(
            confidence=confidence,
            next_action="blocked_low_confidence",
            trigger="confidence_gate",
        )
        return reply

    # ------------------------------------------------------------------
    # Error synthesis
    # ------------------------------------------------------------------

    async def _synthesize_error(
        self,
        agent_name: str,
        error_message: str,
        context_data: dict | None = None,
        missing_data: list[str] | None = None,
    ) -> str:
        """Sintetizza errore in linguaggio naturale per l'utente."""
        error_system = (
            f"Sei Pepe, orchestratore di AgentPeXI per il dominio {self.domain.name}. Un agente ha riscontrato "
            "un problema. Spiega all'utente cosa è successo in modo chiaro e professionale, "
            "proponi 2-3 soluzioni concrete e pratiche. Sii diretto, non tecnico, non "
            "mostrare stack trace o codice. Max 150 parole."
        )
        context_str = json.dumps(context_data, ensure_ascii=False, default=str) if context_data else "{}"
        missing_str = ", ".join(missing_data) if missing_data else "nessuno"
        try:
            response = await self.client.messages.create(
                model=MODEL_HAIKU,
                system=error_system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Agente: {agent_name}\n"
                        f"Errore: {error_message}\n"
                        f"Contesto: {context_str}\n"
                        f"Missing data: {missing_str}"
                    ),
                }],
                max_tokens=512,
            )
            return response.content[0].text if response.content else f"L'agente {agent_name} ha riscontrato un problema. Riprova più tardi."
        except Exception:
            return f"L'agente {agent_name} ha riscontrato un problema. Riprova più tardi."

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

        if agent_name == "design":
            file_paths = output.get("file_paths", [])
            if not file_paths:
                logger.info("Design completato senza file_paths, publisher non triggerato")
                return

            # Recupera contesto necessario per Publisher dall'input del task originale
            publisher_input = {
                "file_paths": file_paths,
                "product_type": output.get("product_type", "printable_pdf"),
                "template": output.get("template", ""),
                "niche": output.get("niche", ""),
                "color_schemes": output.get("color_schemes", []),
                "keywords": output.get("keywords", []),
                "size": output.get("size", "A4"),
                "production_queue_task_id": output.get("production_queue_task_id"),
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
            asyncio.create_task(self._run_publisher_auto(publish_task, session_id))
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
                # Prendi la prima nicchia per il design
                first = niches[0] if isinstance(niches[0], dict) else {"niche": niches[0]}
                design_input = {
                    "niche": first.get("niche", ""),
                    "product_type": first.get("product_type", "printable_pdf"),
                    "research_context": research_output,
                    "keywords": research_output.get("keywords", []),
                    "color_schemes": first.get("color_schemes", []),
                }
                design_task = AgentTask(
                    agent_name="design",
                    input_data=design_input,
                    source="pipeline_auto",
                )
                logger.info(
                    "Research completato → auto-trigger Design per nicchia '%s'",
                    first.get("niche", "?"),
                )
                asyncio.create_task(self._run_design_auto(design_task, session_id))
            return

    async def _run_design_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue il design in background dopo research, notifica via WS."""
        try:
            msg = f"🎨 Design Agent avviato automaticamente per '{task.input_data.get('niche', '?')}'..."
            await self._broadcast({"type": "pepe_message", "content": msg, "source": "pipeline_auto"})
            result = await self._enqueue_and_wait(task)
            if result.status == TaskStatus.COMPLETED:
                output = result.output_data or {}
                n_files = len(output.get("file_paths", []))
                msg = f"✅ Design completato: {n_files} file generati."
                await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
                await self._broadcast({"type": "pepe_message", "content": msg, "source": "pipeline_auto"})
                # _advance_pipeline_if_autonomous gestirà Design → Publisher
                await self._advance_pipeline_if_autonomous("design", result, session_id)
            else:
                error = (result.output_data or {}).get("error", "Errore sconosciuto")
                msg = f"❌ Design fallito: {error}"
                await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
                await self._broadcast({"type": "pepe_message", "content": msg, "source": "pipeline_auto"})
        except Exception as exc:
            logger.error("Design auto fallito: %s", exc)
            await self._broadcast({
                "type": "pepe_message",
                "content": f"❌ Design auto fallito: {exc}",
                "source": "pipeline_auto",
            })

    async def _run_publisher_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue il publisher in background dopo il design, notifica via WS."""
        try:
            result = await self._enqueue_and_wait(task)
            output = result.output_data or {}
            n = output.get("listings_created", 0)
            msg = (
                f"✅ Publisher completato automaticamente: {n} listing creati su Etsy."
                if n > 0
                else "⚠️ Publisher completato ma nessun listing creato. Controlla i log."
            )
            await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
            await self._broadcast({"type": "pepe_message", "content": msg, "source": "pipeline_auto"})
        except Exception as exc:
            logger.error("Publisher auto fallito: %s", exc)
            await self._broadcast({
                "type": "pepe_message",
                "content": f"❌ Publisher auto fallito: {exc}",
                "source": "pipeline_auto",
            })

    # ------------------------------------------------------------------
    # Learning loop (Intervento 9)
    # ------------------------------------------------------------------

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
