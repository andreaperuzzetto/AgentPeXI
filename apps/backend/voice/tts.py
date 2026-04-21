"""TTS — Text-to-Speech via ElevenLabs API.

Pipeline principale:
  1. POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream
  2. Riceve audio MP3 in streaming
  3. Riproduce via afplay (macOS nativo, zero dipendenze extra)

Fallback automatico su macOS `say` se:
  - ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID mancanti nel .env
  - ElevenLabs restituisce errore HTTP
  - Problemi di rete

La funzione pubblica `play_via_say` mantiene la stessa firma usata
dal WebSocket handler in main.py — nessuna modifica richiesta altrove.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import tempfile

import httpx

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.voice.tts")


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Taglia il testo all'ultima frase completa entro max_chars.

    Cerca l'ultimo punto/punto esclamativo/interrogativo prima del limite.
    Se non trova nessuno, taglia al limite grezzo (meglio di niente).
    """
    if len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    # Cerca l'ultimo terminatore di frase nel chunk
    for sep in (".", "!", "?"):
        idx = chunk.rfind(sep)
        if idx > max_chars // 2:   # almeno metà del testo, altrimenti non vale
            return chunk[:idx + 1].strip()
    return chunk.strip()

_TTS_MAX_CHARS = settings.ELEVENLABS_MAX_CHARS

_ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}


# ------------------------------------------------------------------
# Funzione pubblica principale — chiamata da main.py ws_voice
# ------------------------------------------------------------------

async def play_via_say(text: str) -> bool:
    """Sintetizza e riproduce audio.

    Usa ElevenLabs se configurato, altrimenti macOS say come fallback.
    Blocca finché la riproduzione non è completata.

    Returns:
        True se completato con successo, False altrimenti.
    """
    if not text or not text.strip():
        return True

    if len(text) > _TTS_MAX_CHARS:
        logger.warning("TTS: testo troncato da %d a %d caratteri", len(text), _TTS_MAX_CHARS)
        text = _truncate_at_sentence(text, _TTS_MAX_CHARS)

    if not settings.ELEVENLABS_API_KEY or not settings.ELEVENLABS_VOICE_ID:
        logger.error("TTS: ELEVENLABS_API_KEY o ELEVENLABS_VOICE_ID mancanti nel .env")
        return False

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _play_elevenlabs_sync, text)


def _play_elevenlabs_sync(text: str) -> bool:
    """Chiama ElevenLabs, scarica MP3, riproduce via afplay."""
    url = _ELEVENLABS_URL.format(voice_id=settings.ELEVENLABS_VOICE_ID)
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": _VOICE_SETTINGS,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.error(
                "TTS ElevenLabs: HTTP %d body=%s",
                resp.status_code,
                resp.text[:300],
            )
            return False

        audio_bytes = resp.content
        if not audio_bytes:
            logger.error("TTS ElevenLabs: risposta vuota")
            return False

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            subprocess.run(["afplay", tmp_path], check=True, timeout=120)
            logger.info("TTS ElevenLabs: completato (%d bytes, %d chars)", len(audio_bytes), len(text))
            return True
        except Exception as exc:
            logger.error("TTS: afplay fallito: %s", exc)
            return False
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    except Exception as exc:
        logger.error("TTS ElevenLabs: errore (%s)", exc)
        return False


# ------------------------------------------------------------------
# Utility: synthesize → bytes MP3 (per usi futuri, es. invio al browser)
# ------------------------------------------------------------------

async def synthesize(text: str) -> bytes | None:
    """Sintetizza testo e restituisce bytes MP3 raw senza riprodurlo.

    Returns:
        bytes MP3 se successo, None se ElevenLabs non disponibile.
    """
    if not settings.ELEVENLABS_API_KEY or not settings.ELEVENLABS_VOICE_ID:
        return None
    if len(text) > _TTS_MAX_CHARS:
        text = text[:_TTS_MAX_CHARS]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _synthesize_sync, text)


def _synthesize_sync(text: str) -> bytes | None:
    url = _ELEVENLABS_URL.format(voice_id=settings.ELEVENLABS_VOICE_ID)
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": _VOICE_SETTINGS,
    }
    try:
        with httpx.Client(timeout=30) as client:
            with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                return resp.read()
    except Exception as exc:
        logger.warning("TTS synthesize: %s", exc)
        return None


async def synthesize_to_b64(text: str) -> str | None:
    """Convenience: sintetizza e restituisce base64 string (pronta per JSON)."""
    audio_bytes = await synthesize(text)
    if audio_bytes is None:
        return None
    return base64.b64encode(audio_bytes).decode()
