"""PublisherAgent — module-level constants."""
from __future__ import annotations

TAXONOMY_IDS = {
    "printable_pdf": 2078,      # Prints > Digital Prints
    "digital_art_png": 2078,
    # svg_bundle: il taxonomy ID reale deve essere configurato.
    # Usa l'endpoint GET /v3/application/seller-taxonomy/nodes per trovarlo.
    # Lasciare a 0 blocca intenzionalmente la pubblicazione fino alla configurazione.
    "svg_bundle": 0,
}

AB_PRICES = {
    "printable_pdf": {"A": 2.99, "B": 4.99},
    "digital_art_png": {"A": 3.99, "B": 6.99},
    "svg_bundle": {"A": 5.99, "B": 9.99},
}
