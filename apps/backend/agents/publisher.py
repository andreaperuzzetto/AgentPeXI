"""PublisherAgent — pubblica listing su Etsy come draft con SEO generata via LLM."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.storage import StorageManager

logger = logging.getLogger("agentpexi.publisher")

# ------------------------------------------------------------------
# Costanti
# ------------------------------------------------------------------

TAXONOMY_IDS = {
    "printable_pdf": 2078,      # Prints > Digital Prints
    "digital_art_png": 2078,
    "svg_bundle": 1,            # placeholder
}

AB_PRICES = {
    "printable_pdf": {"A": 2.99, "B": 4.99},
    "digital_art_png": {"A": 3.99, "B": 6.99},
    "svg_bundle": {"A": 5.99, "B": 9.99},
}

_SEO_SYSTEM_PROMPT = """\
Sei un esperto di Etsy SEO per prodotti digitali printable.
Rispondi SOLO con JSON valido, nessun testo aggiuntivo:
{"title": "...", "description": "...", "tags": ["...", ...]}

Regole titolo: max 140 caratteri, keyword principale all'inizio,
  includi tipo prodotto e formato (es. "A4 PDF Printable").
Regole descrizione: 150-300 parole, prima riga con keyword principale,
  3-5 bullet point benefici, menzione "instant download", call to action finale.
  NON usare markdown nella descrizione — testo plain con a capo normali.
Regole tag: esattamente 13 tag, max 20 caratteri ciascuno,
  mix broad + long-tail, nessuna ripetizione tra tag e nessuna ripetizione
  di parole già nel titolo ove possibile.\
