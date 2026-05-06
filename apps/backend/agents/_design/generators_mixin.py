"""DesignAgent — PDF, digital art, and SVG generation mixin."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools.playwright_export import generate_pdf_thumbnail

from apps.backend.agents._design.colors import _colors_to_scheme
from apps.backend.agents._design.presets import STYLE_PRESETS, _TEMPLATE_TO_GEN, _REGISTERED_FONTS
from apps.backend.agents._design.scoring import _calculate_design_confidence, _validate_pdf
from apps.backend.agents._design.utils import _count_pdf_pages, _get_cover_title, _niche_slug
from apps.backend.core.production_queue import ProductionQueueService as _PQService
from apps.backend.core.shop_identity_service import ShopIdentityService as _SIService

if TYPE_CHECKING:
    from apps.backend.core.shop_identity_service import ShopIdentityRecord

logger = logging.getLogger("agentpexi.design")


# Section-specific style overrides for AGT-4
_SECTION_STYLE_MAP: dict[str, str] = {
    "party_celebrations":  "warm gold tones, festive and elegant, soft bokeh, celebration atmosphere",
    "wellness_self_care":  "sage green and cream palette, natural textures, serene and grounding",
    "planners_organizers": "clean white background, muted neutrals, functional minimalism",
    "kids_learning":       "bright primary colors, playful bold typography, cheerful warm lighting",
}


def _build_5component_prompt(brief: dict, identity: "ShopIdentityRecord") -> str:
    """Build a structured 5-component image prompt for fal.ai / Replicate.

    Components (ordered by weight per AGT-4.1 spec):
      SUBJECT → STYLE → COMPOSITION → TECHNICAL → NEGATIVE PROMPT
    """
    product_type = brief.get("product_type", "printable")
    niche = brief.get("niche", "")
    section_key = brief.get("section_key", "")

    section_style = _SECTION_STYLE_MAP.get(section_key, "")
    palette_str = (
        f"{identity.palette_primary}, {identity.palette_secondary}, {identity.palette_accent}"
    )
    style_base = identity.mockup_style  # "flat_lay" or "lifestyle"

    subject = f"high-quality {product_type} printable mockup for {niche}"
    style = (
        f"{identity.aesthetic_name} brand aesthetic, "
        f"color palette {palette_str}, "
        f"{section_style}, "
        f"{'flat lay photography' if style_base == 'flat_lay' else 'lifestyle context photography'}"
    ).strip(", ")
    composition = (
        "centered product display, generous negative space, "
        "rule of thirds, professional product photography"
    )
    technical = (
        "3000x3000px, 300 DPI, sharp focus, studio lighting, "
        "Etsy listing hero image quality, PNG format"
    )
    negative = (
        "blurry, low quality, watermark, text overlay, ugly, deformed, "
        "nsfw, violent, dark theme, pixelated, jpeg artifacts, "
        "oversaturated, amateur photography"
    )

    return (
        f"SUBJECT: {subject}\n"
        f"STYLE: {style}\n"
        f"COMPOSITION: {composition}\n"
        f"TECHNICAL: {technical}\n"
        f"NEGATIVE PROMPT: {negative}"
    )


def _verify_image_quality(meta: dict) -> bool:
    """Check fal.ai/Replicate metadata for minimum dimensions (≥2000px).

    Fails open (returns True) if width/height are absent — don't block on missing data.
    """
    import logging as _log
    _logger = _log.getLogger("agentpexi.design.quality_gate")
    width = meta.get("width")
    height = meta.get("height")
    if width is None or height is None:
        _logger.warning("_verify_image_quality: no size metadata — failing open")
        return True
    if width < 2000 or height < 2000:
        _logger.warning(
            "_verify_image_quality: image %dx%d is below 2000px minimum", width, height
        )
        return False
    return True


class _DesignGeneratorsMixin:

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        data = task.input_data or {}

        # --- 0. Verifica ShopIdentity attiva (PA-5) ---
        _si_svc = _SIService(await self.memory.get_db())
        _active_identity = await _si_svc.get_active()
        if _active_identity is None:
            msg = (
                "⚠️ DesignAgent sospeso: nessuna ShopIdentity attiva. "
                "Configura un'identità di brand prima di avviare il pipeline."
            )
            await self._notify_telegram(msg)
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "no_active_shop_identity"},
                confidence=0.0,
                missing_data=["shop_identity"],
            )

        # --- 1. Verifica storage ---
        if not self.storage.is_available():
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": f"Storage non disponibile: {self.storage.base_path}"},
                confidence=0.0,
                missing_data=["Storage non disponibile"],
            )

        # --- 2. Validazione input (Intervento 19) ---
        normalized_input, error = await self._validate_and_normalize_input(data)
        if error:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": error},
                confidence=0.0,
                missing_data=[error],
            )

        niche = normalized_input["niche"]
        product_type = normalized_input["product_type"]
        num_variants = normalized_input["num_variants"]
        color_schemes = normalized_input["color_schemes"]
        size = normalized_input.get("size", "A4")

        # --- 3. Estrai research context (Intervento 17) ---
        research_context = self._extract_research_context(normalized_input)

        # --- Route per product_type non-PDF ---
        if product_type == "digital_art_png":
            return await self._run_digital_art(task, normalized_input, research_context)
        if product_type == "svg_bundle":
            return await self._run_svg_bundle(task, normalized_input, research_context)

        # --- 4. Lookup failure patterns da ChromaDB (Intervento 16) ---
        failure_patterns = await self._lookup_failure_patterns(niche, product_type)

        # --- 5. Seleziona template via LLM (Intervento 5) ---
        template = normalized_input.get("template") or await self._select_template_llm(
            niche, product_type, research_context, failure_patterns,
        )

        # --- 6. Seleziona preset 2-stage (Intervento 3) ---
        preset = await self._select_preset(niche, template, research_context, failure_patterns)

        # --- 7. Decide dated/undated (Intervento 7) ---
        include_dates = await self._should_include_dates(template, niche)

        # --- 8. Cover title con keyword primaria (Intervento 6) ---
        cover_title = _get_cover_title(niche, template, research_context)

        await self._log_step(
            "thinking",
            f"Generazione {num_variants} varianti '{template}' preset={preset} per niche '{niche}'",
            input_data={
                "niche": niche, "template": template, "preset": preset,
                "include_dates": include_dates, "cover_title": cover_title,
            },
        )

        # --- 9. Update production_queue → design started ---
        pq_task_id: str | None = normalized_input.get("production_queue_task_id")
        if pq_task_id:
            _pq = _PQService(await self.memory.get_db())
            await _pq.set_design_started(pq_task_id)

        # --- 10. Prepara output directory ---
        output_dir = self.storage.base_path / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- 11. Genera varianti in parallelo con Semaphore(3) ---
        semaphore = asyncio.Semaphore(3)
        generated_variants: list[dict] = []
        validation_results: list[dict] = []
        all_thumbnails: list[dict] = []
        slug = _niche_slug(niche)

        # Mappa template → generator di file_gen.py
        gen_template = _TEMPLATE_TO_GEN.get(template, "weekly_planner")

        async def generate_single_variant(idx: int, color_scheme: str) -> dict | None:
            async with semaphore:
                # Colori niche-aware (Intervento 4)
                colors = await self._resolve_color_scheme_niche_aware(
                    color_scheme, niche, preset,
                )

                variant_dir = output_dir / f"variant_{idx}"
                variant_dir.mkdir(exist_ok=True)
                pdf_path = variant_dir / f"{slug}_{template}_{idx}.pdf"

                try:
                    # Bridge: converti hex colors → ColorScheme per PDFGenerator
                    scheme = _colors_to_scheme(f"{preset}_{idx}", colors)

                    # Genera PDF via file_gen.py
                    preset_data = STYLE_PRESETS[preset]
                    font_heading_name = preset_data["font_heading"]
                    font_body_name = preset_data["font_primary"]

                    # Usa font custom se disponibili, altrimenti fallback
                    font_heading = (
                        f"{font_heading_name}-Bold"
                        if _REGISTERED_FONTS.get(font_heading_name)
                        else "Helvetica-Bold"
                    )
                    font_body = (
                        font_heading_name
                        if _REGISTERED_FONTS.get(font_heading_name)
                        else "Helvetica"
                    )
                    font_light = (
                        font_body_name
                        if _REGISTERED_FONTS.get(font_body_name)
                        else "Helvetica-Oblique"
                    )

                    pdf_metadata = {
                        "title": cover_title,
                        "subject": f"Printable {template.replace('_', ' ').title()} - {niche}",
                        "keywords": f"{niche}, printable, {template.replace('_', ' ')}, digital download, Etsy",
                    }

                    await self._call_tool(
                        "pdf_generator",
                        f"generate_{gen_template}",
                        {"scheme": scheme.name, "size": size, "output": str(pdf_path)},
                        self._pdf_gen.generate,
                        gen_template,
                        scheme,
                        size,
                        pdf_path,
                        font_heading=font_heading,
                        font_body=font_body,
                        font_light=font_light,
                        cover_title=cover_title,
                        add_instructions=True,
                        metadata=pdf_metadata,
                    )

                    # Conta pagine dopo generazione
                    pages_count = _count_pdf_pages(pdf_path)

                    # Validazione PDF (Intervento 14)
                    validation = await _validate_pdf(pdf_path, template, expected_pages=pages_count)
                    validation_results.append(validation)

                    if not validation["valid"]:
                        logger.warning("PDF validation failed: %s — %s", pdf_path, validation["issues"])

                    # Genera thumbnails Playwright (Intervento 12)
                    preset_data = STYLE_PRESETS[preset]
                    thumbnails = await self._call_tool(
                        "playwright",
                        "generate_thumbnails",
                        {"pdf": str(pdf_path), "preset": preset},
                        generate_pdf_thumbnail,
                        pdf_path=pdf_path,
                        output_dir=variant_dir,
                        preset=preset,
                        preset_data=preset_data,
                        niche=niche,
                        colors=colors,
                    )
                    all_thumbnails.append(thumbnails)

                    return {
                        "pdf_path": str(pdf_path),
                        "variant_index": idx,
                        "color_scheme": color_scheme,
                        "preset": preset,
                        "template": template,
                        "colors": colors,
                        "include_dates": include_dates,
                        "thumbnails": {
                            k: str(v) for k, v in thumbnails.items()
                            if v and k != "errors"
                        },
                        "validation": validation,
                        "pages": pages_count,
                    }

                except Exception as e:
                    logger.warning("Errore generazione variante %d: %s", idx, e)
                    await self._log_step(
                        "tool_call",
                        f"variant_{idx} error: {e}",
                        input_data={"variant": idx, "color_scheme": color_scheme},
                        output_data={"error": str(e)},
                    )
                    return None

        tasks_coroutines = [
            generate_single_variant(i, color_schemes[i % len(color_schemes)])
            for i in range(num_variants)
        ]
        results = await asyncio.gather(*tasks_coroutines, return_exceptions=True)

        for r in results:
            if isinstance(r, dict):
                generated_variants.append(r)

        if not generated_variants:
            if pq_task_id:
                _pq = _PQService(await self.memory.get_db())
                await _pq.set_failed_by_task_id(pq_task_id, "All variants failed to generate")
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "All variants failed to generate"},
                confidence=0.0,
                missing_data=["No variants generated successfully"],
            )

        # --- 12. Confidence scoring (Intervento 15) ---
        confidence, missing_data = _calculate_design_confidence(
            variants_generated=len(generated_variants),
            variants_requested=num_variants,
            thumbnails=all_thumbnails,
            validation_results=validation_results,
            fonts_available=_REGISTERED_FONTS,
            research_available=research_context is not None,
        )

        # --- 13. Update production_queue — store generated files ---
        if pq_task_id:
            file_paths = [v["pdf_path"] for v in generated_variants]
            _pq = _PQService(await self.memory.get_db())
            await _pq.set_files_generated(pq_task_id, file_paths)

        # --- 14. Log step finale ---
        total_size = sum(
            Path(v["pdf_path"]).stat().st_size
            for v in generated_variants
            if Path(v["pdf_path"]).exists()
        )
        summary = (
            f"Generati {len(generated_variants)}/{num_variants} varianti PDF "
            f"({total_size / 1024:.0f} KB), preset={preset}, confidence={confidence}"
        )
        await self._log_step(
            "file_operation",
            summary,
            input_data={"template": template, "size": size, "preset": preset},
            output_data={
                "variants_generated": len(generated_variants),
                "confidence": confidence,
            },
        )

        _n_var = len(generated_variants)
        _var_label = "variante generata" if _n_var == 1 else "varianti generate"
        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "variants": generated_variants,
                "preset": preset,
                "template": template,
                "include_dates": include_dates,
                "cover_title": cover_title,
                "niche": niche,
                "product_type": product_type,
                "failure_patterns_checked": failure_patterns is not None,
            },
            confidence=confidence,
            missing_data=missing_data,
            reply_voice=f"Design completato. {_n_var} {_var_label}.",
        )

    # ------------------------------------------------------------------
    # Digital Art PNG pipeline
    # ------------------------------------------------------------------

    async def _run_digital_art(
        self,
        task: AgentTask,
        normalized_input: dict,
        research_context: dict | None,
    ) -> AgentResult:
        """Genera Digital Art PNG via ImageGenerator (Flux Pro / placeholder)."""
        niche = normalized_input["niche"]
        num_variants = normalized_input["num_variants"]
        pq_task_id: str | None = normalized_input.get("production_queue_task_id")

        if pq_task_id:
            _pq = _PQService(await self.memory.get_db())
            await _pq.set_design_started(pq_task_id)

        output_dir = self.storage.base_path / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        slug = _niche_slug(niche)
        art_type = normalized_input.get("art_type", "wall_art")
        style_preset = normalized_input.get("style_preset", "minimal")

        # --- AGT-4: Try to get active identity for 5-component prompts ---
        identity = None
        try:
            db = getattr(self._memory, "_db", None)  # type: ignore[attr-defined]
            if db is not None:
                from apps.backend.core.shop_identity_service import ShopIdentityService
                svc = ShopIdentityService(db)
                identity = await svc.get_active()
        except Exception:
            pass  # Fall back to standard brief if identity unavailable

        provider = getattr(self._image_gen, "provider_name", "flux" if self._image_gen.is_available else "placeholder")
        await self._log_step(
            "thinking",
            f"Generazione {num_variants} Digital Art PNG per niche '{niche}' "
            f"(art_type={art_type}, api={provider}, agt4={'enabled' if identity else 'disabled'})",
        )

        generated: list[dict] = []
        color_schemes = normalized_input.get("color_schemes", ["neutral", "warm"])

        for i in range(num_variants):
            brief = {
                "niche": niche,
                "art_type": art_type,
                "style_preset": style_preset,
                "colors": normalized_input.get("colors", {}),
                "quote": normalized_input.get("quote", ""),
                "product_type": art_type,  # For AGT-4 prompt building
                "section_key": normalized_input.get("section_key", ""),
            }

            # --- AGT-4: Build 5-component prompts when identity is active ---
            prompt_a = None
            prompt_b = None
            if identity is not None:
                from dataclasses import replace as dc_replace
                try:
                    prompt_a = _build_5component_prompt(brief, identity)
                    identity_b = dc_replace(
                        identity,
                        mockup_style=("lifestyle" if identity.mockup_style == "flat_lay" else "flat_lay")
                    )
                    prompt_b = _build_5component_prompt(brief, identity_b)
                    brief["agt4_prompt_a"] = prompt_a
                    brief["agt4_prompt_b"] = prompt_b
                    logger.info("AGT-4 prompts generated for variant %d", i)
                except Exception as e:
                    logger.warning("AGT-4 prompt generation failed: %s", e)

            out_path = output_dir / f"{slug}_art_{i + 1}.png"
            try:
                path = await self._image_gen.generate_digital_art(brief, out_path, mock_mode=self._get_mock_mode())
                
                # --- AGT-4: Quality gate check ---
                meta = {}
                if path.exists():
                    try:
                        from PIL import Image
                        with Image.open(path) as img:
                            meta = {"width": img.width, "height": img.height}
                            _verify_image_quality(meta)  # Logs warning if quality is low
                    except Exception:
                        pass  # Fail open — don't block on quality check errors
                
                variant_data = {
                    "file_path": str(path),
                    "variant_index": i,
                    "art_type": art_type,
                    "file_size_kb": round(path.stat().st_size / 1024, 1),
                    "image_provider": getattr(self._image_gen, "provider_name", "unknown"),
                }
                if prompt_a:
                    variant_data["agt4_enabled"] = True
                    variant_data["image_path_a"] = str(path)  # Primary variant
                    # Note: prompt_b would be used for generating a second variant in future
                generated.append(variant_data)
            except Exception as e:
                logger.warning("Errore Digital Art variante %d: %s", i, e)

        if not generated:
            if pq_task_id:
                _pq = _PQService(await self.memory.get_db())
                await _pq.set_failed_by_task_id(pq_task_id, "All digital art variants failed")
            return AgentResult(
                task_id=task.task_id, agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "All digital art variants failed"},
                confidence=0.0,
                missing_data=["No digital art generated"],
            )

        confidence = len(generated) / num_variants
        provider = getattr(self._image_gen, "provider_name", "unknown")
        if provider == "placeholder":
            confidence *= 0.6  # placeholder = fiducia ridotta

        if pq_task_id:
            file_paths = [v["file_path"] for v in generated]
            _pq = _PQService(await self.memory.get_db())
            await _pq.set_files_generated(pq_task_id, file_paths)

        await self._log_step(
            "file_operation",
            f"Digital Art: {len(generated)}/{num_variants} PNG generati, confidence={confidence:.2f}",
        )

        return AgentResult(
            task_id=task.task_id, agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "variants": generated,
                "niche": niche,
                "product_type": "digital_art_png",
                "art_type": art_type,
                "image_provider": getattr(self._image_gen, "provider_name", "unknown"),
            },
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # SVG Bundle pipeline
    # ------------------------------------------------------------------

    async def _run_svg_bundle(
        self,
        task: AgentTask,
        normalized_input: dict,
        research_context: dict | None,
    ) -> AgentResult:
        """Genera SVG bundle via SVGGenerator."""
        niche = normalized_input["niche"]
        pq_task_id: str | None = normalized_input.get("production_queue_task_id")

        if pq_task_id:
            _pq = _PQService(await self.memory.get_db())
            await _pq.set_design_started(pq_task_id)

        output_dir = self.storage.base_path / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        svg_type = normalized_input.get("svg_type", "geometric")
        brief = {
            "niche": niche,
            "svg_type": svg_type,
            "complexity": normalized_input.get("complexity", 2),
            "quote": normalized_input.get("quote", ""),
            "color_variants": normalized_input.get("color_variants", []),
        }

        await self._log_step(
            "thinking",
            f"Generazione SVG bundle '{svg_type}' per niche '{niche}'",
        )

        try:
            paths = await self._svg_gen.generate_bundle(brief, output_dir)
        except Exception as e:
            logger.error("SVG bundle generation failed: %s", e)
            if pq_task_id:
                _pq = _PQService(await self.memory.get_db())
                await _pq.set_failed_by_task_id(pq_task_id, f"SVG generation failed: {e}")
            return AgentResult(
                task_id=task.task_id, agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": f"SVG generation failed: {e}"},
                confidence=0.0,
                missing_data=["SVG generation error"],
            )

        file_paths_str = [str(p) for p in paths]

        if pq_task_id:
            _pq = _PQService(await self.memory.get_db())
            await _pq.set_files_generated(pq_task_id, file_paths_str)

        await self._log_step(
            "file_operation",
            f"SVG bundle: {len(paths)} file generati (type={svg_type})",
        )

        return AgentResult(
            task_id=task.task_id, agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "svg_files": file_paths_str,
                "niche": niche,
                "product_type": "svg_bundle",
                "svg_type": svg_type,
                "num_files": len(paths),
            },
            confidence=1.0,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass
