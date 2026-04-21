"""Wake word detection — approccio Whisper-based keyword spotting.

Strategia (senza fine-tuning, senza openwakeword custom):
  1. Il handler /ws/voice accumula chunk audio dal browser (500ms ciascuno).
  2. Ogni ~3 secondi (WAKE_CHUNKS_TARGET chunk) trascrive con faster-whisper.
  3. Se la trascrizione contiene la keyword "jarvis" → wake word rilevato.

WAKE_KEYWORD può essere cambiato in qualsiasi parola — nessun modello da addestrare.

La funzione detect_wake_word_in_text() è il punto di ingresso usato da main.py.
La funzione detect_wake_word() (openwakeword legacy) è mantenuta per compatibilità
ma non viene più chiamata dal pipeline principale.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("agentpexi.voice.wake")

# ── Configurazione keyword ──────────────────────────────────────────────────
WAKE_KEYWORD = "hey pepe"         # parola trigger canonica
WAKE_THRESHOLD_DEFAULT = 0.5      # mantenuto per compatibilità API legacy

# Varianti fonetiche di "Hey Pepe" — Whisper trascrive "hey" in modo molto
# stabile anche su audio italiano. Le varianti coprono i casi edge.
WAKE_PHONETIC_VARIANTS: frozenset[str] = frozenset({
    "hey pepe",   # standard
    "ehi pepe",   # versione italiana di "hey"
    "ei pepe",    # trascrizione rapida
    "hei pepe",   # variante grafica
    "ay pepe",    # accento anglosassone
    "ej pepe",    # variante fonetica spagnola/italiana
    "hey pepé",   # con accento
})


def detect_wake_word_in_text(text: str, keyword: str = WAKE_KEYWORD) -> bool:
    """Controlla se la trascrizione contiene il wake word o una sua variante fonetica.

    Args:
        text: testo trascritto da Whisper.
        keyword: parola trigger canonica (default: WAKE_KEYWORD).

    Returns:
        True se keyword o variante fonetica trovata (case-insensitive).
    """
    # Normalizza punteggiatura: "Ei, Pepe." → "ei pepe"
    # Whisper aggiunge spesso virgole e punti che spezzano il match
    text_lower = re.sub(r"[^\w\s]", " ", text.lower())
    text_lower = re.sub(r"\s+", " ", text_lower).strip()

    # 1. Match esatto keyword canonica
    if keyword.lower() in text_lower:
        logger.info("Wake word '%s' rilevato: '%s'", keyword, text[:80])
        return True

    # 2. Match varianti fonetiche (Whisper base traslittera in modo imprevedibile)
    for variant in WAKE_PHONETIC_VARIANTS:
        if variant in text_lower:
            logger.info("Wake word (variante '%s') rilevato: '%s'", variant, text[:80])
            return True

    return False


# ── Legacy openwakeword (non più usato nel pipeline principale) ─────────────

_wake_model = None
WAKE_WORD_MODEL = "jarvis"   # placeholder — openwakeword non ha un modello "jarvis"
                              # il rilevamento reale avviene via Whisper in main.py


def _get_wake_model():
    """Carica il modello openwakeword (legacy). Fallisce gracefully se non disponibile."""
    global _wake_model
    if _wake_model is None:
        try:
            from openwakeword.model import Model
            _wake_model = Model(
                wakeword_models=[WAKE_WORD_MODEL],
                inference_framework="onnx",
            )
            logger.info("openwakeword model '%s' caricato", WAKE_WORD_MODEL)
        except Exception as exc:
            logger.debug(
                "openwakeword non disponibile (%s) — wake word detection via Whisper attiva",
                exc,
            )
            _wake_model = None
    return _wake_model


def detect_wake_word(audio_chunk: bytes, threshold: float = WAKE_THRESHOLD_DEFAULT) -> bool:
    """[Legacy] Controlla chunk audio via openwakeword.

    Non più chiamato dal pipeline principale (usa detect_wake_word_in_text invece).
    Mantenuto per compatibilità con eventuali test o usi esterni.
    Ritorna sempre False perché openwakeword non ha un modello "jarvis".
    """
    import numpy as np
    model = _get_wake_model()
    if model is None:
        return False

    audio_np = np.frombuffer(audio_chunk, dtype=np.int16)
    prediction = model.predict(audio_np)
    score = float(prediction.get(WAKE_WORD_MODEL, 0.0))

    if score > threshold:
        logger.info("openwakeword: wake word rilevato (score=%.3f)", score)
        return True

    return False
