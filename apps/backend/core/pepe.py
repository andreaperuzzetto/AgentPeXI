"""Pepe — Orchestratore principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.core.config import MODEL_SONNET, MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentStatus, AgentTask, TaskStatus
from apps.backend.agents.base import AgentBase

logger = logging.getLogger("agentpexi.pepe")

# ------------------------------------------------------------------
# System prompt per l'orchestratore — generato dinamicamente
# ------------------------------------------------------------------


def _build_system_prompt(
    agent_statuses: dict[str, str],
    production_queue_summary: str = "",
    recent_analytics: str = "",
    current_month: int | None = None,
) -> str:
    from datetime import datetime as _dt

    month = current_month or _dt.utcnow().month

    # Contesto stagionale
    seasonal_map = {
        1: "Gennaio: picco pianificatori anno nuovo, tracker abitudini, budget annuali",
        2: "Febbraio: Valentine's Day, wedding planning inizia, self-love journals",
        3: "Marzo: spring cleaning organizers, Easter, inizio anno accademico AU/NZ",
        4: "Aprile: spring planners, Easter printables, tax season trackers",
        5: "Maggio: Mother's Day, wedding season inizia, summer planning",
        6: "Giugno: summer planners, wedding season picco, back-to-school prep inizia",
        7: "Luglio: summer activities kids, mid-year review planners",
        8: "Agosto: back-to-school PICCO (massima opportunità), teacher resources",
        9: "Settembre: back-to-school, autumn planners, Q4 prep business",
        10: "Ottobre: Halloween, Christmas prep inizia, holiday planners",
        11: "Novembre: Black Friday, Christmas PICCO, gift guides, advent",
        12: "Dicembre: Christmas, New Year prep, anno nuovo planners",
    }
    seasonal_context = seasonal_map.get(month, "")

    agent_status_str = ", ".join(f"{n}: {s}" for n, s in agent_statuses.items())

    pipeline_section = ""
    if production_queue_summary:
        pipeline_section = f"\n\nStato pipeline attuale:\n{production_queue_summary}"

    analytics_section = ""
    if recent_analytics:
        analytics_section = f"\n\nPerformance recente:\n{recent_analytics}"

    return f"""\
Sei Pepe, orchestratore di AgentPeXI — sistema automatizzato per vendere digital \
products su Etsy. Il tuo proprietario è Andrea. Rispondi sempre in italiano.

Il tuo obiettivo non è gestire agenti — è vendere prodotti digitali su Etsy.
Ogni decisione che prendi deve essere orientata a massimizzare le vendite reali.

## Contesto attuale
Mese: {month} — {seasonal_context}
Stato agenti: {agent_status_str}{pipeline_section}{analytics_section}

## Regole di business Etsy (NON negoziabili)
- Prezzo minimo accettabile: $2.99 (sotto questa soglia il margine è negativo dopo fee Etsy)
- Prezzo sweet spot digital products: $3.99-$7.99 per entry, $9.99-$14.99 per bundle
- Competition level "high" accettabile SOLO se demand level è "high" e c'è un gap chiaro
- Nicchie declining + high competition: non entrare mai
- Stagionalità: pubblica 6-8 settimane prima del picco per indicizzazione Etsy
- 13 tag Etsy sono obbligatori per ogni listing: mix 60% long-tail + 40% high-volume
- I primi 7 giorni di un listing sono critici per l'algoritmo Etsy

## Regole di decisione autonoma
- Confidence ≥ 0.85 + viable = true: procedi al passo successivo SENZA chiedere
- Confidence 0.60-0.84: proponi ad Andrea con raccomandazione chiara, aspetta conferma
- Confidence < 0.60: NON procedere, rilancia ricerca con query raffinate automaticamente
- Se confidence < 0.50 dopo secondo tentativo: blocca e spiega cosa manca

## Regole di chiarimento
Prima di delegare al Research Agent, verifica di avere:
- Nicchia specifica (non "qualcosa di profittevole")
- Tipo prodotto indicativo (PDF/PNG/SVG o "decidi tu")
- Eventuali vincoli (prezzo, stile, target audience)

Se mancano informazioni critiche, fai domande SPECIFICHE una alla volta.
Continua a chiedere finché non hai contesto sufficiente per una ricerca accurata.
Quando hai tutto, dichiara esplicitamente le assunzioni che stai facendo.

## Agenti disponibili
Quando deleghi, il campo "input" DEVE contenere i parametri specificati:

- **research**: analisi nicchia Etsy con selling focus
  input: {{"niches": ["nicchia"], "product_type": "printable_pdf|digital_art_png|svg_bundle", "constraints": {{}}}}

- **design**: creazione prodotti digitali
  input: {{"product_type": "...", "niche": "...", "research_context": {{...output research...}}}}

- **publisher**: pubblicazione listing Etsy
  input: {{"file_paths": [...], "niche": "...", "research_context": {{...}}}}

- **analytics**: analisi performance listing
  input: {{"period_days": 7}}

- **customer_service**: gestione messaggi clienti
  input: {{}}

- **finance**: report finanziari
  input: {{"period_days": 7}}

## Formato risposta
Rispondi SEMPRE con JSON di delega (nessun testo aggiuntivo) oppure in linguaggio naturale.
JSON di delega: {{"delegate": "<agent>", "task_type": "<tipo>", "input": {{...}}}}
MAI testo + JSON insieme — o uno o l'altro.\
"""


