"""ImageGenerator — genera Digital Art PNG via Nano Banana Pro (fal.ai) o Replicate Flux Pro."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentpexi.image_gen")

# Dimensioni standard per Etsy digital art (300 DPI, 10x10 pollici)
DEFAULT_WIDTH = 3000
DEFAULT_HEIGHT = 3000

# Modello Replicate (fallback)
FLUX_PRO_MODEL = "black-forest-labs/flux-1.1-pro"

# Modello fal.ai (primario)
NANO_BANANA_MODEL = "fal-ai/nano-banana-pro"

# Stili per product type
_STYLE_MAP = {
    "wall_art": (
        "high quality printable wall art, professional graphic design, "
        "clean composition, suitable for home decor, 300dpi print quality"
    ),
    "quote_print": (
        "elegant typographic print, decorative typography, "
        "printable wall art, clean background, professional design"
    ),
    "nursery_print": (
        "cute nursery wall art, soft colors, children's room decor, "
        "sweet illustration style, printable art, gentle and warm"
    ),
    "botanical_print": (
        "minimalist botanical illustration, line art style, "
        "elegant plant print, printable wall decor, clean white background"
    ),
}

_NEGATIVE_PROMPT = (
    "blurry, low quality, watermark, text overlay, ugly, deformed, "
    "nsfw, violent, dark theme, pixelated, jpeg artifacts"
)


class ImageGenerator:
    """
    Genera Digital Art PNG via Replicate Flux Pro.

    Fallback automatico su placeholder Pillow quando REPLICATE_API_TOKEN
    non è disponibile — stesso pattern dell'Etsy API.
    """

    def __init__(self, api_token: str | None = None) -> None:
        self._token = api_token or os.getenv("REPLICATE_API_TOKEN")
        self._available = bool(self._token)
        if not self._available:
            logger.info(
                "ImageGenerator: REPLICATE_API_TOKEN non trovato — "
                "usando placeholder Pillow. Aggiungere il token al .env per abilitare Flux Pro."
            )

    @property
    def is_available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Entry point principale — chiamato da Design Agent
    # ------------------------------------------------------------------

    async def generate_digital_art(
        self,
        brief: dict,
        output_path: Path,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        mock_mode: bool = False,
    ) -> Path:
        """
        Genera un'immagine Digital Art PNG dal brief Research.

        Args:
            brief: dict con niche, art_type, style, colors, quote (opzionale)
            output_path: percorso output .png
            width/height: dimensioni in pixel (default 3000x3000)
            mock_mode: se True, usa placeholder Pillow senza Replicate

        Returns:
            Path del file generato
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        prompt = self._build_flux_prompt(brief)

        if mock_mode:
            logger.info("ImageGenerator: mock mode — usando placeholder Pillow")
            return await self._generate_placeholder(
                brief=brief,
                prompt=prompt,
                output_path=output_path,
                width=width,
                height=height,
            )

        if self._available:
            return await self._generate_via_replicate(
                prompt=prompt,
                output_path=output_path,
                width=width,
                height=height,
            )
        else:
            return await self._generate_placeholder(
                brief=brief,
                prompt=prompt,
                output_path=output_path,
                width=width,
                height=height,
            )

    # ------------------------------------------------------------------
    # Replicate / Flux Pro
    # ------------------------------------------------------------------

    async def _generate_via_replicate(
        self,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        """Chiama Flux Pro via Replicate, scarica il PNG risultante."""
        import httpx

        try:
            import replicate
        except ImportError:
            logger.error("Pacchetto 'replicate' non installato. Eseguire: pip install replicate")
            raise

        logger.info("Flux Pro: generazione immagine '%s'", output_path.name)

        loop = asyncio.get_event_loop()

        def _run_sync() -> Any:
            client = replicate.Client(api_token=self._token)
            return client.run(
                FLUX_PRO_MODEL,
                input={
                    "prompt": prompt,
                    "negative_prompt": _NEGATIVE_PROMPT,
                    "width": width,
                    "height": height,
                    "num_inference_steps": 28,
                    "guidance_scale": 3.5,
                    "output_format": "png",
                    "output_quality": 100,
                },
            )

        # Replicate client è sync — esegui in thread
        result = await loop.run_in_executor(None, _run_sync)

        # Result è una URL o un FileOutput
        image_url = str(result) if not hasattr(result, "url") else result.url
        if hasattr(result, "__iter__") and not isinstance(result, str):
            # Lista di output (alcuni modelli ritornano lista)
            items = list(result)
            image_url = str(items[0]) if items else None

        if not image_url:
            raise ValueError("Replicate non ha restituito un URL valido")

        # Scarica il file
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            output_path.write_bytes(response.content)

        logger.info(
            "Flux Pro: immagine salvata — %s (%.1f KB)",
            output_path.name,
            output_path.stat().st_size / 1024,
        )
        return output_path

    # ------------------------------------------------------------------
    # Fallback placeholder Pillow
    # ------------------------------------------------------------------

    async def _generate_placeholder(
        self,
        brief: dict,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        """
        Genera un placeholder PNG con Pillow quando Replicate non è disponibile.
        Usa i colori del brief, testo centrato, gradiente sottile.
        Abbastanza realistico da testare il pipeline end-to-end.
        """
        from PIL import Image, ImageDraw, ImageFont

        colors = brief.get("colors", {})
        bg_hex = colors.get("bg", "#F5F5F0")
        text_hex = colors.get("text", "#2C2C2C")
        accent_hex = colors.get("accent", "#8B7355")

        def _hex_to_rgb(h: str) -> tuple[int, int, int]:
            h = h.lstrip("#")
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        bg_rgb = _hex_to_rgb(bg_hex)
        text_rgb = _hex_to_rgb(text_hex)
        accent_rgb = _hex_to_rgb(accent_hex)

        img = Image.new("RGB", (width, height), bg_rgb)
        draw = ImageDraw.Draw(img)

        # Bordo decorativo
        margin = width // 20
        draw.rectangle(
            [margin, margin, width - margin, height - margin],
            outline=accent_rgb,
            width=max(4, width // 300),
        )

        # Testo centrato
        niche = brief.get("niche", "Digital Art")
        art_type = brief.get("art_type", "Print")
        lines = [
            niche.upper(),
            "",
            art_type,
            "",
            "[PLACEHOLDER — Replicate API]",
        ]

        try:
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", width // 15)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", width // 30)
        except Exception:
            font_large = ImageFont.load_default()
            font_small = font_large

        y = height // 3
        for i, line in enumerate(lines):
            if not line:
                y += width // 25
                continue
            font = font_large if i == 0 else font_small
            color = text_rgb if i != 4 else accent_rgb
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            draw.text(((width - text_w) // 2, y), line, fill=color, font=font)
            y += bbox[3] - bbox[1] + width // 40

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: img.save(str(output_path), "PNG", dpi=(300, 300)),
        )

        logger.info("Placeholder generato: %s", output_path.name)
        return output_path

    # ------------------------------------------------------------------
    # Prompt builder per Flux Pro
    # ------------------------------------------------------------------

    def _build_prompt(self, brief: dict) -> str:
        """Alias pubblico per compatibilità con NanaBananaGenerator."""
        return self._build_flux_prompt(brief)

    def _build_flux_prompt(self, brief: dict) -> str:
        """
        Costruisce il prompt Flux Pro dal brief Research Agent.
        Struttura: [soggetto] + [stile] + [colori] + [qualità print]
        """
        niche = brief.get("niche", "")
        art_type = brief.get("art_type", "wall_art")
        style_preset = brief.get("style_preset", "minimal")
        colors = brief.get("colors", {})
        quote = brief.get("quote", "")  # per quote prints

        style_suffix = _STYLE_MAP.get(art_type, _STYLE_MAP["wall_art"])

        # Colori in linguaggio naturale
        color_desc = ""
        if colors.get("bg"):
            color_desc = f"color palette: {colors.get('bg', '')} background, "
            if colors.get("accent"):
                color_desc += f"{colors['accent']} accent tones, "

        # Stile preset
        style_desc = {
            "minimal": "minimalist clean design, lots of white space, ",
            "decorative": "decorative ornate design, intricate details, ",
            "corporate": "professional modern design, geometric elements, ",
            "playful": "fun whimsical design, bright colors, ",
        }.get(style_preset, "")

        # Costruisci prompt
        if art_type == "quote_print" and quote:
            subject = f'typographic print with the quote "{quote}", elegant lettering, '
        else:
            subject = f"{niche} themed printable art, "

        prompt = f"{subject}{style_desc}{color_desc}{style_suffix}"

        # Pulizia
        prompt = " ".join(prompt.split())
        logger.debug("Flux prompt: %s", prompt[:120])
        return prompt


# ---------------------------------------------------------------------------
# NanaBananaGenerator — Nano Banana Pro via fal.ai (primario)
# ---------------------------------------------------------------------------

class NanaBananaGenerator:
    """
    Genera Digital Art PNG via Nano Banana Pro su fal.ai.

    Nano Banana Pro (Gemini 3 Pro Image) è superiore a Flux Pro su:
    - Text rendering (quote prints, tipografia)
    - Clean graphic design e botanical illustration
    - Semantic understanding di prompt complessi

    Costo: ~$0.02/immagine 2K via fal.ai (vs $0.055 Flux Pro su Replicate).
    Fallback automatico su ImageGenerator (Flux) se FAL_KEY non è disponibile.
    """

    def __init__(self, api_key: str | None = None) -> None:
        # Priorità: parametro esplicito → settings (pydantic) → os.getenv
        if not api_key:
            try:
                from apps.backend.core.config import settings as _settings
                api_key = _settings.FAL_KEY or None
            except Exception:
                pass
        self._api_key = api_key or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        self._available = bool(self._api_key)
        if self._available:
            logger.info("NanaBananaGenerator: fal.ai disponibile — Nano Banana Pro attivo")
        else:
            logger.info(
                "NanaBananaGenerator: FAL_KEY non trovata — "
                "aggiungere al .env per abilitare Nano Banana Pro."
            )

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def provider_name(self) -> str:
        return "nano_banana_pro" if self._available else "placeholder"

    async def generate_digital_art(
        self,
        brief: dict,
        output_path: Path,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        mock_mode: bool = False,
    ) -> Path:
        """
        Genera un'immagine Digital Art PNG con Nano Banana Pro.

        Stessa interfaccia di ImageGenerator per drop-in replacement.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        prompt = self._build_prompt(brief)

        if mock_mode:
            logger.info("NanaBananaGenerator: mock mode — placeholder Pillow")
            return await self._generate_placeholder(brief, prompt, output_path, width, height)

        if self._available:
            try:
                return await self._generate_via_fal(prompt, output_path, width, height)
            except Exception as exc:
                logger.error("fal.ai fallito (%s) — fallback placeholder", exc)
                return await self._generate_placeholder(brief, prompt, output_path, width, height)
        else:
            return await self._generate_placeholder(brief, prompt, output_path, width, height)

    async def _generate_via_fal(
        self,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        """Chiama Nano Banana Pro via fal.ai, scarica il PNG risultante."""
        import httpx

        try:
            import fal_client
        except ImportError:
            logger.error("Pacchetto 'fal-client' non installato. Eseguire: pip install fal-client")
            raise

        logger.info("Nano Banana Pro: generazione immagine '%s'", output_path.name)

        loop = asyncio.get_event_loop()

        def _run_sync() -> Any:
            import fal_client as _fal
            client = _fal.SyncClient(key=self._api_key)
            return client.run(
                NANO_BANANA_MODEL,
                arguments={
                    "prompt": prompt,
                    "negative_prompt": _NEGATIVE_PROMPT,
                    "image_size": {"width": width, "height": height},
                    "num_inference_steps": 28,
                    "guidance_scale": 3.5,
                    "num_images": 1,
                    "output_format": "png",
                    "enable_safety_checker": True,
                },
            )

        result = await loop.run_in_executor(None, _run_sync)

        # fal.ai ritorna {"images": [{"url": "...", "width": ..., "height": ...}]}
        images = result.get("images") or []
        if not images:
            raise ValueError("fal.ai Nano Banana Pro: nessuna immagine nel risultato")

        image_url = images[0].get("url")
        if not image_url:
            raise ValueError("fal.ai Nano Banana Pro: URL immagine mancante nel risultato")

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            output_path.write_bytes(response.content)

        logger.info(
            "Nano Banana Pro: immagine salvata — %s (%.1f KB)",
            output_path.name,
            output_path.stat().st_size / 1024,
        )
        return output_path

    async def _generate_placeholder(
        self,
        brief: dict,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        """Placeholder Pillow identico a ImageGenerator per coerenza nei test."""
        # Riusa la logica di ImageGenerator per il placeholder
        _fallback = ImageGenerator()
        return await _fallback._generate_placeholder(brief, prompt, output_path, width, height)

    def _build_prompt(self, brief: dict) -> str:
        """
        Costruisce il prompt per Nano Banana Pro.
        Sfrutta le capacità di text rendering aggiungendo istruzioni
        esplicite per quote prints e tipografia.
        """
        niche = brief.get("niche", "")
        art_type = brief.get("art_type", "wall_art")
        style_preset = brief.get("style_preset", "minimal")
        colors = brief.get("colors", {})
        quote = brief.get("quote", "")

        style_suffix = _STYLE_MAP.get(art_type, _STYLE_MAP["wall_art"])

        color_desc = ""
        if colors.get("bg"):
            color_desc = f"color palette: {colors.get('bg', '')} background, "
            if colors.get("accent"):
                color_desc += f"{colors['accent']} accent tones, "

        style_desc = {
            "minimal": "minimalist clean design, lots of white space, ",
            "decorative": "decorative ornate design, intricate details, ",
            "corporate": "professional modern design, geometric elements, ",
            "playful": "fun whimsical design, bright colors, ",
        }.get(style_preset, "")

        if art_type == "quote_print" and quote:
            # Nano Banana Pro gestisce bene il testo — istruzioni esplicite
            subject = (
                f'typographic wall art print featuring the exact text: "{quote}", '
                f"crystal clear readable typography, elegant font, centered composition, "
            )
        else:
            subject = f"{niche} themed printable wall art, "

        prompt = f"{subject}{style_desc}{color_desc}{style_suffix}"
        prompt = " ".join(prompt.split())
        logger.debug("Nano Banana prompt: %s", prompt[:120])
        return prompt


# ---------------------------------------------------------------------------
# Factory — restituisce il miglior generatore disponibile
# ---------------------------------------------------------------------------

def create_image_generator() -> NanaBananaGenerator | ImageGenerator:
    """
    Factory function: restituisce il generatore migliore disponibile.

    Priorità:
    1. NanaBananaGenerator (fal.ai) — se FAL_KEY è configurata
    2. ImageGenerator (Replicate Flux Pro) — se REPLICATE_API_TOKEN è configurato
    3. ImageGenerator in modalità placeholder — fallback senza API

    Il Design Agent usa sempre questa factory — mai istanziare direttamente.
    """
    # Legge da settings (pydantic-settings carica .env) con fallback a os.getenv
    # I due non si sincronizzano automaticamente — controlliamo entrambi
    try:
        from apps.backend.core.config import settings as _settings
        fal_key = _settings.FAL_KEY or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
    except Exception:
        fal_key = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
    if fal_key:
        gen = NanaBananaGenerator(api_key=fal_key)
        if gen.is_available:
            logger.info("create_image_generator: usando NanaBananaGenerator (fal.ai)")
            return gen

    # Fallback Flux Pro
    gen_flux = ImageGenerator()
    if gen_flux.is_available:
        logger.info("create_image_generator: FAL_KEY assente — usando ImageGenerator (Flux Pro)")
    else:
        logger.info("create_image_generator: nessuna API — placeholder Pillow")
    return gen_flux
