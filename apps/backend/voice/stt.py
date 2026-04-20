"""STT — Speech-to-Text via faster-whisper.

faster-whisper è 4x più veloce di openai-whisper su Apple Silicon, meno RAM.
Modello "base" per buon compromesso velocità/qualità.
"""

from __future__ import annotations

import asyncio
import logging

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.voice.stt")

# Lazy init del modello (pesante, caricato una sola volta)
_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type="int8",
        )
        logger.info(
            "WhisperModel '%s' caricato (%s, int8)",
            settings.WHISPER_MODEL,
            settings.WHISPER_DEVICE,
        )
    return _model


def _transcribe_sync(audio_path: str) -> str:
    """Trascrizione sincrona (eseguita in thread pool)."""
    model = _get_model()
    segments, info = model.transcribe(audio_path, language=settings.WHISPER_LANGUAGE)
    text = " ".join(segment.text.strip() for segment in segments)
    logger.debug("STT: lingua=%s prob=%.2f testo='%s'", info.language, info.language_probability, text[:80])
    return text


async def transcribe(audio_path: str) -> str:
    """Trascrive un file audio in testo. Async wrapper attorno a faster-whisper."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _transcribe_sync, audio_path)
