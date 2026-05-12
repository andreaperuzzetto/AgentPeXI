"""PublisherAgent — pubblica listing su Etsy come draft con SEO generata via LLM."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents._publisher._crossref_mixin import _CrossrefMixin
from apps.backend.agents._publisher._publish_mixin import _PublishMixin
from apps.backend.agents._publisher._resolve_mixin import _ResolveMixin
from apps.backend.agents._publisher._seo_mixin import _SeoMixin
from apps.backend.agents._publisher._thumbnail_mixin import _ThumbnailMixin
from apps.backend.agents._publisher.constants import AB_PRICES, TAXONOMY_IDS
from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask
from apps.backend.core.production_queue import ProductionQueueService as _PQService
from apps.backend.core.storage import StorageManager

logger = logging.getLogger("agentpexi.publisher")

# Re-export constants so existing importers still work
__all__ = ["PublisherAgent", "TAXONOMY_IDS", "AB_PRICES"]


class PublisherAgent(_CrossrefMixin, _PublishMixin, _ResolveMixin, _ThumbnailMixin, _SeoMixin, AgentBase):
    """Pubblica file generati dal Design Agent su Etsy come draft listing."""

    card: ClassVar[AgentCard] = AgentCard(
        name="publisher",
        description="Pubblica listing Etsy con SEO, pricing e thumbnail verificati",
        input_schema={"file_paths": "list[str]", "niche": "str", "research_context": "dict"},
        layer="business",
        llm="sonnet",
        requires_confirmation=False,   # pubblica come draft, non live
        confidence_threshold=0.85,
        pipeline_position=3,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        storage: StorageManager,
        etsy_api: Any,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable | None = None,
        pinterest_agent: Any | None = None,
    ) -> None:
        super().__init__(
            name="publisher",
            model=MODEL_SONNET,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self.storage = storage
        self.etsy_api = etsy_api
        self._telegram_broadcast = telegram_broadcaster
        self._pinterest_agent = pinterest_agent
        # Prevents double-publication: two concurrent run() calls could both pass
        # the is_file() check before either moves the file. APScheduler max_instances=1
        # provides outer protection; this lock is the inner guard.
        self._publish_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        data = task.input_data or {}

        # --- Passo 1 — Validazione ---
        if not self.storage.is_available():
            raise RuntimeError("Storage non disponibile. Verificare SSD montato.")

        file_paths: list[str] = data.get("file_paths", [])
        thumbnail_paths_input: list[str] = data.get("thumbnail_paths", [])
        product_type: str = data.get("product_type", "printable_pdf")
        template: str = data.get("template", "")
        niche: str = data.get("niche", "")
        color_schemes: list[str] = data.get("color_schemes", [])
        keywords: list[str] = data.get("keywords", [])
        size: str = data.get("size", "A4")
        product_tier: str = data.get("product_tier", "core")  # AGT-3: ladder tier
        pq_task_id: str | None = data.get("production_queue_task_id")

        async with self._publish_lock:
            # Filtra file esistenti
            valid_paths: list[str] = []
            for fp in file_paths[:5]:  # max 5 per task
                if Path(fp).is_file():
                    valid_paths.append(fp)
                else:
                    logger.warning("File mancante, skip: %s", fp)

            if not valid_paths:
                raise RuntimeError("Nessun file valido trovato in file_paths")

            # --- Passo 2 — A/B assignment ---
            base_count = await self.memory.get_etsy_listings_count()

            publish_results: list[dict] = []
            errors: list[str] = []
            files_moved = 0

            # --- Passo 3 — Loop su file_paths ---
            for idx, file_path in enumerate(valid_paths):
                current_count = base_count + idx
                ab_variant = "A" if current_count % 2 == 0 else "B"
                color_scheme = color_schemes[idx] if idx < len(color_schemes) else ""

                try:
                    result = await self._publish_single(
                        file_path=file_path,
                        product_type=product_type,
                        template=template,
                        niche=niche,
                        color_scheme=color_scheme,
                        keywords=keywords,
                        size=size,
                        ab_variant=ab_variant,
                        product_tier=product_tier,
                        pq_task_id=pq_task_id,
                        research_data=data,
                        thumbnail_paths_input=thumbnail_paths_input,
                    )
                    publish_results.append(result)

                    # --- Passo 4 — Sposta file (solo se listing creato) ---
                    if result.get("listing_id"):
                        try:
                            self.storage.move_to_uploaded(Path(file_path))
                            files_moved += 1
                        except Exception as exc:
                            logger.warning("Errore spostamento file %s: %s", file_path, exc)

                except Exception as exc:
                    msg = f"Errore pubblicazione {Path(file_path).name}: {exc}"
                    logger.error(msg)
                    errors.append(msg)
                    publish_results.append({
                        "niche": niche,
                        "file_type": product_type,
                        "status": "error",
                        "listing_id": None,
                        "images_uploaded": 0,
                        "seo_validated": False,
                        "error": str(exc),
                    })

            # --- Passo 5 — Aggiorna production_queue → published ---
            listing_ids = [r["listing_id"] for r in publish_results if r.get("listing_id")]
            if pq_task_id and listing_ids:
                _pq = _PQService(await self.memory.get_db())
                _item = await _pq.get_item_by_task_id(pq_task_id)
                if _item is not None:
                    await _pq.set_published(_item.id, str(listing_ids[0]))

            # --- Passo 6 — Confidence + Status ---
            confidence, missing_data = self._calculate_publish_confidence(publish_results, data)
            status = self._calculate_status(publish_results)

            output = {
                "listings_created": len(listing_ids),
                "listing_ids": listing_ids,
                "ab_variants": {
                    "A": sum(1 for r in publish_results if r.get("ab_variant") == "A" and r.get("listing_id")),
                    "B": sum(1 for r in publish_results if r.get("ab_variant") == "B" and r.get("listing_id")),
                },
                "files_moved_to_uploaded": files_moved,
                "publish_details": publish_results,
            }
            if errors:
                output["errors"] = errors

            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=status,
                output_data=output,
                confidence=confidence,
                missing_data=missing_data,
                reply_voice="Pubblicazione completata, controlla il pannello e Telegram.",
            )