"""


class PublisherAgent(AgentBase):
    """Pubblica file generati dal Design Agent su Etsy come draft listing."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        storage: StorageManager,
        etsy_api: Any,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable | None = None,
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

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        data = task.input_data or {}

        # --- Passo 1 — Validazione ---
        if not self.storage.is_available():
            raise RuntimeError("Storage non disponibile. Verificare SSD montato.")

        file_paths: list[str] = data.get("file_paths", [])
        product_type: str = data.get("product_type", "printable_pdf")
        template: str = data.get("template", "")
        niche: str = data.get("niche", "")
        color_schemes: list[str] = data.get("color_schemes", [])
        keywords: list[str] = data.get("keywords", [])
        size: str = data.get("size", "A4")
        pq_task_id: str | None = data.get("production_queue_task_id")

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

        listing_ids: list[str] = []
        ab_counts = {"A": 0, "B": 0}
        errors: list[str] = []
        files_moved = 0

        # --- Passo 3 — Loop su file_paths ---
        for idx, file_path in enumerate(valid_paths):
            current_count = base_count + idx
            ab_variant = "A" if current_count % 2 == 0 else "B"
            color_scheme = color_schemes[idx] if idx < len(color_schemes) else ""

            try:
                lid = await self._publish_single(
                    file_path=file_path,
                    product_type=product_type,
                    template=template,
                    niche=niche,
                    color_scheme=color_scheme,
                    keywords=keywords,
                    size=size,
                    ab_variant=ab_variant,
                    pq_task_id=pq_task_id,
                )
                listing_ids.append(lid)
                ab_counts[ab_variant] += 1

                # --- Passo 4 — Sposta file ---
                try:
                    self.storage.move_to_uploaded(Path(file_path))
                    files_moved += 1
                except Exception as exc:
                    logger.warning("Errore spostamento file %s: %s", file_path, exc)

            except Exception as exc:
                msg = f"Errore pubblicazione {Path(file_path).name}: {exc}"
                logger.error(msg)
                errors.append(msg)

        # --- Passo 5 — Aggiorna production_queue ---
        if pq_task_id and listing_ids:
            await self.memory.update_production_queue_status(pq_task_id, "completed")

        # --- Passo 6 — AgentResult ---
        output = {
            "listings_created": len(listing_ids),
            "listing_ids": listing_ids,
            "ab_variants": ab_counts,
            "files_moved_to_uploaded": files_moved,
        }
        if errors:
            output["errors"] = errors

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=output,
        )

    # ------------------------------------------------------------------
    # Pubblicazione singolo file
    # ------------------------------------------------------------------

    async def _publish_single(
        self,
        file_path: str,
        product_type: str,
        template: str,
        niche: str,
        color_scheme: str,
        keywords: list[str],
        size: str,
        ab_variant: str,
        pq_task_id: str | None,
    ) -> str:
        """Pubblica un singolo file come draft su Etsy. Ritorna listing_id."""
        # 3a. Generazione SEO via LLM
        seo = await self._generate_seo(
            niche=niche,
            template=template,
            keywords=keywords,
            color_scheme=color_scheme,
            size=size,
        )

        title = seo["title"]
        description = seo["description"]
        tags = seo["tags"]

        # 3b. Prezzo
        price = AB_PRICES.get(product_type, AB_PRICES["printable_pdf"])[ab_variant]

        # 3c. Crea draft su Etsy
        response = await self._call_tool(
            "etsy_api",
            "create_listing",
            {"title": title, "price": price, "tags": tags},
            self.etsy_api.create_listing,
            title=title,
            description=description,
            price=price,
            tags=tags,
            taxonomy_id=TAXONOMY_IDS.get(product_type, 2078),
            state="draft",
            type="download",
            who_made="i_did",
            when_made="2020_2025",
            is_digital=True,
            quantity=999,
        )

        listing_id = str(response.get("listing_id", ""))
        if not listing_id:
            raise RuntimeError(f"Etsy non ha restituito listing_id: {response}")

        # 3d. Upload file
        await self._call_tool(
            "etsy_api",
            "upload_file",
            {"listing_id": listing_id, "file": Path(file_path).name},
            self.etsy_api.upload_file,
            listing_id=int(listing_id),
            file_path=file_path,
            name=Path(file_path).name,
        )

        # 3e. Salvataggio in SQLite
        await self.memory.add_etsy_listing(
            listing_id=listing_id,
            production_queue_task_id=pq_task_id,
            title=title,
            tags=tags,
            product_type=product_type,
            niche=niche,
            template=template,
            color_scheme=color_scheme,
            size=size,
            ab_price_variant=ab_variant,
            price_eur=price,
            file_path=file_path,
        )

        # 3f. Notifica Telegram
        msg = (
            f"🆕 Draft Etsy creato!\n"
            f"📦 {title[:70]}\n"
            f"💰 Prezzo: €{price:.2f} (variante {ab_variant})\n"
            f"🎨 Schema: {color_scheme}\n"
            f"📋 Nicchia: {niche}\n\n"
            f"✅ Approva: https://www.etsy.com/your-shop/tools/listings/drafts\n"
            f"🔗 Listing ID: {listing_id}\n\n"
            f"#draft #{template} #{color_scheme}"
        )
        await self._notify_telegram(msg)

        return listing_id

    # ------------------------------------------------------------------
    # SEO generation
    # ------------------------------------------------------------------

    async def _generate_seo(
        self,
        niche: str,
        template: str,
        keywords: list[str],
        color_scheme: str,
        size: str,
    ) -> dict:
        """Genera title, description, tags via LLM. Retry una volta se JSON malformato."""
        user_prompt = (
            f"Nicchia: {niche}\n"
            f"Template: {template}\n"
            f"Keywords target: {', '.join(keywords) if keywords else 'nessuna'}\n"
            f"Schema colore: {color_scheme}\n"
            f"Formato: {size}"
        )

        for attempt in range(2):
            response_text = await self._call_llm(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=_SEO_SYSTEM_PROMPT,
            )
            parsed = self._parse_seo_json(response_text)
            if parsed:
                return parsed
            logger.warning("SEO JSON malformato (tentativo %d): %s", attempt + 1, response_text[:200])

        raise RuntimeError("LLM non ha generato JSON SEO valido dopo 2 tentativi")

    @staticmethod
    def _parse_seo_json(text: str) -> dict | None:
        """Estrae JSON SEO dalla risposta LLM."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()
        try:
            data = json.loads(cleaned)
            if "title" in data and "description" in data and "tags" in data:
                # Sanitize
                data["title"] = str(data["title"])[:140]
                data["tags"] = [str(t)[:20] for t in data["tags"]][:13]
                return data
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    # ------------------------------------------------------------------
    # Notifica Telegram
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass
