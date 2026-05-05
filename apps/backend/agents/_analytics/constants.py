"""AnalyticsAgent — soglie diagnostiche e costanti."""

VIEWS_MIN_7DAYS            = 30     # views minime dopo 7+ giorni live
CTR_MIN                    = 0.02   # 2% — sotto: thumbnail non converte
CONV_MIN                   = 0.01   # 1% su clicks — sotto: listing non converte
MIN_DAYS_LIVE              = 7      # non diagnosticare listing < 7 giorni
REMEDIATION_COOLDOWN_HOURS = 48     # evita notifiche ripetute per lo stesso problema
