"""PublisherAgent — single-file publish, failure history, and Telegram notification mixin."""
from __future__ import annotations

import csv
import datetime
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp

from apps.backend.agents._publisher.constants import TAXONOMY_IDS
from apps.backend.core.config import settings
from apps.backend.core.etsy_sections_service import EtsySectionsService

logger = logging.getLogger("agentpexi.publisher")


class _PublishMixin:

    def _extra_init_kwargs(self) -> dict:
        return {
            "storage": self.storage,
            "etsy_api": self.etsy_api,
            "telegram_broadcaster": self._telegram_broadcast,
        }

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
        product_tier: str = "core",  # AGT-3: da production_queue.product_tier
    ) -> dict:
        """Pubblica un singolo file come draft su Etsy. Ritorna dict con dettagli."""
        result: dict[str, Any] = {
            "niche": niche,
            "file_type": product_type,
            "template": template,
            "color_scheme": color_scheme,
            "ab_variant": ab_variant,
            "listing_id": None,
            "images_uploaded": 0,
            "seo_validated": False,
            "price_source": "fallback_hardcoded",
        }

        # 3a. File size check — Etsy rifiuta file digitali > 20MB
        ETSY_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB
        file_size = Path(file_path).stat().st_size
        if file_size > ETSY_MAX_FILE_BYTES:
            file_size_mb = file_size / (1024 * 1024)
            logger.error(
                "SKIP: %s non pubblicato — file troppo grande (%.1fMB, max 20MB)",
                niche, file_size_mb,
            )
            result["status"] = "skipped_file_too_large"
            result["error"] = (
                f"File {Path(file_path).name} è {file_size_mb:.1f}MB — "
                "Etsy accetta max 20MB per file digitale. "
                "Ridurre il numero di pagine o la complessità del template."
            )
            return result

        # 3b. Thumbnail check
        if self.etsy_api.mock_mode:
            thumbnails_ok, thumbnail_paths = await self._generate_mock_thumbnail(
                file_path, product_type, niche
            )
        else:
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
            product_tier=product_tier,
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

        # 3e-pre. Section lookup — assegna listing alla sezione Etsy corretta
        section_id = await self._resolve_section_id(niche)

        # 3e. Crea draft su Etsy
        taxonomy_id = TAXONOMY_IDS.get(product_type, 2078)
        if taxonomy_id == 0:
            raise RuntimeError(
                f"TAXONOMY_IDS['{product_type}'] non è ancora configurato. "
                "Usa GET /v3/application/seller-taxonomy/nodes per trovare il taxonomy ID "
                "Etsy corretto e aggiornalo in publisher.py prima di pubblicare."
            )
        create_listing_kwargs: dict = dict(
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
        if section_id:
            create_listing_kwargs["shop_section_id"] = (
                int(section_id) if section_id.isdigit() else section_id
            )

        listing_id, uploaded_count = await self._dispatch_publish(
            create_listing_kwargs,
            file_path,
            thumbnail_paths,
            niche,
            product_type,
        )
        if not listing_id:
            raise RuntimeError("Publish non ha restituito listing_id")
        result["listing_id"] = listing_id
        result["section_id"] = section_id
        # Section count update solo per etsy_api (ha listing reale su Etsy)
        if section_id and os.getenv("PUBLISHER_DELIVERY_METHOD", "csv_export") == "etsy_api":
            try:
                db = await self.memory.get_db()
                ess = EtsySectionsService(db)
                await ess.update_section_listing_count(section_id, listing_id)
            except Exception:
                logger.exception("Failed to update section listing count for section %s", section_id)
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
            logger.exception("Unexpected error")
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
            logger.exception("Unexpected error")
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

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                logger.exception("Unexpected error")

    async def _resolve_section_id(self, niche: str) -> str | None:
        """Lookup/auto-map section_id per la niche. Ritorna section_id o None.

        Flusso:
        1. get_section_for_niche → se trovato, usa
        2. suggest_section_for_niche → se confidence ≥ 0.5, auto-mappa e usa
        3. add_to_uncategorized (con hint suggestion se disponibile) → ritorna None
        """
        try:
            db = await self.memory.get_db()
            ess = EtsySectionsService(db)

            # 1. Lookup esplicito
            section_id = await ess.get_section_for_niche(niche)
            if section_id:
                return section_id

            # 2. Auto-map fuzzy
            suggested_id, confidence = await ess.suggest_section_for_niche(niche)
            if suggested_id and confidence is not None and confidence >= 0.5:
                await ess.map_niche(niche, suggested_id, mapped_by="auto", auto_confidence=confidence)
                logger.info(
                    "Section auto-mapped: %s → %s (confidence %.2f)", niche, suggested_id, confidence
                )
                return suggested_id

            # 3. Uncategorized (con hint se disponibile)
            await ess.add_to_uncategorized(
                niche,
                suggested_section_id=suggested_id,
                suggested_confidence=confidence,
            )
            logger.info("Section not found for %s — added to uncategorized", niche)

        except Exception:
            logger.exception("Section lookup failed for %s — publishing without section", niche)

        return None

    # ------------------------------------------------------------------
    # B-02 · PUBLISHER_DELIVERY_METHOD dispatch layer
    # ------------------------------------------------------------------

    async def _dispatch_publish(
        self,
        create_listing_kwargs: dict,
        file_path: str,
        thumbnail_paths: list,
        niche: str,
        product_type: str,
    ) -> tuple[str, int]:
        """Routes to csv_export | make_webhook | etsy_api. Returns (listing_id, images_uploaded)."""
        method = os.getenv("PUBLISHER_DELIVERY_METHOD", "csv_export")
        if method == "make_webhook":
            listing_id = await self._publish_via_make_webhook(
                create_listing_kwargs, file_path, niche, product_type
            )
            return listing_id, 0
        elif method == "etsy_api":
            return await self._publish_via_etsy_api(
                create_listing_kwargs, file_path, thumbnail_paths
            )
        else:  # csv_export (default sicuro dopo ban Etsy)
            listing_id = await self._publish_via_csv_export(
                create_listing_kwargs, file_path, niche, product_type
            )
            return listing_id, 0

    async def _publish_via_csv_export(
        self,
        create_listing_kwargs: dict,
        file_path: str,
        niche: str,
        product_type: str,
    ) -> str:
        """Scrive una riga CSV in {STORAGE_PATH}/csv_drafts/YYYY-MM-DD.csv."""
        date_str = datetime.date.today().isoformat()
        csv_dir = Path(settings.STORAGE_PATH) / "csv_drafts"
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_path = csv_dir / f"{date_str}.csv"

        listing_id = f"csv_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        row = {
            "listing_id": listing_id,
            "niche": niche,
            "product_type": product_type,
            "file_path": file_path,
            "title": create_listing_kwargs.get("title", ""),
            "description": create_listing_kwargs.get("description", ""),
            "price": create_listing_kwargs.get("price", ""),
            "tags": ",".join(create_listing_kwargs.get("tags", [])),
        }

        file_exists = csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.info("CSV export: %s → %s", niche, csv_path)
        return listing_id

    async def _publish_via_make_webhook(
        self,
        create_listing_kwargs: dict,
        file_path: str,
        niche: str,
        product_type: str,
    ) -> str:
        """POSTa il payload al webhook Make.com e ritorna listing_id con prefisso 'make_'."""
        webhook_url = os.getenv("MAKE_WEBHOOK_URL", "")
        if not webhook_url:
            raise RuntimeError(
                "MAKE_WEBHOOK_URL env var non configurata per make_webhook delivery"
            )

        payload = {
            "niche": niche,
            "product_type": product_type,
            "file_path": file_path,
            **create_listing_kwargs,
            "tags": ",".join(create_listing_kwargs.get("tags", [])),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                resp.raise_for_status()

        listing_id = f"make_{int(time.time())}"
        logger.info("Make.com webhook: %s → %s", niche, listing_id)
        return listing_id

    async def _publish_via_etsy_api(
        self,
        create_listing_kwargs: dict,
        file_path: str,
        thumbnail_paths: list,
    ) -> tuple[str, int]:
        """Crea listing + upload file + upload thumbnail via Etsy API (path originale)."""
        title = create_listing_kwargs.get("title", "")
        price = create_listing_kwargs.get("price", 0)
        tags = create_listing_kwargs.get("tags", [])

        response = await self._call_tool(
            "etsy_api",
            "create_listing",
            {"title": title, "price": price, "tags": tags},
            self.etsy_api.create_listing,
            **create_listing_kwargs,
        )

        listing_id = str(response.get("listing_id", ""))
        if not listing_id:
            raise RuntimeError(f"Etsy non ha restituito listing_id: {response}")

        await self._call_tool(
            "etsy_api",
            "upload_file",
            {"listing_id": listing_id, "file": Path(file_path).name},
            self.etsy_api.upload_file,
            listing_id=listing_id,
            file_path=file_path,
            name=Path(file_path).name,
        )

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

        return listing_id, uploaded_count
