"""Wake word detection via openwakeword.

Riceve chunk audio raw (16-bit PCM, 16kHz, mono) come bytes.
Ritorna True se il wake word viene rilevato nel chunk.
Il modello viene caricato lazy alla prima chiamata.

Placeholder attuale: modello "alexa" di openwakeword.
Sostituire con modello custom "Hey Pepe" dopo fine-tuning
(vedi docs/changes.md §11.3 per istruzioni).
"""
from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger("agentpexi.voice.wake")

_wake_model = None
WAKE_WORD_MODEL = "alexa"   # placeholder — swap con "hey_pepe" dopo fine-tuning


def _get_wake_model():
    global _wake_model
    if _wake_model is None:
        try:
            from openwakeword.model import Model
            _wake_model = Model(
                wakeword_models=[WAKE_WORD_MODEL],
                inference_framework="onnx",
            )
            logger.info("openwakeword model '%s' caricato", WAKE_WORD_MODEL)
        except ImportError:
            logger.warning(
                "openwakeword non installato — wake word detection disabilitata. "
                "Installa con: pip install openwakeword"
            )
            _wake_model = None
    return _wake_model


def detect_wake_word(audio_chunk: bytes, threshold: float = 0.5) -> bool:
    """Controlla se il chunk audio contiene il wake word.

    Args:
        audio_chunk: bytes PCM 16-bit signed, 16kHz, mono (~500ms).
        threshold: soglia di confidenza (0.0–1.0). Default 0.5.
                   Abbassare a 0.35 se ci sono troppi miss, alzare se ci sono
                   falsi positivi.

    Returns:
        True se wake word rilevato nel chunk, False altrimenti.
        Se openwakeword non è installato, ritorna sempre False (degraded mode).
    """
    model = _get_wake_model()
    if model is None:
        return False

    audio_np = np.frombuffer(audio_chunk, dtype=np.int16)
    prediction = model.predict(audio_np)
    score = float(prediction.get(WAKE_WORD_MODEL, 0.0))

    if score > threshold:
        logger.info("Wake word rilevato (score=%.3f)", score)
        return True

    return False
