"""TTS — Text-to-Speech via ElevenLabs API."""

from __future__ import annotations

import asyncio
import logging

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.voice.tts")


_TTS_MAX_CHARS_FALLBACK = 5000


async def synthesize(text: str) -> bytes:
    """Sintetizza testo in audio (bytes MP3) via ElevenLabs."""
    max_chars = settings.ELEVENLABS_MAX_CHARS or _TTS_MAX_CHARS_FALLBACK
    if len(text) > max_chars:
        logger.warning("TTS: testo troncato da %d a %d caratteri", len(text), max_chars)
        text = text[:max_chars]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _synthesize_sync, text)


def _synthesize_sync(text: str) -> bytes:
    """Sintesi sincrona (eseguita in thread pool)."""
    from elevenlabs import ElevenLabs

    client = ElevenLabs(api_key=settings.ELEVENLABS_API_KEY)

    voice_id = settings.ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"  # Rachel default

    response = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
    )

    # response è un generatore di chunks bytes
    audio_bytes = b"".join(response)
    logger.debug("TTS: %d bytes generati per %d caratteri", len(audio_bytes), len(text))
    return audio_bytes
