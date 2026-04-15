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
# System prompt per l'orchestratore
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Sei Pepe, l'orchestratore intelligente di AgentPeXI — un sistema multi-agente \
per automatizzare un business Etsy di prodotti digitali.

Il tuo proprietario è Andrea. Rispondi sempre in italiano.

Hai a disposizione questi agenti. Quando deleghi, il campo "input" DEVE contenere
i parametri specificati per ogni agente:

- **research**: analisi di mercato Etsy, trend, nicchie, keyword, competitor
  input: {"query": "descrizione della ricerca"} OPPURE {"niches": ["nicchia1", "nicchia2"]}

- **design**: creazione prodotti digitali (PDF printable, PNG art, SVG bundle)
  input: {"product_type": "printable_pdf|digital_art_png|svg_bundle", "niche": "...", "style": "..."}

- **publisher**: pubblicazione listing su Etsy
  input: {"file_path": "...", "niche": "...", "keywords": ["..."]}

- **analytics**: analisi performance listing e revenue
  input: {"period_days": 7}

- **customer_service**: gestione messaggi clienti Etsy
  input: {}

- **finance**: report finanziari, costi API, margini
  input: {"period_days": 7}

Quando l'utente chiede qualcosa:
1. Se puoi rispondere direttamente (saluti, domande generali, stato sistema), fallo.
2. Se serve un agente, rispondi SOLO con questo JSON (nessun testo aggiuntivo):
   {"delegate": "<agent_name>", "task_type": "<tipo>", "input": {<parametri>}}
3. Se non sei sicuro, chiedi chiarimenti.

Non inventare dati. Se non hai informazioni, dillo.\
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

        # Stato agenti per contesto
        agent_status_str = ", ".join(
            f"{n}: {s.value}" for n, s in self._agent_status.items()
        )
        system = _SYSTEM_PROMPT
        if agent_status_str:
            system += f"\n\nStato agenti: {agent_status_str}"

        # Chiama Sonnet per decidere
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
        """Sintetizza la risposta finale per l'utente basata sull'output dell'agente."""
        output_str = json.dumps(result.output_data, ensure_ascii=False, default=str)
        # Tronca output se troppo lungo per il contesto
        if len(output_str) > 8000:
            output_str = output_str[:8000] + "... [troncato]"

        messages = [
            {
                "role": "user",
                "content": (
                    f"L'utente ha chiesto: {user_message}\n\n"
                    f"L'agente '{agent_name}' ha completato il task con stato: {result.status.value}\n"
                    f"Output: {output_str}\n\n"
                    f"Sintetizza una risposta chiara e utile per l'utente in italiano. "
                    f"Sii conciso ma completo."
                ),
            }
        ]

        response = await self.client.messages.create(
            model=MODEL_HAIKU,
            system="Sei Pepe, assistente di Andrea. Rispondi in italiano. Sintetizza i risultati degli agenti in modo chiaro.",
            messages=messages,
            max_tokens=1024,
        )
        return response.content[0].text if response.content else "Task completato."

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

        >= 0.85: procedi normalmente
        0.60-0.84: procedi con disclaimer
        < 0.60: blocca, chiedi approfondimento
        None: agente non supporta confidence → procedi normalmente
        """
        output = result.output_data or {}
        confidence = output.get("confidence") if isinstance(output, dict) else None
        missing_data = output.get("missing_data", []) if isinstance(output, dict) else []

        if result.status == TaskStatus.FAILED:
            error_msg = output.get("error", "Errore sconosciuto") if isinstance(output, dict) else str(output)
            reply = await self._synthesize_error(agent_name, error_msg, {}, missing_data)
            await self.memory.save_message(session_id, "assistant", reply, source)
            await self._broadcast({"type": "pepe_message", "content": reply, "source": source})
            return reply

        if confidence is None or confidence >= 0.85:
            # Procedi normalmente
            final_reply = await self._synthesize_reply(user_message, agent_name, result)
            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast({"type": "pepe_message", "content": final_reply, "source": source})
            return final_reply

        if confidence >= 0.60:
            # Procedi con disclaimer
            final_reply = await self._synthesize_reply(user_message, agent_name, result)
            disclaimer = f"\n\n[Nota: analisi basata su dati parziali — confidence {confidence:.0%}]"
            final_reply += disclaimer
            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast({"type": "pepe_message", "content": final_reply, "source": source})
            return final_reply

        # confidence < 0.60 → blocca
        missing_str = ", ".join(missing_data[:5]) if missing_data else "dati insufficienti"
        reply = (
            f"Non ho dati sufficienti per procedere con accuratezza "
            f"(confidence: {confidence:.0%}).\n"
            f"Mancano: {missing_str}.\n"
            f"Vuoi che approfondisca la ricerca su questa nicchia con fonti alternative?"
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
