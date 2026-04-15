"""
Script di setup da eseguire una volta: python scripts/download_fonts.py
Scarica i font Google necessari per il Design Agent.
"""
import urllib.request
from pathlib import Path

FONTS_DIR = Path("apps/backend/assets/fonts")
FONTS_DIR.mkdir(parents=True, exist_ok=True)

FONT_URLS = {
    # Playfair Display
    "PlayfairDisplay-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "PlayfairDisplay-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    # Lato
    "Lato-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/lato/Lato-Regular.ttf",
    "Lato-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/lato/Lato-Bold.ttf",
    # Raleway
    "Raleway-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/raleway/Raleway%5Bwght%5D.ttf",
    "Raleway-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/raleway/Raleway%5Bwght%5D.ttf",
    # Josefin Sans
    "JosefinSans-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/josefinsans/JosefinSans%5Bwght%5D.ttf",
    "JosefinSans-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/josefinsans/JosefinSans%5Bwght%5D.ttf",
}

for filename, url in FONT_URLS.items():
    dest = FONTS_DIR / filename
    if dest.exists():
        print(f"  ✓ {filename} già presente")
        continue
    print(f"  ↓ Download {filename}...")
    try:
        urllib.request.urlretrieve(url, dest)
        print(f"  ✓ {filename} scaricato")
    except Exception as e:
        print(f"  ✗ Errore {filename}: {e}")

print("Font setup completato.")
