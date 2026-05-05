from __future__ import annotations

TARGET_QUEUE_DEPTH  = 1    # max item in (pending_design + pending_approval) insieme
LOOP_SLEEP_NORMAL   = 30   # secondi tra iterazioni normali
LOOP_SLEEP_PAUSED   = 60   # secondi in paused_skip / paused_manual
LOOP_SLEEP_BUDGET   = 300  # secondi in paused_budget
LOOP_SLEEP_NIGHT    = 300  # secondi fuori finestra
LOOP_SLEEP_QUOTA    = 60   # secondi in paused_quota (controlla resume)
LOOP_SLEEP_EMPTY    = 300  # secondi senza niche disponibili

APPROVAL_TIMEOUT    = 86400  # 24h attesa risposta utente
APPROVAL_POLL       = 30.0   # secondi per poll asyncio
