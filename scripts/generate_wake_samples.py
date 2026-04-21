"""Genera campioni audio per il training del wake word detector "Ei Pepe".

Produce:
  training_data/positive/  — varianti di "Ei Pepe" via ElevenLabs
  training_data/negative/  — frasi simili che NON devono triggerare

Ogni file viene convertito in WAV 16kHz mono 16-bit via ffmpeg.

Uso:
  cd /Volumes/Progetti/AgentPeXI
  python scripts/generate_wake_samples.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

# ── Config ─────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = ROOT / "training_data"
POS_DIR = TRAINING_DIR / "positive"
NEG_DIR = TRAINING_DIR / "negative"

# Carica .env manualmente (senza dipendenze extra)
_env_path = ROOT / ".env"
_env: dict[str, str] = {}
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            _env[k.strip()] = v.strip().strip('"').strip("'")

ELEVENLABS_API_KEY  = _env.get("ELEVENLABS_API_KEY", os.environ.get("ELEVENLABS_API_KEY", ""))
ELEVENLABS_VOICE_ID = _env.get("ELEVENLABS_VOICE_ID", os.environ.get("ELEVENLABS_VOICE_ID", ""))

if not ELEVENLABS_API_KEY:
    sys.exit("❌  ELEVENLABS_API_KEY non trovata in .env")

# ── Testi ──────────────────────────────────────────────────────────────────

# Varianti positive: tutte le trascrizioni che Whisper produce per "Ei Pepe"
POSITIVE_TEXTS = [
    "Ei Pepe",
    "Ehi Pepe",
    "Hey Pepe",
    "Hei Pepe",
    "Ei, Pepe",
    "Ehi, Pepe",
    "Hey, Pepe",
]

# Varianti negative: frasi simili che NON devono attivare il wake word
NEGATIVE_TEXTS = [
    "Ei Beppe",
    "Ehi Beppe",
    "Ciao Pepe",
    "Ok Pepe",
    "Grazie Pepe",
    "Ei Mario",
    "Ei Luca",
    "Pepe",
    "Ei",
    "Ehi",
    "Hey",
    "Ei bella",
    "Dai Pepe",
    "Aspetta Pepe",
    "Senti Pepe",
]

# Parametri voce per variare il training set
VOICE_CONFIGS = [
    {"stability": 0.30, "similarity_boost": 0.75, "style": 0.0},
    {"stability": 0.50, "similarity_boost": 0.75, "style": 0.0},
    {"stability": 0.70, "similarity_boost": 0.75, "style": 0.0},
    {"stability": 0.50, "similarity_boost": 0.50, "style": 0.0},
    {"stability": 0.50, "similarity_boost": 0.90, "style": 0.0},
    {"stability": 0.40, "similarity_boost": 0.80, "style": 0.2},
]

# Voci ElevenLabs da usare (aggiungi ID se vuoi più varietà)
# La prima è quella configurata in .env, le altre sono voci pre-made pubbliche
VOICE_IDS = [
    ELEVENLABS_VOICE_ID,
    "21m00Tcm4TlvDq8ikWAM",  # Rachel (voce femminile)
    "AZnzlk1XvdvUeBnXmlld",  # Domi  (voce maschile, accento leggero)
]
# Rimuove duplicati e vuoti
VOICE_IDS = list(dict.fromkeys(v for v in VOICE_IDS if v))

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


# ── Helpers ────────────────────────────────────────────────────────────────

def generate_mp3(text: str, voice_id: str, voice_cfg: dict) -> bytes | None:
    """Chiama ElevenLabs e restituisce bytes MP3."""
    url = TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {**voice_cfg, "use_speaker_boost": True},
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            print(f"  ⚠ ElevenLabs {resp.status_code} per '{text}' — salto")
            return None
        return resp.content
    except Exception as exc:
        print(f"  ⚠ Errore rete per '{text}': {exc} — salto")
        return None


def mp3_to_wav(mp3_bytes: bytes, out_path: Path) -> bool:
    """Converte MP3 bytes in WAV 16kHz mono 16-bit tramite ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(mp3_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", tmp_path,
                "-ar", "16000",
                "-ac", "1",
                "-acodec", "pcm_s16le",
                str(out_path),
            ],
            capture_output=True,
        )
        return result.returncode == 0
    finally:
        os.unlink(tmp_path)


def generate_set(texts: list[str], out_dir: Path, label: str) -> int:
    """Genera campioni per un set di testi. Ritorna numero file creati."""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    total = len(texts) * len(VOICE_IDS) * len(VOICE_CONFIGS)
    print(f"\n{'─'*50}")
    print(f"  {label}: {total} campioni previsti")
    print(f"{'─'*50}")

    for voice_id in VOICE_IDS:
        for cfg_idx, cfg in enumerate(VOICE_CONFIGS):
            for text in texts:
                slug = (
                    text.lower()
                    .replace(",", "")
                    .replace(" ", "_")
                )
                fname = f"{slug}_v{voice_id[:8]}_c{cfg_idx}.wav"
                out_path = out_dir / fname

                if out_path.exists():
                    print(f"  ↩ già esistente: {fname}")
                    count += 1
                    continue

                print(f"  🎙 '{text}' | voice={voice_id[:8]} | stab={cfg['stability']:.1f}", end=" … ", flush=True)
                mp3 = generate_mp3(text, voice_id, cfg)
                if mp3 is None:
                    continue
                if mp3_to_wav(mp3, out_path):
                    print(f"✅ {out_path.name}")
                    count += 1
                else:
                    print("❌ ffmpeg fallito")

                time.sleep(0.3)  # anti rate-limit

    return count


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🎤  Generazione campioni wake word — Ei Pepe")
    print(f"    Voci: {len(VOICE_IDS)} | Config: {len(VOICE_CONFIGS)} | Testi: {len(POSITIVE_TEXTS)}+{len(NEGATIVE_TEXTS)}")

    pos = generate_set(POSITIVE_TEXTS, POS_DIR, "POSITIVI")
    neg = generate_set(NEGATIVE_TEXTS, NEG_DIR, "NEGATIVI")

    print(f"\n✅  Completato — {pos} positivi, {neg} negativi")
    print(f"    Directory: {TRAINING_DIR}")
    print("\nProssimo step:")
    print("  python scripts/train_wake_word.py")
