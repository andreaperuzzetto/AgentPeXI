"""PublisherAgent — thumbnail generation and validation mixin."""
from __future__ import annotations

import logging
from pathlib import Path

import aiofiles

logger = logging.getLogger("agentpexi.publisher")


class _ThumbnailMixin:

    async def _generate_mock_thumbnail(
        self,
        file_path: str,
        product_type: str,
        niche: str,
    ) -> tuple[bool, list[Path]]:
        """Genera thumbnail sintetico per mock mode — bypassa Playwright.

        - digital_art_png: il file PNG IS il thumbnail (nessuna generazione)
        - printable_pdf: crea un placeholder 500×667 con Pillow (>10KB).
          Se Pillow non è disponibile, scrive raw bytes PNG validi >10KB.
        """
        MIN_SIZE = 10_001  # _valid() richiede > 10_000

        if product_type == "digital_art_png":
            png_path = Path(file_path)
            if png_path.exists() and png_path.stat().st_size > 10_000:
                logger.info("Mock thumbnail: usando PNG diretto per '%s'", niche)
                return True, [png_path]
            # File non esiste ancora (mock totale) — crea placeholder
            thumb_path = png_path.parent / f"thumbnail_mock_{png_path.stem}.png"
        else:
            file_stem = Path(file_path).stem if file_path else "mock"
            output_dir = Path(file_path).parent if file_path else Path("/tmp")
            thumb_path = output_dir / f"thumbnail_mock_{file_stem}.png"

        # Crea placeholder con Pillow se disponibile
        try:
            from PIL import Image, ImageDraw, ImageFont  # type: ignore

            img = Image.new("RGB", (500, 667), color=(240, 235, 230))
            draw = ImageDraw.Draw(img)
            # Rettangolo decorativo
            draw.rectangle([20, 20, 480, 647], outline=(180, 160, 140), width=3)
            # Testo niche (troncato se lungo)
            label = niche[:40] if len(niche) > 40 else niche
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            except Exception:
                font = ImageFont.load_default()
            draw.text((40, 300), label, fill=(80, 60, 50), font=font)
            draw.text((40, 340), "[MOCK THUMBNAIL]", fill=(160, 140, 120), font=font)
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(thumb_path), "PNG", optimize=False)
            if thumb_path.stat().st_size < MIN_SIZE:
                # Aggiungi padding se il PNG è troppo piccolo
                async with aiofiles.open(thumb_path, "ab") as f:
                    await f.write(b"\x00" * (MIN_SIZE - thumb_path.stat().st_size + 1))
            logger.info("Mock thumbnail Pillow creato: %s (%d B)", thumb_path, thumb_path.stat().st_size)
            return True, [thumb_path]
        except Exception as pillow_err:
            logger.debug("Pillow non disponibile (%s) — fallback raw PNG bytes", pillow_err)

        # Fallback: raw PNG minimalista + padding fino a >10KB
        # PNG signature + IHDR (500×667, RGB) + IDAT (1 pixel nero) + IEND
        import zlib
        import struct

        def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        width, height = 500, 667
        ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        # Scanline minima: filter byte 0x00 + RGB pixel ripetuto
        scanline = b"\x00" + b"\xf0\xeb\xe6" * width
        raw_data = scanline * height
        idat_data = zlib.compress(raw_data, 1)

        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr_data)
            + _png_chunk(b"IDAT", idat_data)
            + _png_chunk(b"IEND", b"")
        )
        if len(png_bytes) < MIN_SIZE:
            # Aggiungi tEXt chunk con padding
            padding = b"Comment\x00" + b"x" * (MIN_SIZE - len(png_bytes) + 100)
            png_bytes = (
                b"\x89PNG\r\n\x1a\n"
                + _png_chunk(b"IHDR", ihdr_data)
                + _png_chunk(b"IDAT", idat_data)
                + _png_chunk(b"tEXt", padding)
                + _png_chunk(b"IEND", b"")
            )

        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(png_bytes)
        logger.info("Mock thumbnail raw PNG creato: %s (%d B)", thumb_path, len(png_bytes))
        return True, [thumb_path]

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
