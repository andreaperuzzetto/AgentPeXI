"""PublisherAgent — pubblica listing su Etsy come draft con SEO generata via LLM."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_SONNET, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus
from apps.backend.core.storage import StorageManager

logger = logging.getLogger("agentpexi.publisher")

# ------------------------------------------------------------------
# Costanti
# ------------------------------------------------------------------

TAXONOMY_IDS = {
    "printable_pdf": 2078,      # Prints > Digital Prints
    "digital_art_png": 2078,
    # svg_bundle: il taxonomy ID reale deve essere configurato.
    # Usa l'endpoint GET /v3/application/seller-taxonomy/nodes per trovarlo.
    # Lasciare a 0 blocca intenzionalmente la pubblicazione fino alla configurazione.
    "svg_bundle": 0,
}

AB_PRICES = {
    "printable_pdf": {"A": 2.99, "B": 4.99},
    "digital_art_png": {"A": 3.99, "B": 6.99},
    "svg_bundle": {"A": 5.99, "B": 9.99},
}


class PublisherAgent(AgentBase):
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

    def _extra_init_kwargs(self) -> dict:
        return {
            "storage": self.storage,
            "etsy_api": self.etsy_api,
            "telegram_broadcaster": self._telegram_broadcast,
        }

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

        # --- Passo 5 — Aggiorna production_queue ---
        listing_ids = [r["listing_id"] for r in publish_results if r.get("listing_id")]
        if pq_task_id and listing_ids:
            await self.memory.update_production_queue_status(pq_task_id, "completed")

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
        research_data: dict,
        thumbnail_paths_input: list[str] | None = None,
    ) -> dict:
        """Pubblica un singolo file come draft su Etsy. Ritorna dict con dettagli."""
        result: dict[str, Any] = {
            "niche": niche,
            "file_type": product_type,
            "ab_variant": ab_variant,
            "listing_id": None,
            "images_uploaded": 0,
            "seo_validated": False,
            "price_source": "fallback_hardcoded",
        }

        # 3a. Thumbnail check — blocca se mancanti
        thumbnails_ok, thumbnail_paths = await self._check_thumbnails(
            niche, product_type,
            pdf_path=file_path,
            explicit_paths=thumbnail_paths_input or [],
        )
        if not thumbnails_ok:
            logger.error("SKIP: Listing %s non pubblicato — thumbnail mancanti", niche)
            result["status"] = "skipped_no_thumbnails"
            result["error"] = "Thumbnail non trovati — eseguire Playwright prima di pubblicare"
            return result

        # 3b. Failure history check
        adjustments = await self._check_failure_history(niche, research_data)
        if adjustments:
            result["failure_adjustments"] = adjustments

        # 3c. Generazione SEO via LLM (con dati Research)
        seo = await self._generate_seo(
            niche=niche,
            template=template,
            keywords=keywords,
            color_scheme=color_scheme,
            size=size,
            research_data=research_data,
        )

        title = seo["title"]
        description = seo["description"]
        tags = seo["tags"]
        result["seo_validated"] = seo.get("seo_validated", False)
        if seo.get("seo_issues"):
            result["seo_issues"] = seo["seo_issues"]

        # 3d. Prezzo research-driven
        price = self._resolve_price(product_type, research_data, variant=ab_variant.lower())
        result["price_source"] = (
            "research" if research_data.get("pricing", {}).get("launch_price_usd") else "fallback_hardcoded"
        )

        # 3e. Crea draft su Etsy
        taxonomy_id = TAXONOMY_IDS.get(product_type, 2078)
        if taxonomy_id == 0:
            raise RuntimeError(
                f"TAXONOMY_IDS['{product_type}'] non è ancora configurato. "
                "Usa GET /v3/application/seller-taxonomy/nodes per trovare il taxonomy ID "
                "Etsy corretto e aggiornalo in publisher.py prima di pubblicare."
            )
        response = await self._call_tool(
            "etsy_api",
            "create_listing",
            {"title": title, "price": price, "tags": tags},
            self.etsy_api.create_listing,
            title=title,
            description=description,
            price=price,
            tags=tags,
            taxonomy_id=taxonomy_id,
            state="draft",
            type="download",
            who_made="i_did",
            when_made=self._get_when_made(),
            is_digital=True,
            quantity=999,
        )

        listing_id = str(response.get("listing_id", ""))
        if not listing_id:
            raise RuntimeError(f"Etsy non ha restituito listing_id: {response}")
        result["listing_id"] = listing_id

        # 3f. Upload file
        await self._call_tool(
            "etsy_api",
            "upload_file",
            {"listing_id": listing_id, "file": Path(file_path).name},
            self.etsy_api.upload_file,
            listing_id=listing_id,
            file_path=file_path,
            name=Path(file_path).name,
        )

        # 3g. Upload thumbnail
        uploaded_count = 0
        for thumb_path in thumbnail_paths:
            try:
                await self._call_tool(
                    "etsy_api",
                    "upload_image",
                    {"listing_id": listing_id, "image": thumb_path.name},
                    self.etsy_api.upload_image,
                    listing_id=listing_id,
                    file_path=str(thumb_path),
                )
                uploaded_count += 1
            except Exception as exc:
                logger.warning("Errore upload thumbnail %s: %s", thumb_path.name, exc)
        result["images_uploaded"] = uploaded_count

        # 3h. Salvataggio in SQLite
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

        # 3i. Notifica Telegram
        seo_status = "validato" if result["seo_validated"] else "non validato"
        price_src = "research" if result["price_source"] == "research" else "fallback"
        schema_detail = f" | schema: {color_scheme}" if color_scheme else ""
        seo_issues = ""
        if result.get("seo_issues"):
            seo_issues = f"SEO issues: {', '.join(result['seo_issues'][:2])}\n"
        msg = (
            f"Draft creato — {niche}\n"
            f"{'─' * 14}\n"
            f"Titolo: {title[:70]}\n"
            f"Prezzo: \u20ac{price:.2f} (variante {ab_variant}, fonte: {price_src}){schema_detail}\n"
            f"Immagini: {uploaded_count}/3  |  SEO: {seo_status}\n"
            f"{seo_issues}"
            f"ID: {listing_id}\n"
            f"Approva: https://www.etsy.com/your-shop/tools/listings/drafts"
        )
        await self._notify_telegram(msg)

        result["status"] = "published"
        return result

    # ------------------------------------------------------------------
    # SEO generation
    # ------------------------------------------------------------------

    def _build_seo_system_prompt(self, selling_signals: dict) -> str:
        """System prompt SEO dinamico con selling_signals e contesto stagionale."""
        thumbnail_style = selling_signals.get("thumbnail_style", "clean mockup")
        conversion_triggers = selling_signals.get("conversion_triggers", [])
        bundle_vs_single = selling_signals.get("bundle_vs_single", "single")

        trigger_instructions = ""
        if conversion_triggers:
            trigger_instructions = (
                "\nLINGUAGGIO DI CONVERSIONE (usa questi concetti nella description):\n"
                + "\n".join(f"- {t}" for t in conversion_triggers[:3])
            )

        bundle_instruction = ""
        if bundle_vs_single == "bundle":
            bundle_instruction = (
                "\nEnfatizza il VALORE del bundle: più file, più risparmio "
                "rispetto all'acquisto singolo."
            )

        seasonal = self._get_seasonal_context()
        seasonal_instruction = ""
        if seasonal["keywords"]:
            seasonal_instruction = (
                f"\nCONTESTO STAGIONALE ({seasonal['season']}):\n"
                f"Se rilevante per la niche, considera di includere nel title o description:\n"
                f"{', '.join(seasonal['keywords'][:3])}\n"
                f"Non forzarlo se non c'entra con il prodotto."
            )

        return (
            "Sei un copywriter Etsy specializzato in prodotti digitali stampabili.\n"
            "Il tuo obiettivo è massimizzare conversioni, non solo ottimizzare per search.\n\n"
            f"STILE THUMBNAIL DA MENZIONARE NELLA DESCRIPTION: {thumbnail_style}\n"
            f"{trigger_instructions}\n"
            f"{bundle_instruction}\n"
            f"{seasonal_instruction}\n\n"
            "REGOLE ASSOLUTE:\n"
            "1. Title: keyword principale PRIMA di tutto, benefit nei primi 60 chars\n"
            "2. Description: prima riga ottimizzata per Etsy search preview (150 chars max)\n"
            "3. Bullet points con \u2022 per le caratteristiche\n"
            "4. Tags: usa ESATTAMENTE la lista fornita, non modificare\n"
            "5. Nessun claim falso (no \"best seller\", \"award winning\")\n"
            "6. Sempre in inglese\n"
            '7. Rispondi SOLO con JSON valido: {"title": "...", "description": "...", "tags": [...]}\n'
        )

    async def _generate_seo(
        self,
        niche: str,
        template: str,
        keywords: list[str],
        color_scheme: str,
        size: str,
        research_data: dict,
    ) -> dict:
        """Genera title, description, tags via LLM usando dati Research. Retry una volta se JSON malformato."""
        etsy_tags_13 = research_data.get("etsy_tags_13", [])
        selling_signals = research_data.get("selling_signals", {})
        conversion_triggers = selling_signals.get("conversion_triggers", [])
        bundle_vs_single = selling_signals.get("bundle_vs_single", "single")
        thumbnail_style = selling_signals.get("thumbnail_style", "")

        system_prompt = self._build_seo_system_prompt(selling_signals)

        # Build user message con dati Research
        tags_instruction = ""
        if etsy_tags_13:
            tags_instruction = (
                "\nTAG ETSY OBBLIGATORI (usa esattamente questi 13, sono già ottimizzati da Research):\n"
                f"{json.dumps(etsy_tags_13, ensure_ascii=False)}\n\n"
                "IMPORTANTE: Il campo \"tags\" DEVE essere esattamente la lista fornita sopra, non generarne di nuovi.\n"
            )

        signals_section = ""
        if selling_signals:
            signals_section = (
                "\nSEGNALI DI VENDITA DA RESEARCH:\n"
                f"- Stile thumbnail vincente: {thumbnail_style}\n"
                f"- Trigger di conversione: {', '.join(conversion_triggers)}\n"
                f"- Formato consigliato: {bundle_vs_single}\n"
            )

        first_keyword = etsy_tags_13[0] if etsy_tags_13 else niche
        tags_json = json.dumps(etsy_tags_13) if etsy_tags_13 else '["...", ...]'

        user_prompt = (
            f"Crea il listing Etsy per: {niche} ({size})\n"
            f"Template: {template}\n"
            f"Schema colore: {color_scheme}\n"
            f"Keywords target: {', '.join(keywords) if keywords else 'nessuna'}\n"
            f"{tags_instruction}"
            f"{signals_section}\n"
            f"REGOLE TITLE:\n"
            f"- Inizia con la keyword principale: \"{first_keyword}\"\n"
            f"- Max 140 caratteri\n"
            f"- Includi il benefit principale nei primi 60 caratteri\n\n"
            f"REGOLE DESCRIPTION:\n"
            f"- Prima riga: keyword principale + benefit immediato (per Etsy search preview)\n"
            f"- 150-300 parole\n"
            f"- Bullet points per caratteristiche (\u2022)\n"
            f"- Includi: cosa ricevi, come scaricarlo, come usarlo\n"
            + (f"- Usa il linguaggio dei conversion_triggers sopra\n" if conversion_triggers else "")
            + f"\nOUTPUT JSON:\n"
            f'{{\"title\": \"...\", \"description\": \"...\", \"tags\": {tags_json}}}\n'
        )

        for attempt in range(2):
            response_text = await self._call_llm(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=system_prompt,
            )
            parsed = self._parse_seo_json(response_text, etsy_tags_13=etsy_tags_13)
            if parsed:
                return parsed
            logger.warning("SEO JSON malformato (tentativo %d): %s", attempt + 1, response_text[:200])

        raise RuntimeError("LLM non ha generato JSON SEO valido dopo 2 tentativi")

    def _parse_seo_json(self, text: str, etsy_tags_13: list[str] | None = None) -> dict | None:
        """Estrae e valida JSON SEO dalla risposta LLM con quality check."""
        cleaned = text.strip()
        # Estrai JSON grezzo da qualsiasi wrapper (```json ... ```, ``` ... ```, o testo libero)
        # Strategia: trova la prima { e l'ultima } — robusto su JSON annidati
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
        try:
            data = json.loads(cleaned)
            if not ("title" in data and "description" in data and "tags" in data):
                return None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

        # --- SEO quality validation ---
        issues: list[str] = []
        seo_validated = True

        title = str(data.get("title", ""))
        description = str(data.get("description", ""))
        tags = data.get("tags", [])

        # Title validation
        if len(title) > 140:
            data["title"] = title[:140]
            issues.append("title troncato a 140 chars")
        if len(title) < 30:
            issues.append("title troppo corto (<30 chars)")
            seo_validated = False

        # Description bullet points
        if "\u2022" not in description and "- " not in description:
            issues.append("description senza bullet points")
            seo_validated = False

        # Description length
        if len(description) < 150:
            issues.append(f"description troppo corta ({len(description)} chars, min 150)")
            seo_validated = False

        # Tags — override con Research se disponibili
        if etsy_tags_13:
            data["tags"] = etsy_tags_13
        else:
            if len(tags) < 10:
                issues.append(f"solo {len(tags)} tag (min 10)")
                seo_validated = False
            data["tags"] = [str(t)[:20] for t in tags[:13]]

        if issues:
            logger.warning("SEO issues: %s", issues)

        data["seo_validated"] = seo_validated
        data["seo_issues"] = issues
        return data

    # ------------------------------------------------------------------
    # Confidence + Status scoring
    # ------------------------------------------------------------------

    def _calculate_publish_confidence(
        self, results: list[dict], task_context: dict,
    ) -> tuple[float, list[str]]:
        """Calcola confidence basata su qualità oggettiva del publishing."""
        missing: list[str] = []
        score = 0.0

        # 40% — dati Research presenti e usati
        research_score = 0.0
        if task_context.get("etsy_tags_13"):
            research_score += 0.20
        else:
            missing.append("etsy_tags_13 mancanti da Research Agent")
        if task_context.get("selling_signals"):
            research_score += 0.10
        else:
            missing.append("selling_signals mancanti da Research Agent")
        if task_context.get("pricing", {}).get("launch_price_usd"):
            research_score += 0.10
        else:
            missing.append("pricing da Research mancante — usato prezzo hardcoded")
        score += research_score

        # 35% — success rate listing pubblicati
        if results:
            successful = sum(1 for r in results if r.get("listing_id"))
            success_rate = successful / len(results)
            score += 0.35 * success_rate
            if success_rate < 1.0:
                missing.append(f"{len(results) - successful} listing su {len(results)} falliti")
        else:
            missing.append("Nessun listing pubblicato")

        # 15% — thumbnail caricate
        if results:
            with_images = sum(1 for r in results if r.get("images_uploaded", 0) > 0)
            image_rate = with_images / len(results)
            score += 0.15 * image_rate
            if image_rate < 1.0:
                missing.append(f"{len(results) - with_images} listing senza thumbnail")

        # 10% — SEO validato
        if results:
            valid_seo = sum(1 for r in results if r.get("seo_validated", False))
            score += 0.10 * (valid_seo / len(results))

        return round(score, 2), missing

    def _calculate_status(self, results: list[dict]) -> TaskStatus:
        """COMPLETED: 100% pubblicati. PARTIAL: 50-99%. FAILED: <50% o 0."""
        if not results:
            return TaskStatus.FAILED

        successful = sum(1 for r in results if r.get("listing_id"))
        total = len(results)
        ratio = successful / total

        if ratio == 1.0:
            return TaskStatus.COMPLETED
        elif ratio >= 0.5:
            return TaskStatus.PARTIAL
        else:
            return TaskStatus.FAILED

    # ------------------------------------------------------------------
    # Pricing research-driven
    # ------------------------------------------------------------------

    def _resolve_price(self, file_type: str, research_data: dict, variant: str = "a") -> float:
        """Usa il prezzo da Research se disponibile, fallback su AB_PRICES."""
        pricing = research_data.get("pricing", {})

        if variant.lower() == "a" and pricing.get("launch_price_usd"):
            usd = float(pricing["launch_price_usd"])
            return round(usd * settings.USD_EUR_RATE, 2)
        elif variant.lower() == "b" and pricing.get("mature_price_usd"):
            usd = float(pricing["mature_price_usd"])
            return round(usd * settings.USD_EUR_RATE, 2)

        # Fallback su AB_PRICES
        ab_key = variant.upper()
        prices = AB_PRICES.get(file_type, AB_PRICES["printable_pdf"])
        return prices.get(ab_key, prices["A"])

    # ------------------------------------------------------------------
    # Thumbnail check
    # ------------------------------------------------------------------

    async def _check_thumbnails(
        self,
        niche: str,
        file_type: str,
        pdf_path: str | None = None,
        explicit_paths: list[str] | None = None,
    ) -> tuple[bool, list[Path]]:
        """Verifica esistenza thumbnail. Ritorna (ok, paths).

        Priorità di ricerca:
        1. explicit_paths — thumbnail passati esplicitamente da Design Agent
        2. Directory del PDF — cerca thumbnail_*.png nella stessa dir del file prodotto
        3. assets/thumbnails/ — fallback legacy (slug-based)
        """
        def _valid(paths: list[Path]) -> list[Path]:
            return [p for p in paths if p.exists() and p.stat().st_size > 10_000]

        # 1. Path espliciti da Design Agent (Playwright)
        if explicit_paths:
            candidates = [Path(p) for p in explicit_paths]
            valid = _valid(candidates)
            if valid:
                logger.info("Thumbnail da Design Agent: %d validi per '%s'", len(valid), niche)
                return True, valid[:3]
            logger.debug("Thumbnail espliciti forniti ma non validi/esistenti per '%s'", niche)

        # 2. Directory del file PDF (thumbnail_*.png nella stessa cartella)
        if pdf_path:
            pdf_dir = Path(pdf_path).parent
            found_in_dir = list(pdf_dir.glob("thumbnail_*.png"))
            valid = _valid(found_in_dir)
            if valid:
                logger.info("Thumbnail trovati in dir PDF (%s): %d per '%s'", pdf_dir, len(valid), niche)
                return True, valid[:3]
            logger.debug("Nessun thumbnail in dir PDF '%s' per '%s'", pdf_dir, niche)

        # 3. Fallback legacy — assets/thumbnails/ by niche slug
        niche_slug = niche.lower().replace(" ", "_").replace("/", "_")[:30]
        thumbnails_dir = Path("apps/backend/assets/thumbnails")
        found = list(thumbnails_dir.glob(f"{niche_slug}*.png"))
        if not found:
            found = list(thumbnails_dir.glob(f"*{niche_slug[:15]}*.png"))
        valid = _valid(found)
        if valid:
            logger.info("Thumbnail legacy (%s): %d per '%s'", thumbnails_dir, len(valid), niche)
            return True, valid[:3]

        logger.warning(
            "Nessun thumbnail trovato per '%s' (%s). "
            "Cercato in: explicit_paths=%d, pdf_dir=%s, assets/thumbnails/%s*.png",
            niche, file_type,
            len(explicit_paths or []),
            Path(pdf_path).parent if pdf_path else "N/A",
            niche_slug,
        )
        return False, []

    # ------------------------------------------------------------------
    # Failure history
    # ------------------------------------------------------------------

    async def _check_failure_history(self, niche: str, research_data: dict) -> dict:
        """Consulta failure_analysis da Research e analytics DB per niche simili."""
        adjustments: dict[str, Any] = {}

        # 1. Failure analysis da Research
        failure_analysis = research_data.get("failure_analysis_applied", False)
        failure_reasons = research_data.get("failure_reasons", [])

        if failure_analysis and failure_reasons:
            logger.info("Research ha applicato failure constraints per: %s", failure_reasons)
            adjustments["failure_constraints_active"] = failure_reasons

        # 2. ChromaDB — failure analysis recenti per niche simili
        try:
            failures = await self.memory.query_chromadb_recent(
                query=f"niche {niche}",
                n_results=20,
                where={"type": "failure_analysis"},
                primary_days=90,
                fallback_days=180,
            )
            if failures:
                adjustments["chromadb_failures"] = [
                    {
                        "document": f.get("document", ""),
                        "failure_type": f.get("metadata", {}).get("failure_type", ""),
                    }
                    for f in failures[:5]
                ]
        except Exception:
            pass

        # 3. ChromaDB — success pattern recenti per niche simili
        try:
            successes = await self.memory.query_chromadb_recent(
                query=f"niche {niche} success",
                n_results=2,
                where={"type": "success_pattern"},
                primary_days=90,
                fallback_days=180,
            )
            if successes:
                adjustments["chromadb_successes"] = [
                    {
                        "document": s.get("document", ""),
                        "niche": s.get("metadata", {}).get("niche", ""),
                    }
                    for s in successes
                ]
        except Exception:
            pass

        # 4. Analytics DB — niche simili con 0 vendite dopo views
        try:
            failed_listings = await self.memory.get_stale_listings_without_sales(
                min_views=50, days_old=30, limit=20
            )

            niche_words = set(niche.lower().split())
            similar_failures = []
            for listing in failed_listings:
                listing_words = set(listing["niche"].lower().split())
                overlap = len(niche_words & listing_words) / max(len(niche_words), 1)
                if overlap > 0.4:
                    similar_failures.append({
                        "niche": listing["niche"],
                        "price": listing["price_eur"],
                        "views": listing["views"],
                    })

            if similar_failures:
                logger.warning(
                    "Trovate %d niche simili con 0 vendite dopo views: %s",
                    len(similar_failures), similar_failures[:3],
                )
                adjustments["similar_failures"] = similar_failures
                adjustments["warning"] = (
                    "Niche simili non hanno convertito — valutare pricing o SEO diverso"
                )
        except Exception as exc:
            logger.warning("Errore consultazione failure history: %s", exc)

        return adjustments

    # ------------------------------------------------------------------
    # when_made dinamico
    # ------------------------------------------------------------------

    def _get_when_made(self) -> str:
        """Ritorna range 'when_made' valido per Etsy includendo l'anno corrente."""
        current_year = datetime.datetime.now().year
        decade_start = (current_year // 5) * 5
        decade_end = decade_start + 5
        return f"{decade_start}_{min(decade_end, current_year)}"

    # ------------------------------------------------------------------
    # Contesto stagionale
    # ------------------------------------------------------------------

    def _get_seasonal_context(self) -> dict:
        """Ritorna season e keyword rilevanti per il mese corrente."""
        month = datetime.datetime.now().month
        seasonal_map = {
            1: {"season": "New Year", "keywords": ["new year goals", "fresh start", "2026 planner"]},
            2: {"season": "Valentine's", "keywords": ["gift idea", "printable gift", "love"]},
            3: {"season": "Spring", "keywords": ["spring refresh", "organization", "spring cleaning"]},
            4: {"season": "Spring/Easter", "keywords": ["spring", "productivity", "goal setting"]},
            5: {"season": "Mother's Day", "keywords": ["gift for mom", "printable gift", "mothers day"]},
            6: {"season": "Summer", "keywords": ["summer planning", "vacation tracker", "summer goals"]},
            7: {"season": "Midyear Review", "keywords": ["mid year review", "goal check-in", "halfway goals"]},
            8: {"season": "Back to School", "keywords": ["back to school", "student planner", "study tracker"]},
            9: {"season": "Fall/Q4 Prep", "keywords": ["fall planning", "q4 goals", "autumn organizer"]},
            10: {"season": "Halloween/Q4", "keywords": ["october", "halloween", "end of year planning"]},
            11: {"season": "Thanksgiving/Black Friday", "keywords": ["gratitude", "holiday planner", "gift guide"]},
            12: {"season": "Christmas/Year End", "keywords": ["christmas gift", "year in review", "holiday organizer"]},
        }
        return seasonal_map.get(month, {"season": "General", "keywords": []})

    # ------------------------------------------------------------------
    # Notifica Telegram
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass
