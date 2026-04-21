"""Addestra il wake word detector per "Ei Pepe".

Legge i campioni WAV da training_data/{positive,negative}/,
estrae feature mel-spectrogram con numpy puro,
addestra un RandomForestClassifier e salva il modello in:
  apps/backend/voice/wake_model.pkl

Uso:
  cd /Volumes/Progetti/AgentPeXI
  python scripts/train_wake_word.py

Dipendenze: numpy, scikit-learn (entrambi già nel requirements)
"""

from __future__ import annotations

import os
import pickle
import struct
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
POS_DIR  = ROOT / "training_data" / "positive"
NEG_DIR  = ROOT / "training_data" / "negative"
OUT_PATH = ROOT / "apps" / "backend" / "voice" / "wake_model.pkl"

# ── Feature extraction ─────────────────────────────────────────────────────

SR        = 16000
N_FFT     = 512
WIN_LEN   = 400   # 25 ms
HOP_LEN   = 160   # 10 ms
N_MELS    = 40


def _mel_filterbank(n_filters: int, n_fft: int, sr: int) -> np.ndarray:
    """Restituisce matrice (n_filters, n_fft//2+1) con il banco di filtri mel."""
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


def extract_features(audio: np.ndarray) -> np.ndarray:
    """Estrae 80 feature (mean+std log-mel) da un array PCM int16."""
    # Normalizza in float32 [-1, 1]
    x = audio.astype(np.float32) / 32768.0

    # Pre-emphasis
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])

    # Padding minimo se il clip è troppo corto
    if len(x) < WIN_LEN:
        x = np.pad(x, (0, WIN_LEN - len(x)))

    # Framing
    n_frames = max(1, (len(x) - WIN_LEN) // HOP_LEN + 1)
    frames = np.stack([x[i * HOP_LEN: i * HOP_LEN + WIN_LEN] for i in range(n_frames)])
    frames = frames * _WINDOW

    # Power spectrum
    mag   = np.abs(np.fft.rfft(frames, n=N_FFT))
    power = (mag ** 2) / N_FFT

    # Log-mel spectrogram
    mel   = np.dot(power, _MEL_FB.T)
    log_mel = np.log(mel + 1e-10)

    # Statistiche temporali → vettore fisso 80-dim
    return np.concatenate([np.mean(log_mel, axis=0), np.std(log_mel, axis=0)])


def load_wav(path: Path) -> np.ndarray | None:
    """Carica WAV 16kHz mono 16-bit come array int16."""
    try:
        with wave.open(str(path), "rb") as wf:
            assert wf.getnchannels() == 1,   f"{path.name}: non mono"
            assert wf.getsampwidth() == 2,   f"{path.name}: non 16-bit"
            raw = wf.readframes(wf.getnframes())
        n = len(raw) // 2
        return np.array(struct.unpack(f"<{n}h", raw), dtype=np.int16)
    except Exception as exc:
        print(f"  ⚠ skip {path.name}: {exc}")
        return None


# ── Training ──────────────────────────────────────────────────────────────

def load_dataset():
    X, y = [], []

    for path in sorted(POS_DIR.glob("*.wav")):
        if path.name.startswith("._"):
            continue  # resource fork macOS — ignora
        audio = load_wav(path)
        if audio is not None:
            X.append(extract_features(audio))
            y.append(1)

    for path in sorted(NEG_DIR.glob("*.wav")):
        if path.name.startswith("._"):
            continue  # resource fork macOS — ignora
        audio = load_wav(path)
        if audio is not None:
            X.append(extract_features(audio))
            y.append(0)

    return np.array(X), np.array(y)


def train(X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    clf = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            random_state=42,
        )),
    ])

    # Cross-validation per avere una stima dell'accuracy
    if len(np.unique(y)) == 2 and len(y) >= 10:
        cv = StratifiedKFold(n_splits=min(5, len(y) // 4), shuffle=True, random_state=42)
        scores = cross_val_score(clf, X, y, cv=cv, scoring="f1")
        print(f"  CV F1: {scores.mean():.3f} ± {scores.std():.3f}")

    clf.fit(X, y)
    return clf


# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not POS_DIR.exists() or not NEG_DIR.exists():
        sys.exit("❌  Esegui prima: python scripts/generate_wake_samples.py")

    print("📂  Carico dataset …")
    X, y = load_dataset()
    n_pos = int(y.sum())
    n_neg = int((y == 0).sum())
    print(f"    {n_pos} positivi, {n_neg} negativi — {len(X)} campioni totali")

    if len(X) < 10:
        sys.exit("❌  Dataset troppo piccolo. Aggiungi più campioni.")

    print("🏋️  Training …")
    model = train(X, y)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "wb") as f:
        pickle.dump(model, f)

    print(f"✅  Modello salvato: {OUT_PATH}")
    print("\nProssimo step:")
    print("  Riavvia il server — wake_oww.py caricherà il modello automaticamente.")
