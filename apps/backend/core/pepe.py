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

Hai a disposizione questi agenti:
- **research**: analisi di mercato, trend Etsy, nicchie, keyword, competitor
- **design**: creazione prodotti digitali (PDF, PNG, SVG)
- **publisher**: pubblicazione listing su Etsy (titoli SEO, tag, upload)
- **analytics**: analisi performance listing, revenue, ottimizzazione
- **customer_service**: risposte messaggi clienti Etsy
- **finance**: report finanziari, costi API, margini

Quando l'utente chiede qualcosa:
1. Se puoi rispondere direttamente (saluti, domande generali, stato sistema), fallo.
2. Se serve un agente, rispondi con un JSON così:
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

    async def handle_user_message(self, message: str, source: str = "web") -> str:
        """Gestisce un messaggio utente: risposta diretta o delega ad agente."""
        # Salva conversazione
        await self.memory.save_conversation("user", message)

        # Recupera contesto da ChromaDB
        context_docs = await self.memory.query_insights(message, n_results=3)
        context_text = ""
        if context_docs:
            context_text = "\n\nContesto dalla memoria:\n" + "\n".join(
                f"- {d['document']}" for d in context_docs
            )

        # Conversazioni recenti per continuità
        recent = await self.memory.get_recent_conversations(limit=10)
        history = [{"role": m["role"], "content": m["content"]} for m in recent[:-1]]  # escludi ultimo (è il messaggio corrente)

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
                error_reply = f"Mi dispiace, l'agente {agent_name} ha riscontrato un errore: {exc}"
                await self.memory.save_conversation("assistant", error_reply)
                await self._broadcast({"type": "pepe_message", "content": error_reply, "source": source})
                return error_reply

            # Sintetizza risposta finale basata sul risultato dell'agente
            final_reply = await self._synthesize_reply(message, agent_name, result)
            await self.memory.save_conversation("assistant", final_reply)
            await self._broadcast({"type": "pepe_message", "content": final_reply, "source": source})
            return final_reply

        # Risposta diretta
        await self.memory.save_conversation("assistant", reply_text)
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
        # Cerca JSON nel testo (può essere inline o in un blocco code)
        import re

        # Pattern: cerca {"delegate": ...}
        patterns = [
            r'```json\s*(\{.*?\})\s*```',  # blocco code
            r'(\{"delegate"\s*:.*?\})',      # inline
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    if "delegate" in data:
                        return data
                except (json.JSONDecodeError, IndexError):
                    continue
        return None

    async def _broadcast(self, event: dict) -> None:
        """Invia evento WebSocket se broadcaster disponibile."""
        if self._ws_broadcast is not None:
            try:
                await self._ws_broadcast(event)
            except Exception:
                pass
