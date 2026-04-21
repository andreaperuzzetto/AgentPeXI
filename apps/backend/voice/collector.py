"""Raccolta campioni reali per il wake word detector.

Modalità di raccolta: attivata via API, salva i blob WebM ricevuti da /ws/voice
come WAV 16kHz mono 16-bit in training_data/positive/ o training_data/negative/.

I file real_XXXXXX.wav vengono inclusi automaticamente da train_wake_word.py.

Uso tipico:
  1. POST /api/personal/voice/collect  {"mode": "positive"}
  2. Dire "Ei Pepe" ~20 volte (ogni 3s il server salva un campione)
  3. POST /api/personal/voice/collect  {"mode": "negative"}
  4. Fare rumore/parlare ~20 volte di altro
  5. POST /api/personal/voice/collect  {"mode": "off"}
  6. python scripts/train_wake_word.py
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger("agentpexi.voice.collector")

# ── Percorsi ──────────────────────────────────────────────────────────────────

_ROOT       = Path(__file__).resolve().parents[3]
_POS_DIR    = _ROOT / "training_data" / "positive"
_NEG_DIR    = _ROOT / "training_data" / "negative"
_SR         = 16000

# ── Stato raccolta (singleton in-process) ────────────────────────────────────

CollectMode = Literal["positive", "negative", "off"]

_mode: CollectMode = "off"
_counts: dict[str, int] = {"positive": 0, "negative": 0}


def set_mode(mode: CollectMode) -> None:
    global _mode
    _mode = mode
    logger.info("collector: modalità → %s", mode)


def get_status() -> dict:
    return {
        "mode": _mode,
        "counts": dict(_counts),
        "pos_dir": str(_POS_DIR),
        "neg_dir": str(_NEG_DIR),
    }


def is_active() -> bool:
    return _mode in ("positive", "negative")


# ── Conversione e salvataggio ─────────────────────────────────────────────────

def _webm_to_wav(webm_bytes: bytes, out_path: Path) -> bool:
    """Converte blob WebM in WAV 16kHz mono 16-bit tramite ffmpeg."""
    import shutil
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg" or "/usr/local/bin/ffmpeg"

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(webm_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", tmp_path,
                "-ar", str(_SR),
                "-ac", "1",
                "-acodec", "pcm_s16le",
                str(out_path),
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(
                "collector: ffmpeg errore (rc=%d): %s",
                result.returncode,
                result.stderr.decode()[:200],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("collector: conversione fallita: %s", exc)
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def save_sample(webm_bytes: bytes) -> Optional[Path]:
    """Salva il blob WebM come WAV nella cartella corretta.

    Ritorna il path salvato, o None se modalità off / conversione fallita.
    """
    if _mode == "off":
        return None

    out_dir = _POS_DIR if _mode == "positive" else _NEG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time() * 1000) % 10_000_000   # 7 cifre, evita nomi troppo lunghi
    fname = f"real_{ts:07d}.wav"
    out_path = out_dir / fname

    if _webm_to_wav(webm_bytes, out_path):
        _counts[_mode] = _counts.get(_mode, 0) + 1
        logger.info(
            "collector: salvato %s/%s (#%d)",
            _mode,
            fname,
            _counts[_mode],
        )
        return out_path
    return None
