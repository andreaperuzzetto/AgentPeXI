"""ImageGenerator — genera immagini via Replicate API (Flux Pro). [STUB — API key non disponibile]"""

from __future__ import annotations

from pathlib import Path


class ImageGenerator:
    """Genera immagini via Replicate API (Flux Pro). [STUB — API key non disponibile]"""

    async def generate(self, prompt: str, output_path: Path) -> Path:
        raise NotImplementedError(
            "Replicate API non configurata. Aggiungere REPLICATE_API_TOKEN al .env"
        )
