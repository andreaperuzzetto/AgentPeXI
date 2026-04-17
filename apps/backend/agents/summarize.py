"""SummarizeAgent — riassume testi da URL, file allegati Telegram e testo libero.

Input:
  {
    "source_type": "text" | "url" | "file",
    "content":     str,          # testo diretto, URL, o file_id Telegram
    "length":      "brief" | "normal" | "detailed",   # default: "normal"
    "save":        bool,         # default: True — salva summary in pepe_memory
  }

Pipeline:
1. Estrazione testo (by source_type) + source quality check
2. Chunking se testo > 3.000 chars, max SUMMARIZE_MAX_CHUNKS (5)
3. Sintesi progressiva (map-reduce se più chunk, diretta se singolo)
4. Action item detection (caveman Ollama) → propone reminder se trovati
5. Salvataggio pepe_memory (se save=True) via store_insight
6. Risposta Telegram

Usa Claude Haiku per velocità + cost control.
Fallback Ollama se Anthropic non raggiungibile.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools.text_extract import TextExtractor

logger = logging.getLogger("agentpexi.summarize")

# Chunking: soglia e limite chunk
_CHUNK_THRESHOLD: int = settings.SUMMARIZE_CHUNK_THRESHOLD
_MAX_CHUNKS: int = settings.SUMMARIZE_MAX_CHUNKS

# Controllo qualità sorgente — parole chiave che indicano estrazione fallita
_QUALITY_FAIL_KEYWORDS = ("enable javascript", "access denied", "cookie", "403", "404")

# Prompts sintesi per livello
_SUMMARY_PROMPTS = {
    "brief": "Riassumi in 3-5 frasi. Solo punti chiave. Italiano.",
    "normal": (
        "Riassumi in ~10 frasi. Struttura: tema principale, punti chiave, conclusione. Italiano."
    ),
    "detailed": (
        "Riassumi con sezioni. Usa bullet points. "
        "Includi dettagli tecnici rilevanti. Italiano."
    ),
}

# Step 4 — Action item detection (caveman)
_ACTION_SYSTEM = (
    "Find deadlines/actions in text. Output ONLY:\n"
    "SI: [azione 1] | [azione 2]\n"
    "oppure\n"
    "NO"
)

_CHUNK_SYSTEM = "Riassumi questo estratto in max 3 frasi. Solo fatti, niente commenti."

_MERGE_SYSTEM = (
    "Sei Pepe, assistente di Andrea. "
    "Unisci questi riassunti parziali in un unico riassunto coerente in italiano. "
    "Formato: argomento principale, punti chiave, conclusioni. "
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
        text_extractor: TextExtractor | None = None,
    ) -> None:
        super().__init__(
            name="summarize",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self._extractor = text_extractor or TextExtractor(
            max_chars=settings.SUMMARIZE_MAX_CHARS
        )

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        inp = task.input_data or {}
        source_type: str = inp.get("source_type", "text")
        content: str = inp.get("content", "").strip()
        length: str = inp.get("length", "normal")   # "brief" | "normal" | "detailed"
        save: bool = inp.get("save", True)

        if not content:
            return self._fail("content mancante — fornisci url, file_id o testo")
        if length not in _SUMMARY_PROMPTS:
            length = "normal"

        # ── Step 1: estrazione testo ─────────────────────────────────
        text: str | None = None
        source_label: str = "testo inline"

        if source_type == "url":
            await self._log_step("extract", f"Estrazione da URL: {content[:60]}")
            text = await self._extractor.from_url(content)
            source_label = content[:60]
            if not text:
                return self._fail(
                    "Non riesco ad accedere a questo URL. "
                    "Potrebbe essere: protetto da JavaScript, paywall, o non raggiungibile."
                )
            # Source quality check: prime 300 chars
            preview = text[:300].lower()
            if any(kw in preview for kw in _QUALITY_FAIL_KEYWORDS):
                return self._fail("La pagina non contiene testo leggibile.")

        elif source_type == "file":
            bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
            if not bot_token:
                return self._fail("TELEGRAM_BOT_TOKEN non configurato — impossibile scaricare allegato")
            await self._log_step("extract", f"Download allegato Telegram: {content[:20]}")
            result = await self._extractor.from_telegram_file(
                bot_token=bot_token, file_id=content
            )
            if result is None or result[0] is None:
                return self._fail("Formato non supportato. Tipi accettati: PDF, TXT, MD")
            text, ext = result
            source_label = f"allegato ({ext})"

        else:  # source_type == "text"
            text = content

        text = text.strip()
        n_chars = len(text)
        await self._log_step("analyze", f"Testo ottenuto: {n_chars} caratteri")

        # ── Step 2: chunking con limite MAX_CHUNKS ───────────────────
        if n_chars > _CHUNK_THRESHOLD:
            chunks = self._extractor.chunk_text(text, max_chars=_CHUNK_THRESHOLD, overlap=200)
            truncated = False
            if len(chunks) > _MAX_CHUNKS:
                chunks = chunks[:_MAX_CHUNKS]
                truncated = True
            await self._log_step("chunk", f"Chunking: {len(chunks)} chunk{' (troncato a 5)' if truncated else ''}")
        else:
            chunks = [text]

        # ── Step 3: sintesi ──────────────────────────────────────────
        if len(chunks) == 1:
            await self._log_step("summarize", "Summary diretto")
            summary = await self._direct_summary(chunks[0], length)
        else:
            await self._log_step("map_reduce", f"Map-reduce su {len(chunks)} chunk")
            summary = await self._map_reduce_summary_from_chunks(chunks, length)

        if not summary:
            return self._fail("Sintesi fallita — risposta LLM vuota")

        # ── Step 4: action item detection ────────────────────────────
        await self._log_step("actions", "Ricerca action items")
        action_items = await self._detect_action_items(summary)
        action_note = ""
        if action_items:
            items_str = " | ".join(f"«{a}»" for a in action_items)
            action_note = f"\n\n⚡ Azioni trovate: {items_str}\nVuoi che imposti un reminder per una di queste?"

        # ── Step 5: salvataggio pepe_memory ─────────────────────────
        if save:
            from datetime import datetime as _dt
            try:
                await self.memory.store_insight(
                    summary,
                    metadata={
                        "source_type": source_type,
                        "url": content if source_type == "url" else None,
                        "tag": "summary",
                        "length": length,
                        "date": _dt.utcnow().strftime("%Y-%m-%d"),
                        "created_at": _dt.utcnow().isoformat(),
                    },
                )
                await self._log_step("save", "Salvato in pepe_memory")
            except Exception as exc:
                logger.warning("store_insight fallito (fail-safe): %s", exc)

        # ── Step 6: risposta ─────────────────────────────────────────
        trunc_warn = "\n⚠️ Documento molto lungo, ho processato i primi ~15.000 caratteri." if (n_chars > _CHUNK_THRESHOLD and len(chunks) == _MAX_CHUNKS) else ""
        reply = f"📄 Riassunto da: {source_label}\n{'─' * 30}\n{summary}{action_note}{trunc_warn}"

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "summary": summary,
                "reply": reply,
                "source": source_label,
                "chars_extracted": n_chars,
                "length": length,
                "action_items": action_items,
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # Summary diretto (testo breve)
    # ------------------------------------------------------------------

    async def _direct_summary(self, text: str, length: str) -> str:
        system = _SUMMARY_PROMPTS.get(length, _SUMMARY_PROMPTS["normal"])
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

    async def _map_reduce_summary_from_chunks(self, chunks: list[str], length: str) -> str:
        """Riassume chunk pre-calcolati, poi merge finale."""
        await self._log_step("map", f"Map: {len(chunks)} chunk da riassumere")

        # MAP: riassunto parallelo di ogni chunk (batch da 4 per non saturare Ollama)
        chunk_summaries: list[str] = []
        for i in range(0, len(chunks), 4):
            batch = chunks[i : i + 4]
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
        try:
            return await self._call_llm(
                messages=[{"role": "user", "content": f"Riassunti parziali:\n\n{merged_input}"}],
                system_prompt=_MERGE_SYSTEM,
                max_tokens=800,
                domain_name="etsy_store",
            )
        except Exception as exc:
            logger.warning("Merge summary fallito, provo Ollama: %s", exc)
            return await self._call_llm_ollama(
                system=_MERGE_SYSTEM,
                user=f"Riassunti parziali:\n\n{merged_input[:6000]}",
                max_tokens=600,
                temperature=0.3,
            )

    async def _detect_action_items(self, summary_text: str) -> list[str]:
        """Step 4: Ollama caveman cerca deadline/azioni nel riassunto.

        Ritorna lista di azioni trovate, oppure lista vuota.
        """
        try:
            result = await self._call_llm_ollama(
                system=_ACTION_SYSTEM,
                user=summary_text[:1000],
                max_tokens=80,
                temperature=0.0,
            )
            raw = (result or "").strip()
            if raw.upper().startswith("NO") or not raw:
                return []
            if raw.upper().startswith("SI:"):
                items_str = raw[3:].strip()
                items = [i.strip().strip("[]") for i in items_str.split("|") if i.strip()]
                return [i for i in items if i]
        except Exception as exc:
            logger.debug("_detect_action_items fallito (fail-safe): %s", exc)
        return []

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

    def _fail(self, reason: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            task_id=self._task_id,
            status=TaskStatus.FAILED,
            output_data={"error": reason},
        )
