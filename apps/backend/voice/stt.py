"""STT — Speech-to-Text via faster-whisper.

faster-whisper è 4x più veloce di openai-whisper su Apple Silicon, meno RAM.
Modello "base" per buon compromesso velocità/qualità.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

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


def _transcribe_sync(audio_path: str, language: Optional[str], vad_filter: bool = False) -> str:
    """Trascrizione sincrona (eseguita in thread pool).

    Args:
        audio_path: percorso al file audio (webm, wav, mp3…).
        language: codice lingua ISO 639-1 oppure None per auto-detect.
        vad_filter: se True, usa silero-VAD integrato in faster-whisper per
                    ignorare i segmenti di silenzio. Riduce drasticamente le
                    allucinazioni di Whisper su audio vuoto o rumore di fondo
                    (es. "Horses Horses", "Thank you very much" su silenzio).
                    Usare True per wake word detection, False per utterance.
    """
    model = _get_model()
    kwargs: dict = {"language": language, "vad_filter": vad_filter}
    if vad_filter:
        kwargs["vad_parameters"] = {"min_silence_duration_ms": 300}
    segments, info = model.transcribe(audio_path, **kwargs)
    text = " ".join(segment.text.strip() for segment in segments)
    logger.debug(
        "STT: lingua=%s (forced=%s, vad=%s) prob=%.2f testo='%s'",
        info.language,
        language or "auto",
        vad_filter,
        info.language_probability,
        text[:80],
    )
    return text


async def transcribe(
    audio_path: str,
    language: Optional[str] = None,
    vad_filter: bool = False,
) -> str:
    """Trascrive un file audio in testo. Async wrapper attorno a faster-whisper.

    Args:
        audio_path: percorso al file audio.
        language: codice lingua ISO 639-1 o None per auto-detect.
        vad_filter: True per wake word (elimina allucinazioni su silenzio),
                    False per utterance (non troncare l'audio utente).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _transcribe_sync, audio_path, language, vad_filter)
