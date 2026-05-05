"""MarketDataAgent — costanti e soglie."""
from __future__ import annotations

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
ETSY_AUTOCOMPLETE_URL = (
    "https://www.etsy.com/api/v3/ajax/bespoke/public/fetch/listings/search/suggestions"
)

# Stagionalità per niche keyword — fonte di verità per tutto il sistema.
# Boost moltiplicativo applicato a entry_score: 1.0 = neutro, 1.3 = picco stagionale.
SEASONAL_MAP: dict[str, dict[int, float]] = {
    "wedding":      {3: 1.2, 4: 1.3, 5: 1.3, 6: 1.2, 9: 1.1, 10: 1.1},
    "christmas":    {10: 1.1, 11: 1.3, 12: 1.3},
    "valentine":    {12: 1.1, 1: 1.3, 2: 1.3},
    "halloween":    {8: 1.1, 9: 1.2, 10: 1.3},
    "thanksgiving": {10: 1.2, 11: 1.3},
    "easter":       {2: 1.1, 3: 1.3, 4: 1.2},
    "mother":       {4: 1.2, 5: 1.3},
    "father":       {5: 1.1, 6: 1.3},
    "graduation":   {4: 1.1, 5: 1.3, 6: 1.2},
    "birthday":     {},   # sempre stabile — nessun boost
    "baby":         {},   # stabile
    "planner":      {12: 1.1, 1: 1.3},   # inizio anno
    "resume":       {1: 1.2, 8: 1.1, 9: 1.2},
    "back to school": {7: 1.2, 8: 1.3},
    "new year":     {12: 1.2, 1: 1.2},
}

_HTTP_TIMEOUT = 15.0

# Soglie per normalizzazione entry_score
_MAX_RESULT_COUNT = 50_000   # oltre → saturazione massima
_MAX_AVG_REVIEWS  = 200      # oltre → domanda massima

# Peso Google Trends nel blending demand Tier 2
_TRENDS_WEIGHT = 0.35

# Competition bonus — moltiplicatore entry_score basato sul numero di risultati Etsy
_COMPETITION_TOO_SMALL   = 2_000
_COMPETITION_SWEET_LOW   = 10_000
_COMPETITION_NORMAL_HIGH = 50_000

_BONUS_SWEET_SPOT = 1.25
_BONUS_NORMAL     = 1.0
_BONUS_TOO_SMALL  = 1.0
_BONUS_CROWDED    = 0.9
