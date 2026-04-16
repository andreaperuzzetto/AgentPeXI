"""SummarizeAgent — riassume testi da URL, file allegati Telegram e testo libero.

Input:
  {"url": "https://...", "text": "...", "file_id": "...", "mode": "short|detailed"}

Risoluzione sorgente (priorità):
  1. url  → TextExtractor.from_url()
  2. file_id (allegato Telegram) → TextExtractor.from_telegram_file()
  3. text  → testo già fornito inline

Pipeline:
1. Estrai testo dalla sorgente
2. Se testo > SUMMARIZE_MAX_CHARS: chunk + map-reduce summary
   Se testo ≤ SUMMARIZE_MAX_CHARS: summary diretto
3. Output in formato Perplexity-style (sezioni numerate, breve, no markdown pesante)

Usa Claude Haiku per velocità + cost control (summarize non richiede ragionamento profondo).
Fallback Ollama se Anthropic non raggiungibile.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools.text_extract import TextExtractor

logger = logging.getLogger("agentpexi.summarize")

# Numero massimo caratteri prima di passare a map-reduce
_SUMMARIZE_MAX_CHARS: int = getattr(settings, "SUMMARIZE_MAX_CHARS", 20_000)

_SUMMARY_SYSTEM_SHORT = (
    "Sei Pepe, assistente di Andrea. "
    "Riassumi il testo in max 5 punti chiave, in italiano. "
    "Formato: bullet concisi, niente intro, niente conclusioni verbose. "
    "Inizia direttamente con il primo punto."
)

_SUMMARY_SYSTEM_DETAILED = (
    "Sei Pepe, assistente di Andrea. "
    "Produci un riassunto strutturato in italiano nel formato:\n"
    "1. Argomento principale (1 frase)\n"
    "2. Punti chiave (3-5 bullet)\n"
    "3. Conclusioni o azioni rilevanti (se presenti)\n"
    "Niente intro. Niente 'Ecco il riassunto'. Inizia con il numero 1."
)

_CHUNK_SYSTEM = (
    "Riassumi questo estratto in max 3 frasi. Solo fatti, niente commenti."
)

_MERGE_SYSTEM = (
    "Sei Pepe, assistente di Andrea. "
    "Unisci questi riassunti parziali in un unico riassunto coerente in italiano. "
    "Formato identico a un riassunto dettagliato: argomento, punti chiave, conclusioni. "
    "Niente ripetizioni. Niente intro. Max 300 parole."
)


class SummarizeAgent(AgentBase):
    """Riassume testi da URL, file e testo inline. Map-reduce per testi lunghi."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        super().__init__(
            name="summarize",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self._extractor = TextExtractor(max_chars=_SUMMARIZE_MAX_CHARS * 3)

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        inp = task.input_data or {}
        url: str | None = inp.get("url")
        text: str | None = inp.get("text")
        file_id: str | None = inp.get("file_id")
        mode: str = inp.get("mode", "detailed")   # "short" | "detailed"

        # ── Step 1: estrazione testo ─────────────────────────────────
        source_label = "testo inline"

        if url:
            await self._log_step("extract", f"Estrazione da URL: {url[:60]}")
            text = await self._extractor.from_url(url)
            source_label = url[:60]
            if not text:
                return self._fail(
                    f"Impossibile estrarre testo da {url}. "
                    "La pagina potrebbe essere protetta da paywall, richiedere JS o essere offline."
                )

        elif file_id:
            bot_token = getattr(settings, "TELEGRAM_TOKEN", "")
            if not bot_token:
                return self._fail("TELEGRAM_TOKEN non configurato — impossibile scaricare allegato")
            await self._log_step("extract", f"Download allegato Telegram: {file_id[:20]}")
            text, ext = await self._extractor.from_telegram_file(
                bot_token=bot_token, file_id=file_id
            )
            source_label = f"allegato Telegram ({ext})"
            if not text:
                return self._fail(
                    f"Impossibile estrarre testo dall'allegato ({file_id[:20]}). "
                    "Formati supportati: PDF, TXT, MD."
                )

        elif not text:
            return self._fail("Fornisci almeno uno tra: url, file_id o text")

        text = text.strip()
        n_chars = len(text)
        await self._log_step("analyze", f"Testo ottenuto: {n_chars} caratteri")

        # ── Step 2: summary ──────────────────────────────────────────
        if n_chars > _SUMMARIZE_MAX_CHARS:
            await self._log_step("map_reduce", f"Testo lungo ({n_chars} chars) — map-reduce")
            summary = await self._map_reduce_summary(text, mode)
        else:
            await self._log_step("summarize", "Summary diretto")
            summary = await self._direct_summary(text, mode)

        if not summary:
            return self._fail("Sintesi fallita — risposta LLM vuota")

        # ── Step 3: output ───────────────────────────────────────────
        reply = f"📄 Riassunto da: {source_label}\n{'─' * 30}\n{summary}"

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "summary": summary,
                "reply": reply,
                "source": source_label,
                "chars_extracted": n_chars,
                "mode": mode,
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # Summary diretto (testo breve)
    # ------------------------------------------------------------------

    async def _direct_summary(self, text: str, mode: str) -> str:
        system = _SUMMARY_SYSTEM_DETAILED if mode == "detailed" else _SUMMARY_SYSTEM_SHORT
        try:
            return await self._call_llm(
                messages=[{"role": "user", "content": f"Testo:\n\n{text}"}],
                system_prompt=system,
                max_tokens=600,
                domain_name="etsy_store",   # → Anthropic Haiku
            )
        except Exception as exc:
            logger.warning("Direct summary fallito, provo Ollama: %s", exc)
            return await self._call_llm_ollama(
                system=system,
                user=f"Testo:\n\n{text[:8000]}",
                max_tokens=600,
                temperature=0.3,
            )

    # ------------------------------------------------------------------
    # Map-reduce summary (testo lungo)
    # ------------------------------------------------------------------

    async def _map_reduce_summary(self, text: str, mode: str) -> str:
        """Divide in chunk, riassume ognuno, poi merge finale."""
        chunks = self._extractor.chunk_text(
            text, max_chars=3_000, overlap=200
        )
        await self._log_step("map", f"Map: {len(chunks)} chunk da riassumere")

        # MAP: riassunto parallelo di ogni chunk (batch da 4 per non saturare Ollama)
        chunk_summaries: list[str] = []
        for i in range(0, len(chunks), 4):
            batch = chunks[i : i + 4]
            import asyncio
            results = await asyncio.gather(
                *[self._summarize_chunk(c, idx=i + j) for j, c in enumerate(batch)],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, str) and r:
                    chunk_summaries.append(r)
                elif isinstance(r, Exception):
                    logger.debug("Chunk summary fallito: %s", r)

        if not chunk_summaries:
            return ""

        # Se un solo chunk (o pochi), ritorna direttamente
        if len(chunk_summaries) == 1:
            return chunk_summaries[0]

        # REDUCE: merge dei riassunti parziali
        await self._log_step("reduce", f"Reduce: merge di {len(chunk_summaries)} riassunti parziali")
        merged_input = "\n\n---\n\n".join(
            f"[Parte {i+1}]\n{s}" for i, s in enumerate(chunk_summaries)
        )
        system = _MERGE_SYSTEM
        try:
            return await self._call_llm(
                messages=[{"role": "user", "content": f"Riassunti parziali:\n\n{merged_input}"}],
                system_prompt=system,
                max_tokens=800,
                domain_name="etsy_store",
            )
        except Exception as exc:
            logger.warning("Merge summary fallito, provo Ollama: %s", exc)
            return await self._call_llm_ollama(
                system=system,
                user=f"Riassunti parziali:\n\n{merged_input[:6000]}",
                max_tokens=600,
                temperature=0.3,
            )

    async def _summarize_chunk(self, chunk: str, idx: int = 0) -> str:
        """Riassume un singolo chunk via Ollama (locale, veloce)."""
        try:
            return await self._call_llm_ollama(
                system=_CHUNK_SYSTEM,
                user=chunk,
                max_tokens=150,
                temperature=0.1,
            )
        except Exception as exc:
            logger.debug("Chunk %d summary fallito: %s", idx, exc)
            return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _log_step(self, step_type: str, description: str) -> None:
        self._step_counter += 1
        await self._broadcast({
            "type": "agent_step",
            "agent": self.name,
            "step": self._step_counter,
            "step_type": step_type,
            "description": description,
        })

    def _fail(self, reason: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            task_id="",
            status=TaskStatus.FAILED,
            output_data={"error": reason},
        )
