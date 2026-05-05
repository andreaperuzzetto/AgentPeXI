from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import anthropic

from apps.backend.core.config import MODEL_SONNET, settings
from apps.backend.core._wiki._helpers import (
    _estimate_tokens,
    _DISTILL_SYSTEM,
    COMPACTION_LIMITS,
    DEFAULT_HARD_LIMIT,
    PERSONAL_HARD_LIMIT,
)

logger = logging.getLogger("agentpexi.wiki")


class _MaintenanceMixin:
    """Maintenance: wiki compaction, file distillation, LLM routing."""

    wiki_path: Path
    _manifest_lock: asyncio.Lock

    async def compact_wiki(self, domain: str, llm) -> dict[str, list]:
        """Per ogni file wiki che supera il limite hard, chiama LLM per distillare.

        Chiamata pubblica — invocata da pepe.run_wiki_health_check (Step 5.2.4)
        prima del lint, in modo che il report lavori già sui file compattati.
        """
        stats: dict[str, list] = {"compacted": [], "skipped": []}
        for wiki_file in self._iter_wiki_files(domain):
            try:
                text        = wiki_file.read_text(encoding="utf-8")
                token_count = _estimate_tokens(text)
                threshold   = COMPACTION_LIMITS.get(wiki_file.stem, DEFAULT_HARD_LIMIT)
                if domain == "personal":
                    threshold = PERSONAL_HARD_LIMIT

                if token_count > threshold:
                    target = int(threshold * 0.70)
                    await self._distill_file(wiki_file, llm, target=target)
                    stats["compacted"].append(wiki_file.name)
                    logger.info(
                        "_compact_wiki: %s (%d tok → target %d)", wiki_file.name, token_count, target
                    )
                else:
                    stats["skipped"].append(wiki_file.name)
            except Exception as exc:
                logger.error("_compact_wiki: errore su %s: %s", wiki_file.name, exc)
        return stats

    async def _distill_file(self, wiki_file: Path, llm, target: int = 1200) -> None:
        """Distilla un file wiki verboso. Scrittura atomica con .bak per safety.

        Pattern: copia .bak → scrivi nuovo → unlink .bak solo a successo → ripristino su eccezione.
        """
        original = wiki_file.read_text(encoding="utf-8")
        bak      = wiki_file.with_suffix(wiki_file.suffix + ".bak")
        bak.write_text(original, encoding="utf-8")  # backup prima di qualsiasi modifica
        try:
            system    = _DISTILL_SYSTEM.format(target_tokens=target)
            distilled = await self._llm_call(llm, system, original, max_tokens=target + 300)
            async with self._manifest_lock:          # serializza con compile_niche/compile_wiki_file
                wiki_file.write_text(distilled, encoding="utf-8")
            bak.unlink(missing_ok=True)             # pulizia .bak solo a successo
        except Exception:
            if bak.exists():
                async with self._manifest_lock:
                    wiki_file.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
                bak.unlink(missing_ok=True)
            raise

    async def _llm_call(
        self, llm, system: str, user: str, max_tokens: int = 2000
    ) -> str:
        """Chiamata LLM con routing automatico: Anthropic (Sonnet) o OpenAI-compat (Ollama)."""
        if isinstance(llm, anthropic.AsyncAnthropic):
            msg = await llm.messages.create(
                model=MODEL_SONNET,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text
        else:  # openai.AsyncOpenAI — Ollama
            resp = await llm.chat.completions.create(
                model=settings.OLLAMA_MODEL,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            return resp.choices[0].message.content or ""
