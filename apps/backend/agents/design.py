"""DesignAgent — genera digital products (PDF, PNG, SVG) per Etsy."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.storage import StorageManager
from apps.backend.tools.file_gen import (
    DEFAULT_SCHEMES,
    SCHEME_BY_NAME,
    ColorScheme,
    PDFGenerator,
)

logger = logging.getLogger("agentpexi.design")


def _niche_slug(niche: str, max_len: int = 40) -> str:
    """Converte niche in slug filesystem-safe."""
    slug = re.sub(r"[^a-z0-9]+", "_", niche.lower()).strip("_")
    return slug[:max_len]


class DesignAgent(AgentBase):
    """Agente per generazione digital products (Printable PDF focus)."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        storage: StorageManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        super().__init__(
            name="design",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self.storage = storage
        self._pdf_gen = PDFGenerator()

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        data = task.input_data or {}

        # --- 1. Verifica storage ---
        if not self.storage.is_available():
            raise RuntimeError(
                f"Storage non disponibile: {self.storage._base}. "
                "Verificare che l'SSD sia montato."
            )

        pq_task_id: str | None = data.get("production_queue_task_id")

        # --- 2. Aggiorna production_queue → in_progress ---
        if pq_task_id:
            await self.memory.update_production_queue_status(pq_task_id, "in_progress")

        # --- 3. Valida input ---
        niche: str = data.get("niche", "")
        product_type: str = data.get("product_type", "printable_pdf")
        template: str = data.get("template", "weekly_planner")
        size: str = data.get("size", "A4")
        num_variants: int = data.get("num_variants", 3)
        scheme_names: list[str] = data.get("color_schemes", [])
        keywords: list[str] = data.get("keywords", [])

        if not niche:
            raise ValueError("Campo 'niche' obbligatorio nel brief")

        # Solo printable_pdf supportato al momento
        if product_type == "digital_art_png":
            raise NotImplementedError(
                "PNG via Replicate non disponibile. Aggiungere REPLICATE_API_TOKEN al .env."
            )
        if product_type == "svg_bundle":
            raise NotImplementedError(
                "SVG generator non ancora implementato (previsto Fase 2C)."
            )

        # --- 4. Risolvi color schemes ---
        schemes = await self._resolve_schemes(scheme_names, num_variants)

        await self._log_step(
            "thinking",
            f"Generazione {len(schemes)} varianti {template} per niche '{niche}'",
            input_data={"niche": niche, "template": template, "schemes": [s.name for s in schemes]},
        )

        # --- 5. Prepara output directory ---
        output_dir = Path(self.storage._base) / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- 6. Genera varianti in parallelo (max 3 concurrent) ---
        sem = asyncio.Semaphore(3)
        slug = _niche_slug(niche)

        async def _gen_variant(scheme: ColorScheme) -> Path:
            filename = f"{slug}_{template}_{scheme.name}_{size}.pdf"
            out_path = output_dir / filename
            async with sem:
                path = await self._call_tool(
                    "pdf_generator",
                    f"generate_{template}",
                    {"scheme": scheme.name, "size": size, "output": str(out_path)},
                    self._pdf_gen.generate,
                    template,
                    scheme,
                    size,
                    out_path,
                )
            return path

        results = await asyncio.gather(
            *[_gen_variant(s) for s in schemes],
            return_exceptions=True,
        )

        # --- 7. Raccogli risultati ---
        file_paths: list[str] = []
        errors: list[str] = []
        total_size = 0

        for r in results:
            if isinstance(r, Exception):
                errors.append(str(r))
                logger.warning("Errore generazione variante: %s", r)
            else:
                p = Path(r)
                file_paths.append(str(p))
                if p.exists():
                    total_size += p.stat().st_size

        if not file_paths:
            if pq_task_id:
                await self.memory.update_production_queue_status(pq_task_id, "failed")
            raise RuntimeError(
                f"Nessun file generato. Errori: {errors}"
            )

        # --- 8. Aggiorna production_queue → completed ---
        if pq_task_id:
            await self.memory.update_production_queue_status(
                pq_task_id, "completed", file_paths=file_paths,
            )

        # --- 9. Log finale ---
        scheme_names_used = [s.name for s in schemes]
        summary = (
            f"Generati {len(file_paths)} file PDF ({total_size / 1024:.0f} KB totali), "
            f"schemi: {', '.join(scheme_names_used)}"
        )
        await self._log_step(
            "file_operation",
            summary,
            input_data={"template": template, "size": size},
            output_data={"file_paths": file_paths, "total_bytes": total_size},
        )

        # --- 10. Risultato ---
        output_data = {
            "file_paths": file_paths,
            "product_type": product_type,
            "template": template,
            "niche": niche,
            "num_variants": len(file_paths),
            "color_schemes": scheme_names_used,
            "keywords": keywords,
            "size": size,
        }
        if errors:
            output_data["errors"] = errors

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=output_data,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_schemes(
        self,
        names: list[str],
        num_variants: int,
    ) -> list[ColorScheme]:
        """Risolve color schemes: da nomi → oggetti ColorScheme.

        Se la lista è vuota o mancano nomi, usa i primi N default.
        Se servono più schemi di quanti disponibili, chiede all'LLM.
        """
        if names:
            resolved = [SCHEME_BY_NAME[n] for n in names if n in SCHEME_BY_NAME]
            if resolved:
                return resolved[:num_variants]

        # Nessun nome valido → usa default
        if num_variants <= len(DEFAULT_SCHEMES):
            return DEFAULT_SCHEMES[:num_variants]

        # Più varianti dei default → chiedi all'LLM di scegliere
        available = ", ".join(SCHEME_BY_NAME.keys())
        response = await self._call_llm(
            messages=[{
                "role": "user",
                "content": (
                    f"Scegli {num_variants} schemi colore adatti tra: {available}. "
                    "Puoi ripetere nomi se necessario. "
                    "Rispondi SOLO con un JSON array di nomi, es. [\"sage\", \"blush\"]."
                ),
            }],
            system_prompt="Sei un assistente per la selezione di palette colori per prodotti digitali Etsy.",
        )
        try:
            chosen = json.loads(response)
            if isinstance(chosen, list):
                result = [SCHEME_BY_NAME[n] for n in chosen if n in SCHEME_BY_NAME]
                if result:
                    return result[:num_variants]
        except (json.JSONDecodeError, KeyError):
            pass

        return DEFAULT_SCHEMES[:num_variants]