class Pepe:
    """Orchestratore centrale: gestisce queue, agenti, e interazione utente."""

    def __init__(
        self,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster

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
        system = _build_system_prompt(
            agent_statuses={n: s.value for n, s in self._agent_status.items()},
            production_queue_summary=pipeline_summary,
            recent_analytics=analytics_summary,
        )

        # Prima chiamata LLM — decide se delegare o rispondere
        response = await self.client.messages.create(
            model=MODEL_SONNET,
            system=system,
            messages=history,
            max_tokens=2048,
        )
        reply_text = response.content[0].text if response.content else ""

        # Controlla se Pepe vuole delegare
        delegation = self._parse_delegation(reply_text)

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

    @staticmethod
    def _parse_delegation(text: str) -> dict | None:
        """Cerca un blocco JSON di delega nella risposta di Pepe."""
        import re

        # Prima prova con blocco code markdown ```json ... ```
        code_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if code_match:
            try:
                data = json.loads(code_match.group(1))
                if "delegate" in data:
                    return data
            except json.JSONDecodeError:
                pass

        # Poi cerca JSON raw contando le graffe (gestisce nesting correttamente)
        for i, char in enumerate(text):
            if char == "{":
                depth = 0
                for j, c in enumerate(text[i:], i):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = text[i : j + 1]
                            try:
                                data = json.loads(candidate)
                                if "delegate" in data:
                                    return data
                            except json.JSONDecodeError:
                                break

        return None

    async def _broadcast(self, event: dict) -> None:
        """Invia evento WebSocket se broadcaster disponibile."""
        if self._ws_broadcast is not None:
            try:
                await self._ws_broadcast(event)
            except Exception:
                pass

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

        # Genera domanda specifica tramite LLM
        clarification_prompt = await self.client.messages.create(
            model=MODEL_HAIKU,
            system=(
                "Sei Pepe, assistente di Andrea per il suo business Etsy. "
                "Devi fare UNA domanda specifica per ottenere le informazioni mancanti. "
                "La domanda deve essere diretta, concisa, e aiutare a capire esattamente "
                "cosa vuole vendere. Rispondi solo con la domanda, niente altro."
            ),
            messages=[
                *history,
                {
                    "role": "user",
                    "content": (
                        f"L'utente ha scritto: '{user_message}'\n"
                        f"Per fare una ricerca accurata mancano: {', '.join(missing)}.\n"
                        f"Genera UNA domanda specifica per ottenere queste informazioni. "
                        f"Esempio: se manca la nicchia chiedi quale categoria specifica. "
                        f"Se manca il tipo prodotto chiedi se vuole PDF stampabile, "
                        f"arte digitale PNG, o bundle SVG."
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
            # Failure history da ChromaDB
            try:
                failure_docs = await self.memory.query_insights(
                    f"failure analysis {niche}",
                    n_results=3,
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
                cached = await self.memory.query_insights(
                    f"Research report per nicchia '{niche}'",
                    n_results=1,
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
            return reply

        # confidence None o >= 0.85: procedi autonomamente
        if confidence is None or confidence >= 0.85:
            final_reply = await self._synthesize_reply(user_message, agent_name, result)

            # Triggera passo successivo pipeline se in modalità autonoma
            await self._advance_pipeline_if_autonomous(agent_name, result, session_id)

            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast({"type": "pepe_message", "content": final_reply, "source": source})
            return final_reply

        # confidence 0.60-0.84: procedi con disclaimer e proposta
        if confidence >= 0.60:
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
            return final_reply

        # confidence < 0.60: NON procedere, rilancia automaticamente
        missing_str = ", ".join(missing_data[:5]) if missing_data else "dati insufficienti"
        reply = (
            f"❌ Dati insufficienti per procedere con sicurezza "
            f"(confidence: {confidence:.0%}).\n\n"
            f"**Mancano**: {missing_str}\n\n"
            f"**Causa principale**: senza Etsy API attiva, i dati di pricing e keyword "
            f"provengono da inferenza LLM invece che da listing reali.\n\n"
            f"**Cosa puoi fare**:\n"
            f"• Attendere l'approvazione Etsy API per dati reali\n"
            f"• Specificare una nicchia più narrow per migliorare la ricerca\n"
            f"• Procedere lo stesso accettando il rischio di dati parziali"
        )
        await self.memory.save_message(session_id, "assistant", reply, source)
        await self._broadcast({"type": "pepe_message", "content": reply, "source": source})
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
            "Sei Pepe, orchestratore di un sistema AI per Etsy. Un agente ha riscontrato "
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

        Research completato → propone Design.
        Analytics completato → triggera learning loop.
        """
        output = result.output_data or {}

        if agent_name == "analytics":
            # Learning loop: processa risultati analytics
            await self._handle_learning_loop(output)
            return

        # Per research in pipeline automatica (source = "scheduler")
        # Non triggerare automaticamente se la richiesta è venuta da Andrea
        # (source = "web" o "telegram") — in quel caso aspetta conferma
        # La pipeline automatica è gestita dallo scheduler

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

            # Bestseller: sales >= 10 → proposta varianti
            if sales >= 10:
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

            # 0 views dopo 7 giorni → fix tag
            elif failure_type == "no_views" and days_live >= 7:
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

            # 0 conversioni dopo 45 giorni → revisione prezzo
            elif failure_type == "no_conversion" and days_live >= 45 and views > 0:
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
