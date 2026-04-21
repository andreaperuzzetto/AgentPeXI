"""WakeWordDetector — wake word detection con modello ML custom.

Sostituisce Whisper per la fase wake word in /ws/voice.
Usa un GradientBoostingClassifier addestrato su campioni ElevenLabs.

Il modello viene caricato una sola volta (lazy, singleton).
Se il modello non esiste, ritorna None → main.py fa fallback su Whisper.

Flusso:
  WebM bytes (3s dal browser)
    → ffmpeg → PCM 16kHz mono int16
    → extract_features() → vettore 80-dim
    → classifier.predict_proba() → score float
    → score > THRESHOLD → wake word rilevato
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("agentpexi.voice.wake_oww")

# ── Config ─────────────────────────────────────────────────────────────────

MODEL_PATH = Path(__file__).resolve().parent / "wake_model.pkl"
THRESHOLD  = 0.55   # score minimo per rilevare il wake word (0-1)
              # voce reale mic ≠ training sintetico ElevenLabs → soglia più bassa
              # abbassa ulteriormente a 0.50 se ancora troppo restrittivo
SR         = 16000

# ── Feature extraction (identica a train_wake_word.py) ────────────────────

N_FFT   = 512
WIN_LEN = 400
HOP_LEN = 160
N_MELS  = 40


def _mel_filterbank(n_filters: int, n_fft: int, sr: int) -> np.ndarray:
    def hz2mel(hz): return 2595.0 * np.log10(1.0 + hz / 700.0)
    def mel2hz(mel): return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    mel_lo, mel_hi = hz2mel(0), hz2mel(sr / 2)
    mel_pts = np.linspace(mel_lo, mel_hi, n_filters + 2)
    hz_pts  = mel2hz(mel_pts)
    bins    = np.floor((n_fft + 1) * hz_pts / sr).astype(int)

    fb = np.zeros((n_filters, n_fft // 2 + 1))
    for m in range(1, n_filters + 1):
        lo, cen, hi = bins[m - 1], bins[m], bins[m + 1]
        for k in range(lo, cen):
            fb[m - 1, k] = (k - lo) / max(cen - lo, 1)
        for k in range(cen, hi):
            fb[m - 1, k] = (hi - k) / max(hi - cen, 1)
    return fb


_MEL_FB = _mel_filterbank(N_MELS, N_FFT, SR)
_WINDOW = np.hamming(WIN_LEN)


def _extract_features(audio: np.ndarray) -> np.ndarray:
    x = audio.astype(np.float32) / 32768.0
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    if len(x) < WIN_LEN:
        x = np.pad(x, (0, WIN_LEN - len(x)))

    n_frames = max(1, (len(x) - WIN_LEN) // HOP_LEN + 1)
    frames   = np.stack([x[i * HOP_LEN: i * HOP_LEN + WIN_LEN] for i in range(n_frames)])
    frames   = frames * _WINDOW

    mag     = np.abs(np.fft.rfft(frames, n=N_FFT))
    power   = (mag ** 2) / N_FFT
    mel     = np.dot(power, _MEL_FB.T)
    log_mel = np.log(mel + 1e-10)

    return np.concatenate([np.mean(log_mel, axis=0), np.std(log_mel, axis=0)])


# ── Singleton modello ──────────────────────────────────────────────────────

_model = None
_model_loaded = False   # True anche se il file non esiste (evita retry continui)


def _get_model():
    global _model, _model_loaded
    if _model_loaded:
        return _model
    _model_loaded = True
    if not MODEL_PATH.exists():
        logger.warning(
            "wake_oww: modello non trovato (%s) — fallback su Whisper attivo. "
            "Esegui scripts/train_wake_word.py per addestrarlo.",
            MODEL_PATH,
        )
        return None
    try:
        with open(MODEL_PATH, "rb") as f:
            _model = pickle.load(f)
        logger.info("wake_oww: modello caricato da %s", MODEL_PATH)
    except Exception as exc:
        logger.error("wake_oww: errore caricamento modello: %s", exc)
    return _model


# ── PCM decoding ───────────────────────────────────────────────────────────

def _webm_to_pcm_sync(webm_bytes: bytes) -> Optional[np.ndarray]:
    """Decodifica blob WebM in PCM 16kHz mono int16 tramite ffmpeg."""
    import shutil
    # Trova ffmpeg: prova PATH prima, poi paths comuni macOS
    _ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg" or "/usr/local/bin/ffmpeg"

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_in:
        tmp_in.write(webm_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path.replace(".webm", ".wav")
    try:
        result = subprocess.run(
            [
                _ffmpeg, "-y", "-loglevel", "error",
                "-i", tmp_in_path,
                "-ar", str(SR),
                "-ac", "1",
                "-acodec", "pcm_s16le",
                tmp_out_path,
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(
                "wake_oww: ffmpeg errore (rc=%d): %s",
                result.returncode,
                result.stderr.decode()[:300],
            )
            return None

        with wave.open(tmp_out_path, "rb") as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        n = len(raw) // 2
        if n == 0:
            logger.warning("wake_oww: file WAV vuoto dopo ffmpeg")
            return None
        logger.debug("wake_oww: decodificati %d campioni PCM (%.2fs)", n, n / SR)
        return np.array(struct.unpack(f"<{n}h", raw), dtype=np.int16)
    except Exception as exc:
        logger.warning("wake_oww: decodifica fallita: %s", exc)
        return None
    finally:
        for p in (tmp_in_path, tmp_out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ── Interfaccia pubblica ───────────────────────────────────────────────────

def is_available() -> bool:
    """True se il modello è pronto per l'uso."""
    return _get_model() is not None


def predict_sync(webm_bytes: bytes) -> Optional[float]:
    """Elabora blob WebM e restituisce score [0-1] oppure None se indisponibile.

    None = modello non disponibile → main.py usa Whisper come fallback.
    """
    model = _get_model()
    if model is None:
        return None

    audio = _webm_to_pcm_sync(webm_bytes)
    if audio is None or len(audio) == 0:
        return None

    features = _extract_features(audio).reshape(1, -1)
    try:
        score = float(model.predict_proba(features)[0][1])
        logger.debug(
            "wake_oww: score=%.3f threshold=%.2f → %s",
            score,
            THRESHOLD,
            "DETECTED" if score >= THRESHOLD else "no",
        )
        return score
    except Exception as exc:
        logger.warning("wake_oww: predict_proba fallito: %s", exc)
        return None


async def predict(webm_bytes: bytes) -> Optional[float]:
    """Versione async di predict_sync (esegue in thread pool)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, predict_sync, webm_bytes)


def is_wake_word(score: Optional[float], threshold: float = THRESHOLD) -> bool:
    """True se lo score supera la soglia."""
    return score is not None and score >= threshold
